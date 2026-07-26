"""Validate the documented AmtHero24 environment-variable contract.

The validator is network-free. It compares literal runtime environment accesses in
tracked Python source with `.env.example`, rejects committed example secrets, and emits
only variable names and source locations—never runtime values.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

_KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")
# These helpers accept an environment-variable name as their first argument. Keeping
# this list explicit avoids treating arbitrary uppercase business constants as runtime
# configuration while still following the project's typed/dynamic environment helpers.
_WRAPPER_PATTERN = re.compile(
    r"^(?:required_env|get_env|env_value|env_bool|env_int|env_float|env_str|"
    r"_env_value|_env_bool|_env_int|_env_float|_env_str|_environment_value|"
    r"_flag|_int_env|_limit|assess_secret)$"
)
_EXTERNAL_KEYS = {
    "PORT",  # injected and consumed by the Railway start command
}
_EXCLUDED_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache"}


@dataclass(frozen=True)
class EnvironmentFinding:
    rule: str
    variable: str
    file: str
    line: int
    message: str


@dataclass(frozen=True)
class EnvironmentUse:
    variable: str
    file: str
    line: int


def _tracked_python_files(root: Path) -> list[Path]:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "*.py"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        return [root / item.decode("utf-8") for item in result.stdout.split(b"\0") if item]
    except (OSError, subprocess.CalledProcessError, UnicodeDecodeError):
        return [
            path
            for path in root.rglob("*.py")
            if not any(part in _EXCLUDED_DIRS for part in path.parts)
        ]


def parse_env_example(path: Path) -> tuple[dict[str, tuple[str, int]], list[EnvironmentFinding]]:
    documented: dict[str, tuple[str, int]] = {}
    findings: list[EnvironmentFinding] = []
    if not path.is_file():
        return {}, [
            EnvironmentFinding("env-example-missing", "", path.as_posix(), 0, ".env.example is missing")
        ]

    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in raw:
            findings.append(
                EnvironmentFinding(
                    "invalid-env-line",
                    "",
                    path.as_posix(),
                    line_number,
                    "non-comment lines must use KEY=VALUE format",
                )
            )
            continue
        name, value = raw.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not _KEY_PATTERN.fullmatch(name):
            findings.append(
                EnvironmentFinding(
                    "invalid-env-name",
                    name,
                    path.as_posix(),
                    line_number,
                    "environment variable name is invalid",
                )
            )
            continue
        if name in documented:
            findings.append(
                EnvironmentFinding(
                    "duplicate-env-name",
                    name,
                    path.as_posix(),
                    line_number,
                    f"variable duplicates line {documented[name][1]}",
                )
            )
            continue
        documented[name] = (value, line_number)
        if _is_sensitive(name) and value:
            findings.append(
                EnvironmentFinding(
                    "example-secret-value",
                    name,
                    path.as_posix(),
                    line_number,
                    "sensitive values must remain empty in .env.example",
                )
            )
    return documented, findings


def _is_sensitive(name: str) -> bool:
    if name in {"DATABASE_URL", "GROQ_API_KEY", "WHATSAPP_TOKEN", "META_APP_SECRET"}:
        return True
    return name.endswith(("_KEY", "_TOKEN", "_SECRET", "_PASSWORD"))


def _literal_env_name(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str) and _KEY_PATTERN.fullmatch(node.value):
        return node.value
    return None


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _is_os_environ(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "environ"
        and isinstance(node.value, ast.Name)
        and node.value.id == "os"
    )


class _EnvironmentVisitor(ast.NodeVisitor):
    def __init__(self, path: Path, root: Path) -> None:
        self.path = path
        self.root = root
        self.uses: list[EnvironmentUse] = []

    def _record(self, variable: str | None, node: ast.AST) -> None:
        if not variable:
            return
        self.uses.append(
            EnvironmentUse(variable, self.path.relative_to(self.root).as_posix(), getattr(node, "lineno", 0))
        )

    def visit_Call(self, node: ast.Call) -> None:
        variable: str | None = None
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "getenv"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "os"
        ):
            variable = _literal_env_name(node.args[0] if node.args else None)
        elif (
            isinstance(node.func, ast.Attribute)
            and node.func.attr in {"get", "setdefault", "pop"}
            and _is_os_environ(node.func.value)
        ):
            variable = _literal_env_name(node.args[0] if node.args else None)
        elif _WRAPPER_PATTERN.fullmatch(_call_name(node.func)):
            variable = _literal_env_name(node.args[0] if node.args else None)
        self._record(variable, node)
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if _is_os_environ(node.value):
            self._record(_literal_env_name(node.slice), node)
        self.generic_visit(node)


def collect_environment_uses(root: Path, files: Iterable[Path] | None = None) -> tuple[list[EnvironmentUse], list[EnvironmentFinding]]:
    uses: list[EnvironmentUse] = []
    findings: list[EnvironmentFinding] = []
    resolved = root.resolve()
    candidates = list(files) if files is not None else _tracked_python_files(resolved)
    for candidate in candidates:
        path = candidate if candidate.is_absolute() else resolved / candidate
        try:
            relative = path.relative_to(resolved)
        except ValueError:
            relative = path
        if relative.parts and relative.parts[0] == "tests":
            continue
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=path.as_posix())
        except (OSError, UnicodeDecodeError, SyntaxError) as exc:
            findings.append(
                EnvironmentFinding(
                    "source-scan-error",
                    "",
                    relative.as_posix(),
                    getattr(exc, "lineno", 0) or 0,
                    f"could not inspect Python source: {type(exc).__name__}",
                )
            )
            continue
        visitor = _EnvironmentVisitor(path, resolved)
        visitor.visit(tree)
        uses.extend(visitor.uses)
    unique = {(item.variable, item.file, item.line): item for item in uses}
    return sorted(unique.values(), key=lambda item: (item.variable, item.file, item.line)), findings


def validate_environment_contract(root: Path) -> tuple[list[EnvironmentFinding], list[EnvironmentUse], dict[str, tuple[str, int]]]:
    resolved = root.resolve()
    documented, findings = parse_env_example(resolved / ".env.example")
    uses, scan_findings = collect_environment_uses(resolved)
    findings.extend(scan_findings)

    used_names = {item.variable for item in uses}
    documented_names = set(documented)
    first_use = {name: next(item for item in uses if item.variable == name) for name in sorted(used_names)}

    for name in sorted(used_names - documented_names):
        use = first_use[name]
        findings.append(
            EnvironmentFinding(
                "undocumented-runtime-variable",
                name,
                use.file,
                use.line,
                "runtime variable is used by code but missing from .env.example",
            )
        )

    for name in sorted(documented_names - used_names - _EXTERNAL_KEYS):
        _, line = documented[name]
        findings.append(
            EnvironmentFinding(
                "unused-documented-variable",
                name,
                ".env.example",
                line,
                "documented variable is not referenced by tracked runtime Python source",
            )
        )

    return sorted(findings, key=lambda item: (item.rule, item.variable, item.file, item.line)), uses, documented


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the documented environment-variable contract.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    findings, uses, documented = validate_environment_contract(args.root)
    report = {
        "status": "pass" if not findings else "fail",
        "finding_count": len(findings),
        "documented_count": len(documented),
        "runtime_variable_count": len({item.variable for item in uses}),
        "findings": [asdict(item) for item in findings],
        "runtime_variables": sorted({item.variable for item in uses}),
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    elif findings:
        for item in findings:
            print(f"{item.file}:{item.line}: {item.rule}: {item.variable}: {item.message}")
    else:
        print(f"Environment contract passed ({report['runtime_variable_count']} runtime variables).")
    return 0 if not findings else 1


if __name__ == "__main__":
    sys.exit(main())
