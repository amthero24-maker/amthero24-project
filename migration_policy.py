"""Static and runtime policy for online-safe startup migrations.

The policy inspects migration source and SQL structure only. Reports contain rule names,
source locations, and generic messages; they never contain SQL text, database values,
credentials, user data, or connection details.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PolicyFinding:
    rule: str
    file: str
    line: int
    message: str


@dataclass(frozen=True)
class SqlAssessment:
    safe: bool
    rule: str


_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT = re.compile(r"--[^\n]*")
_WHITESPACE = re.compile(r"\s+")
_IDENTIFIER = r'(?:"[^"]+"|[A-Za-z_][A-Za-z0-9_]*)'

_FORBIDDEN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("destructive-drop", re.compile(r"\bDROP\b")),
    ("destructive-rename", re.compile(r"\bRENAME\b")),
    ("destructive-truncate", re.compile(r"\bTRUNCATE\b")),
    ("data-delete", re.compile(r"\bDELETE\s+FROM\b")),
    ("data-update", re.compile(r"\bUPDATE\b")),
    ("data-insert", re.compile(r"\bINSERT\s+INTO\b")),
    ("alter-column", re.compile(r"\bALTER\s+COLUMN\b")),
    ("set-not-null", re.compile(r"\bSET\s+NOT\s+NULL\b")),
    ("type-change", re.compile(r"\bTYPE\b")),
    ("unique-index", re.compile(r"\bCREATE\s+UNIQUE\s+INDEX\b")),
    ("explicit-lock", re.compile(r"\bLOCK\s+TABLE\b")),
    ("maintenance-command", re.compile(r"\b(?:VACUUM|REINDEX|CLUSTER)\b")),
)

_CREATE_TABLE = re.compile(rf"^CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+{_IDENTIFIER}\b")
_CREATE_INDEX = re.compile(rf"^CREATE\s+INDEX\s+IF\s+NOT\s+EXISTS\s+{_IDENTIFIER}\s+ON\s+{_IDENTIFIER}\b")
_ADD_COLUMN = re.compile(rf"^ALTER\s+TABLE\s+{_IDENTIFIER}\s+ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\s+{_IDENTIFIER}\b")
_UNSAFE_ADD_COLUMN = re.compile(r"\b(?:NOT\s+NULL|UNIQUE|REFERENCES|GENERATED|IDENTITY)\b")


def _normalized_sql(statement: str) -> str:
    without_comments = _LINE_COMMENT.sub(" ", _BLOCK_COMMENT.sub(" ", str(statement or "")))
    return _WHITESPACE.sub(" ", without_comments).strip().rstrip(";").strip().upper()


def assess_online_sql(statement: str) -> SqlAssessment:
    """Classify one startup-migration statement without returning its content."""
    normalized = _normalized_sql(statement)
    if not normalized:
        return SqlAssessment(False, "empty-sql")
    if ";" in normalized:
        return SqlAssessment(False, "multiple-statements")
    for rule, pattern in _FORBIDDEN_PATTERNS:
        if pattern.search(normalized):
            return SqlAssessment(False, rule)
    if _CREATE_TABLE.match(normalized):
        return SqlAssessment(True, "create-table")
    if _CREATE_INDEX.match(normalized):
        return SqlAssessment(True, "create-index")
    if _ADD_COLUMN.match(normalized):
        if _UNSAFE_ADD_COLUMN.search(normalized):
            return SqlAssessment(False, "unsafe-add-column")
        return SqlAssessment(True, "add-nullable-column")
    return SqlAssessment(False, "unsupported-online-ddl")


def require_online_safe_sql(statement: str) -> None:
    assessment = assess_online_sql(statement)
    if not assessment.safe:
        raise ValueError(assessment.rule)


def _literal_string(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _literal_string(node.left)
        right = _literal_string(node.right)
        return left + right if left is not None and right is not None else None
    if isinstance(node, ast.JoinedStr):
        pieces: list[str] = []
        for value in node.values:
            if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
                return None
            pieces.append(value.value)
        return "".join(pieces)
    return None


def _literal_value(node: ast.AST | None, constants: dict[str, Any]) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    return None


def _constants(tree: ast.Module) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for statement in tree.body:
        if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
            continue
        targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
        value_node = statement.value
        value = _literal_value(value_node, values)
        if value is None:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                values[target.id] = value
    return values


def _migration_calls(tree: ast.Module) -> list[ast.Call]:
    for statement in tree.body:
        target: ast.AST | None = None
        value: ast.AST | None = None
        if isinstance(statement, ast.Assign):
            target = statement.targets[0] if statement.targets else None
            value = statement.value
        elif isinstance(statement, ast.AnnAssign):
            target = statement.target
            value = statement.value
        if not isinstance(target, ast.Name) or target.id != "_MIGRATIONS":
            continue
        if not isinstance(value, (ast.Tuple, ast.List)):
            return []
        return [item for item in value.elts if isinstance(item, ast.Call)]
    return []


def _call_argument(call: ast.Call, position: int, keyword: str) -> ast.AST | None:
    for item in call.keywords:
        if item.arg == keyword:
            return item.value
    return call.args[position] if len(call.args) > position else None


def _function_map(tree: ast.Module) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    return {
        item.name: item
        for item in tree.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def validate_migration_source(path: str | Path) -> list[PolicyFinding]:
    source_path = Path(path)
    try:
        source = source_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=source_path.as_posix())
    except (OSError, UnicodeError, SyntaxError):
        return [PolicyFinding("source-unavailable", source_path.as_posix(), 0, "migration source is unavailable or invalid")]

    constants = _constants(tree)
    functions = _function_map(tree)
    calls = _migration_calls(tree)
    findings: list[PolicyFinding] = []
    if not calls:
        return [PolicyFinding("migration-registry-missing", source_path.as_posix(), 0, "ordered migration registry is missing")]

    versions: list[int] = []
    names: list[str] = []
    referenced_functions: set[str] = set()

    for call in calls:
        if not isinstance(call.func, ast.Name) or call.func.id != "MigrationSpec":
            findings.append(PolicyFinding("invalid-migration-entry", source_path.as_posix(), call.lineno, "registry entries must use MigrationSpec"))
            continue
        version = _literal_value(_call_argument(call, 0, "version"), constants)
        name = _literal_value(_call_argument(call, 1, "name"), constants)
        apply_node = _call_argument(call, 3, "apply")
        phase = _literal_value(_call_argument(call, 4, "phase"), constants)
        legacy = _literal_value(_call_argument(call, 5, "legacy_bootstrap"), constants)

        if not isinstance(version, int) or version < 1:
            findings.append(PolicyFinding("invalid-version", source_path.as_posix(), call.lineno, "migration version must be a positive integer"))
        else:
            versions.append(version)
        if not isinstance(name, str) or not name.strip():
            findings.append(PolicyFinding("invalid-name", source_path.as_posix(), call.lineno, "migration name must be a non-empty constant"))
        else:
            names.append(name)
        if phase != "expand":
            findings.append(PolicyFinding("non-expand-phase", source_path.as_posix(), call.lineno, "startup migrations must use the expand phase"))
        if legacy is True and version != 1:
            findings.append(PolicyFinding("legacy-bootstrap-version", source_path.as_posix(), call.lineno, "legacy bootstrap is permitted only for migration version 1"))
        if not isinstance(apply_node, ast.Name):
            findings.append(PolicyFinding("dynamic-apply-function", source_path.as_posix(), call.lineno, "migration apply function must be a direct named function"))
            continue
        referenced_functions.add(apply_node.id)
        function = functions.get(apply_node.id)
        if function is None:
            findings.append(PolicyFinding("missing-apply-function", source_path.as_posix(), call.lineno, "migration apply function is missing"))
            continue

        for node in ast.walk(function):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute) or node.func.attr != "execute":
                continue
            receiver = node.func.value
            if not isinstance(receiver, ast.Name) or receiver.id != "executor":
                findings.append(PolicyFinding("raw-sql-execution", source_path.as_posix(), node.lineno, "migration SQL must use the online-safe executor"))
                continue
            statement = _literal_string(node.args[0] if node.args else None)
            if statement is None:
                findings.append(PolicyFinding("dynamic-sql", source_path.as_posix(), node.lineno, "migration SQL must be a literal statement"))
                continue
            assessment = assess_online_sql(statement)
            if not assessment.safe:
                findings.append(PolicyFinding(assessment.rule, source_path.as_posix(), node.lineno, "migration contains SQL that is unsafe during rolling deployment"))

    if versions:
        expected = list(range(1, max(versions) + 1))
        if sorted(versions) != expected:
            findings.append(PolicyFinding("version-sequence", source_path.as_posix(), 0, "migration versions must be unique and consecutive from 1"))
    if len(names) != len(set(names)):
        findings.append(PolicyFinding("duplicate-name", source_path.as_posix(), 0, "migration names must be unique"))

    return sorted(findings, key=lambda item: (item.line, item.rule, item.message))


def report_payload(findings: list[PolicyFinding]) -> dict[str, Any]:
    return {
        "status": "pass" if not findings else "fail",
        "finding_count": len(findings),
        "findings": [asdict(item) for item in findings],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate online-safe AmtHero24 startup migrations.")
    parser.add_argument("--source", type=Path, default=Path("database_migrations.py"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    payload = report_payload(validate_migration_source(args.source))
    if args.json:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    else:
        for finding in payload["findings"]:
            sys.stdout.write(f"{finding['file']}:{finding['line']}: {finding['rule']}: {finding['message']}\n")
    return 0 if payload["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
