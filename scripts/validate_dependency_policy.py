"""Validate AmtHero24 dependency manifests and workflow supply-chain policy.

The policy is intentionally deterministic and network-free. It rejects dependency
sources that bypass PyPI resolution, unbounded direct dependencies, development tools
inside the production image, dangerous pull_request_target workflows, and floating
GitHub Action references.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

_REQUIREMENT_NAME = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.-]*)(?:\[[A-Za-z0-9_,.-]+\])?")
_ACTION_USE = re.compile(r"^\s*-?\s*uses:\s*([^\s#]+)", re.MULTILINE)
_FLOATING_ACTION_REFS = {"main", "master", "latest", "head", "develop", "development"}
_PRODUCTION_FORBIDDEN = {"pytest", "pip-audit", "pip_audit"}


@dataclass(frozen=True)
class PolicyFinding:
    file: str
    line: int
    rule: str
    message: str


def _normalized_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).casefold()


def _logical_lines(path: Path) -> Iterable[tuple[int, str]]:
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        content = raw.split("#", 1)[0].strip()
        if content:
            yield number, content


def validate_requirements(path: Path, *, production: bool, require_base_include: bool = False) -> list[PolicyFinding]:
    findings: list[PolicyFinding] = []
    seen: dict[str, int] = {}
    base_includes = 0

    if not path.is_file():
        return [PolicyFinding(str(path), 0, "manifest-missing", "required dependency manifest is missing")]

    for line_number, content in _logical_lines(path):
        lowered = content.casefold()
        if lowered.startswith(("-r ", "--requirement ")):
            target = content.split(maxsplit=1)[1].strip() if " " in content else ""
            if production:
                findings.append(
                    PolicyFinding(str(path), line_number, "production-include", "production requirements must not include another manifest")
                )
            elif target != "requirements.txt":
                findings.append(
                    PolicyFinding(str(path), line_number, "unexpected-include", "only '-r requirements.txt' is allowed")
                )
            else:
                base_includes += 1
            continue

        if content.startswith("-"):
            findings.append(
                PolicyFinding(str(path), line_number, "installer-option", "installer flags and editable dependencies are not allowed in manifests")
            )
            continue

        if "://" in content or " @ " in content or lowered.startswith(("git+", "hg+", "svn+", "bzr+", "file:")):
            findings.append(
                PolicyFinding(str(path), line_number, "external-source", "direct URLs, VCS references, and local dependency sources are not allowed")
            )
            continue

        requirement = content.split(";", 1)[0].strip()
        match = _REQUIREMENT_NAME.match(requirement)
        if not match:
            findings.append(PolicyFinding(str(path), line_number, "invalid-requirement", "dependency requirement cannot be parsed"))
            continue

        package = _normalized_name(match.group(1))
        if package in seen:
            findings.append(
                PolicyFinding(str(path), line_number, "duplicate-dependency", f"dependency duplicates line {seen[package]}")
            )
        else:
            seen[package] = line_number

        if production and package in {_normalized_name(item) for item in _PRODUCTION_FORBIDDEN}:
            findings.append(
                PolicyFinding(str(path), line_number, "production-dev-tool", "development or audit tooling must not be installed in production")
            )

        if ">=" not in requirement or "<" not in requirement:
            findings.append(
                PolicyFinding(str(path), line_number, "bounded-range", "direct dependencies require both a minimum and an exclusive upper bound")
            )

    if require_base_include and base_includes != 1:
        findings.append(
            PolicyFinding(str(path), 0, "base-include", "manifest must include requirements.txt exactly once")
        )
    return findings


def validate_workflows(workflow_dir: Path) -> list[PolicyFinding]:
    findings: list[PolicyFinding] = []
    if not workflow_dir.is_dir():
        return [PolicyFinding(str(workflow_dir), 0, "workflow-dir-missing", "workflow directory is missing")]

    for path in sorted((*workflow_dir.glob("*.yml"), *workflow_dir.glob("*.yaml"))):
        content = path.read_text(encoding="utf-8")
        for number, line in enumerate(content.splitlines(), start=1):
            if re.match(r"^\s*pull_request_target\s*:", line):
                findings.append(
                    PolicyFinding(str(path), number, "pull-request-target", "pull_request_target is prohibited for this repository")
                )

        for match in _ACTION_USE.finditer(content):
            reference = match.group(1).strip("'\"")
            line_number = content.count("\n", 0, match.start()) + 1
            if reference.startswith("./"):
                continue
            if "@" not in reference:
                findings.append(
                    PolicyFinding(str(path), line_number, "action-unpinned", "external GitHub Actions require an explicit version or commit reference")
                )
                continue
            action, ref = reference.rsplit("@", 1)
            if not action or not ref:
                findings.append(
                    PolicyFinding(str(path), line_number, "action-invalid", "GitHub Action reference is incomplete")
                )
            elif ref.casefold() in _FLOATING_ACTION_REFS:
                findings.append(
                    PolicyFinding(str(path), line_number, "action-floating", f"floating GitHub Action ref '{ref}' is prohibited")
                )
    return findings


def run_policy(root: Path) -> list[PolicyFinding]:
    return [
        *validate_requirements(root / "requirements.txt", production=True),
        *validate_requirements(root / "requirements-dev.txt", production=False, require_base_include=True),
        *validate_requirements(root / "requirements-security.txt", production=False, require_base_include=True),
        *validate_workflows(root / ".github" / "workflows"),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate dependency and workflow supply-chain policy.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    findings = run_policy(args.root.resolve())
    report = {
        "status": "pass" if not findings else "fail",
        "finding_count": len(findings),
        "findings": [asdict(item) for item in findings],
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    elif findings:
        for item in findings:
            print(f"{item.file}:{item.line}: {item.rule}: {item.message}")
    else:
        print("Dependency and workflow supply-chain policy passed.")
    return 0 if not findings else 1


if __name__ == "__main__":
    sys.exit(main())
