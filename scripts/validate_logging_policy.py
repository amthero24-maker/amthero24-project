"""Reject runtime logging patterns likely to expose user or credential data.

This policy is network-free and reports only identifiers and source locations. Runtime
redaction remains the final safety boundary; this scanner prevents obviously unsafe
logging calls from being introduced in the first place.
"""
from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

_LOG_METHODS = {"debug", "info", "warning", "warn", "error", "exception", "critical", "log"}
_SAFE_WRAPPERS = {"redact_text", "redact_value", "safe_log_value", "sanitize_log_record"}
_SENSITIVE_PARTS = {
    "phone",
    "phone_number",
    "recipient",
    "wa_id",
    "message",
    "text",
    "body",
    "payload",
    "document",
    "content",
    "caption",
    "request",
    "response",
    "headers",
    "authorization",
    "cookie",
    "token",
    "secret",
    "password",
    "api_key",
    "app_secret",
    "ciphertext",
    "database_url",
    "media_bytes",
    "raw",
}
_EXCLUDED_DIRS = {"tests", "scripts", ".git", ".venv", "venv", "__pycache__", ".pytest_cache"}
_EXCLUDED_FILES = {"production_smoke.py"}  # explicit read-only operator CLI
_RUNTIME_REDACTED_EXTRA_KEYS = {"message_id"}


@dataclass(frozen=True)
class LoggingFinding:
    file: str
    line: int
    rule: str
    identifier: str
    message: str


def _tracked_runtime_files(root: Path) -> list[Path]:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "*.py"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        candidates = [root / item.decode("utf-8") for item in result.stdout.split(b"\0") if item]
    except (OSError, subprocess.CalledProcessError, UnicodeDecodeError):
        candidates = list(root.rglob("*.py"))
    return [
        path
        for path in candidates
        if path.relative_to(root).as_posix() not in _EXCLUDED_FILES
        and not any(part in _EXCLUDED_DIRS for part in path.relative_to(root).parts)
    ]


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _identifier_parts(node: ast.AST) -> set[str]:
    parts: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            parts.add(child.id.casefold())
        elif isinstance(child, ast.Attribute):
            parts.add(child.attr.casefold())
        elif isinstance(child, ast.Constant) and isinstance(child.value, str):
            value = child.value.strip().casefold()
            if value and len(value) <= 80:
                parts.add(value)
    return parts


def _is_runtime_redacted_extra(node: ast.AST) -> bool:
    """Allow only mappings whose keys are guaranteed redacted by ``log_safety``."""
    if not isinstance(node, ast.Dict) or not node.keys:
        return False
    keys: set[str] = set()
    for key in node.keys:
        if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
            return False
        keys.add(key.value.strip().casefold().replace("-", "_"))
    return bool(keys) and keys.issubset(_RUNTIME_REDACTED_EXTRA_KEYS)


def _sensitive_identifier(node: ast.AST) -> str:
    if isinstance(node, ast.Call) and _call_name(node.func) in _SAFE_WRAPPERS:
        return ""
    if _is_runtime_redacted_extra(node):
        return ""
    for part in sorted(_identifier_parts(node)):
        normalized = part.replace("-", "_")
        if normalized in _SENSITIVE_PARTS:
            return normalized
        if any(
            normalized.endswith(suffix)
            for suffix in (
                "_phone",
                "_phone_number",
                "_recipient",
                "_message",
                "_text",
                "_body",
                "_payload",
                "_document",
                "_headers",
                "_token",
                "_secret",
                "_password",
                "_api_key",
                "_ciphertext",
            )
        ):
            return normalized
    return ""


def _is_logging_call(node: ast.Call) -> bool:
    return isinstance(node.func, ast.Attribute) and node.func.attr in _LOG_METHODS


def _data_arguments(node: ast.Call) -> list[ast.AST]:
    if not node.args:
        return []
    start = 2 if isinstance(node.func, ast.Attribute) and node.func.attr == "log" else 1
    values = list(node.args[start:])
    for keyword in node.keywords:
        if keyword.arg in {"extra"}:
            values.append(keyword.value)
    # F-strings and string concatenation place dynamic values inside the message itself.
    first = node.args[1] if start == 2 and len(node.args) > 1 else node.args[0]
    if isinstance(first, (ast.JoinedStr, ast.BinOp, ast.Call)):
        values.append(first)
    return values


class _LoggingVisitor(ast.NodeVisitor):
    def __init__(self, path: Path, root: Path) -> None:
        self.path = path
        self.root = root
        self.findings: list[LoggingFinding] = []

    def _add(self, node: ast.AST, rule: str, identifier: str, message: str) -> None:
        self.findings.append(
            LoggingFinding(
                self.path.relative_to(self.root).as_posix(),
                getattr(node, "lineno", 0),
                rule,
                identifier,
                message,
            )
        )

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id == "print":
            self._add(node, "runtime-print", "print", "runtime service code must use structured logging")
        elif _is_logging_call(node):
            for argument in _data_arguments(node):
                identifier = _sensitive_identifier(argument)
                if identifier:
                    self._add(
                        argument,
                        "sensitive-log-argument",
                        identifier,
                        "sensitive data must not be passed directly to a logging call",
                    )
        self.generic_visit(node)


def validate_logging_policy(root: Path, files: Iterable[Path] | None = None) -> list[LoggingFinding]:
    resolved = root.resolve()
    candidates = list(files) if files is not None else _tracked_runtime_files(resolved)
    findings: list[LoggingFinding] = []
    for candidate in candidates:
        path = candidate if candidate.is_absolute() else resolved / candidate
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.as_posix())
        except (OSError, UnicodeDecodeError, SyntaxError) as exc:
            try:
                relative = path.relative_to(resolved).as_posix()
            except ValueError:
                relative = path.as_posix()
            findings.append(
                LoggingFinding(
                    relative,
                    getattr(exc, "lineno", 0) or 0,
                    "source-scan-error",
                    type(exc).__name__,
                    "runtime source could not be inspected",
                )
            )
            continue
        visitor = _LoggingVisitor(path, resolved)
        visitor.visit(tree)
        findings.extend(visitor.findings)
    return sorted(findings, key=lambda item: (item.file, item.line, item.rule, item.identifier))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate runtime logging privacy policy.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    findings = validate_logging_policy(args.root)
    report = {
        "status": "pass" if not findings else "fail",
        "finding_count": len(findings),
        "findings": [asdict(item) for item in findings],
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    elif findings:
        for item in findings:
            print(f"{item.file}:{item.line}: {item.rule}: {item.identifier}: {item.message}")
    else:
        print("Runtime logging privacy policy passed.")
    return 0 if not findings else 1


if __name__ == "__main__":
    sys.exit(main())
