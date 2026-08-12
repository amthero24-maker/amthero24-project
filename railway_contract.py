"""Deterministic, offline Railway deployment configuration validator.

The validator reads repository files only. It never contacts Railway, loads secrets, or
prints environment values. It protects the readiness path, restart policy, graceful
handoff window, explicit production ASGI entrypoint, and truthful stdout/stderr severity
routing from accidental configuration drift.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

_EXPECTED_SCHEMA = "https://railway.com/railway.schema.json"
_EXPECTED_HEALTH_PATH = "/ready"
_EXPECTED_LOG_CONFIG = "logging.railway.json"
_EXPECTED_ENTRYPOINT = (
    "uvicorn webhook_security:app --host 0.0.0.0 --port $PORT "
    f"--log-config {_EXPECTED_LOG_CONFIG}"
)


@dataclass(frozen=True)
class RailwayContractFinding:
    code: str
    passed: bool
    detail: str


def _integer(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _load_json(path: Path) -> tuple[dict[str, Any] | None, RailwayContractFinding | None]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, RailwayContractFinding(
            "railway_file", False, f"{path.name} is missing"
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None, RailwayContractFinding(
            "railway_file", False, f"{path.name} is unreadable or invalid"
        )
    if not isinstance(payload, dict):
        return None, RailwayContractFinding(
            "railway_file", False, f"{path.name} must contain an object"
        )
    return payload, None


def _entrypoint(deploy: dict[str, Any]) -> str:
    return str(deploy.get("startCommand") or "").strip()


def _logging_config_finding(root: Path) -> RailwayContractFinding:
    payload, error = _load_json(root / _EXPECTED_LOG_CONFIG)
    if error is not None or payload is None:
        return RailwayContractFinding(
            "logging_config",
            False,
            "Railway logging configuration is missing or invalid",
        )

    handlers = payload.get("handlers") if isinstance(payload.get("handlers"), dict) else {}
    filters = payload.get("filters") if isinstance(payload.get("filters"), dict) else {}
    loggers = payload.get("loggers") if isinstance(payload.get("loggers"), dict) else {}
    root_logger = payload.get("root") if isinstance(payload.get("root"), dict) else {}
    stdout = handlers.get("stdout") if isinstance(handlers.get("stdout"), dict) else {}
    stderr = handlers.get("stderr") if isinstance(handlers.get("stderr"), dict) else {}
    access_stdout = (
        handlers.get("access_stdout")
        if isinstance(handlers.get("access_stdout"), dict)
        else {}
    )
    max_info = filters.get("max_info") if isinstance(filters.get("max_info"), dict) else {}
    uvicorn = loggers.get("uvicorn") if isinstance(loggers.get("uvicorn"), dict) else {}
    uvicorn_access = (
        loggers.get("uvicorn.access")
        if isinstance(loggers.get("uvicorn.access"), dict)
        else {}
    )

    only_stream_handlers = bool(handlers) and all(
        isinstance(value, dict)
        and value.get("class") == "logging.StreamHandler"
        for value in handlers.values()
    )
    configured = all(
        (
            payload.get("version") == 1,
            payload.get("disable_existing_loggers") is False,
            only_stream_handlers,
            stdout.get("stream") == "ext://sys.stdout",
            stdout.get("filters") == ["max_info"],
            access_stdout.get("stream") == "ext://sys.stdout",
            access_stdout.get("filters") == ["max_info"],
            stderr.get("stream") == "ext://sys.stderr",
            str(stderr.get("level") or "").upper() == "WARNING",
            max_info.get("()") == "railway_logging.MaxLevelFilter",
            str(max_info.get("max_level") or "").upper() == "INFO",
            root_logger.get("handlers") == ["stdout", "stderr"],
            uvicorn.get("handlers") == ["stdout", "stderr"],
            uvicorn.get("propagate") is False,
            uvicorn_access.get("handlers") == ["access_stdout", "stderr"],
            uvicorn_access.get("propagate") is False,
        )
    )
    return RailwayContractFinding(
        "logging_config",
        configured,
        (
            "INFO routes to stdout and warnings/errors route to stderr"
            if configured
            else "Railway logging severity routing drifted"
        ),
    )


def validate_railway_contract(root: str | Path = ".") -> list[RailwayContractFinding]:
    base = Path(root)
    payload, error = _load_json(base / "railway.json")
    if error is not None:
        return [error]
    assert payload is not None
    deploy = payload.get("deploy") if isinstance(payload.get("deploy"), dict) else {}

    schema = str(payload.get("$schema") or "").strip()
    health_path = str(deploy.get("healthcheckPath") or "").strip()
    health_timeout = _integer(deploy.get("healthcheckTimeout"))
    restart_policy = str(deploy.get("restartPolicyType") or "").strip().upper()
    restart_retries = _integer(deploy.get("restartPolicyMaxRetries"))
    overlap = _integer(deploy.get("overlapSeconds"))
    draining = _integer(deploy.get("drainingSeconds"))
    entrypoint = _entrypoint(deploy)

    overlap_ok = overlap is not None and 15 <= overlap <= 120
    draining_ok = draining is not None and 5 <= draining <= 60
    handoff_ok = overlap_ok and draining_ok and draining <= overlap

    return [
        RailwayContractFinding(
            "schema",
            schema == _EXPECTED_SCHEMA,
            "official Railway JSON schema configured" if schema == _EXPECTED_SCHEMA else "official Railway JSON schema is missing",
        ),
        RailwayContractFinding(
            "healthcheck_path",
            health_path == _EXPECTED_HEALTH_PATH,
            health_path or "missing",
        ),
        RailwayContractFinding(
            "healthcheck_timeout",
            health_timeout is not None and 120 <= health_timeout <= 600,
            str(health_timeout) if health_timeout is not None else "missing or invalid",
        ),
        RailwayContractFinding(
            "restart_policy",
            restart_policy == "ON_FAILURE",
            restart_policy or "missing",
        ),
        RailwayContractFinding(
            "restart_retries",
            restart_retries == 10,
            str(restart_retries) if restart_retries is not None else "missing or invalid",
        ),
        RailwayContractFinding(
            "graceful_handoff",
            handoff_ok,
            f"overlap={overlap}; draining={draining}" if overlap is not None and draining is not None else "missing or invalid",
        ),
        RailwayContractFinding(
            "production_entrypoint",
            entrypoint == _EXPECTED_ENTRYPOINT,
            "secure ASGI entrypoint configured" if entrypoint == _EXPECTED_ENTRYPOINT else "production entrypoint drifted",
        ),
        _logging_config_finding(base),
    ]


def report_payload(findings: list[RailwayContractFinding]) -> dict[str, Any]:
    return {
        "passed": bool(findings) and all(item.passed for item in findings),
        "findings": [asdict(item) for item in findings],
    }


def write_report(payload: dict[str, Any], output: Path | None = None) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(encoded + "\n", encoding="utf-8")
    return encoded


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate AmtHero24's Railway deployment contract offline.")
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path, default=Path("railway-contract.json"))
    args = parser.parse_args(argv)
    payload = report_payload(validate_railway_contract(args.root))
    sys.stdout.write(write_report(payload, args.output) + "\n")
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
