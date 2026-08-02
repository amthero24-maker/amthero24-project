"""Non-mutating production smoke checks for AmtHero24.

The checker calls only read-only health and protected aggregate endpoints. It never
sends WhatsApp messages, creates users, or writes application data.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class SmokeCheck:
    name: str
    status: str
    detail: str

    @property
    def passed(self) -> bool:
        return self.status == "pass"


class SmokeError(RuntimeError):
    """Raised when an endpoint cannot be read safely."""


def _base_url(value: str) -> str:
    cleaned = str(value or "").strip()
    if not cleaned.startswith(("https://", "http://")):
        raise ValueError("base URL must start with https:// or http://")
    return cleaned.rstrip("/") + "/"


def _safe_content_type(value: str | None) -> str:
    """Return only a bounded media type suitable for sanitized incident reports."""
    media_type = str(value or "missing").split(";", 1)[0].strip().casefold()
    if not re.fullmatch(r"[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+", media_type):
        return "unknown"
    return media_type[:80]


def fetch_json(base_url: str, path: str, *, token: str = "", timeout: float = 15.0) -> tuple[int, dict[str, Any]]:
    """Fetch one JSON endpoint without logging credentials, response bodies, or sensitive headers."""
    url = urljoin(_base_url(base_url), path.lstrip("/"))
    headers = {"Accept": "application/json", "User-Agent": "AmtHero24-Smoke/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, headers=headers, method="GET")
    content_type = "missing"
    try:
        with urlopen(request, timeout=max(1.0, min(float(timeout), 60.0))) as response:
            status = int(response.status)
            content_type = _safe_content_type(response.headers.get("Content-Type"))
            raw = response.read(1_000_000)
    except HTTPError as exc:
        status = int(exc.code)
        content_type = _safe_content_type(exc.headers.get("Content-Type"))
        raw = exc.read(1_000_000)
    except (URLError, TimeoutError, OSError) as exc:
        raise SmokeError(f"endpoint unavailable: {type(exc).__name__}") from exc

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SmokeError(
            f"endpoint returned invalid JSON (HTTP {status}; content-type={content_type})"
        ) from exc
    if not isinstance(payload, dict):
        raise SmokeError(
            f"endpoint returned non-object JSON (HTTP {status}; content-type={content_type})"
        )
    return status, payload


def run_smoke(
    base_url: str,
    *,
    admin_token: str = "",
    expected_version: str = "",
    require_postgresql: bool = True,
    require_signature: bool = False,
    require_launch_ready: bool = False,
    timeout: float = 15.0,
) -> list[SmokeCheck]:
    """Run read-only production checks and return an ordered report."""
    checks: list[SmokeCheck] = []

    try:
        health_status, health = fetch_json(base_url, "/health", timeout=timeout)
        health_ok = health_status == 200 and health.get("status") == "ok"
        checks.append(SmokeCheck("health", "pass" if health_ok else "fail", f"HTTP {health_status}; status={health.get('status', 'missing')}"))
        if expected_version:
            actual = str(health.get("version") or "")
            checks.append(SmokeCheck("version", "pass" if actual == expected_version else "fail", f"expected={expected_version}; actual={actual or 'missing'}"))
    except SmokeError as exc:
        checks.append(SmokeCheck("health", "fail", str(exc)))
        return checks

    try:
        ready_status, ready = fetch_json(base_url, "/ready", timeout=timeout)
        components = ready.get("components") if isinstance(ready.get("components"), dict) else {}
        ready_ok = ready_status == 200 and ready.get("status") == "ready"
        checks.append(SmokeCheck("readiness", "pass" if ready_ok else "fail", f"HTTP {ready_status}; status={ready.get('status', 'missing')}"))

        backend = str(components.get("storage_backend") or "missing")
        backend_ok = backend == "postgresql" or not require_postgresql
        checks.append(SmokeCheck("storage_backend", "pass" if backend_ok else "fail", backend))

        schemas = str(components.get("postgresql_schemas") or "missing")
        schemas_ok = schemas == "initialized" or not require_postgresql
        checks.append(SmokeCheck("postgresql_schemas", "pass" if schemas_ok else "fail", schemas))

        migrations = str(components.get("database_schema_migrations") or "missing")
        migrations_ok = migrations == "current" or not require_postgresql
        checks.append(SmokeCheck("database_schema_migrations", "pass" if migrations_ok else "fail", migrations))

        schema_version = components.get("database_schema_version")
        version_ok = (isinstance(schema_version, int) and schema_version >= 1) or not require_postgresql
        checks.append(SmokeCheck("database_schema_version", "pass" if version_ok else "fail", str(schema_version if schema_version is not None else "missing")))

        fallback = str(components.get("database_fallback") or "missing")
        fallback_ok = fallback == "fail-closed" or not require_postgresql
        checks.append(SmokeCheck("database_fallback", "pass" if fallback_ok else "fail", fallback))

        process_lifecycle = str(components.get("process_lifecycle") or "missing")
        checks.append(SmokeCheck("process_lifecycle", "pass" if process_lifecycle == "accepting" else "fail", process_lifecycle))

        signature = str(components.get("webhook_signature") or "missing")
        signature_ok = signature == "enforced" or not require_signature
        checks.append(SmokeCheck("webhook_signature", "pass" if signature_ok else "fail", signature))

        idempotency = str(components.get("webhook_idempotency") or "missing")
        checks.append(SmokeCheck("webhook_idempotency", "pass" if idempotency == "retry-safe" else "fail", idempotency))

        durable_queue = str(components.get("durable_inbound_queue") or "missing")
        queue_ok = durable_queue in {"disabled", "configured"}
        checks.append(SmokeCheck("durable_inbound_queue", "pass" if queue_ok else "fail", durable_queue))

        delivery_receipts = str(components.get("outbound_delivery_receipts") or "missing")
        checks.append(SmokeCheck("outbound_delivery_receipts", "pass" if delivery_receipts == "enabled" else "fail", delivery_receipts))

        reminders = str(components.get("reminders") or "missing")
        checks.append(SmokeCheck("reminders", "pass" if reminders == "enabled" else "fail", reminders))
        reminder_encryption = str(components.get("reminder_encryption") or "missing")
        checks.append(SmokeCheck("reminder_encryption", "pass" if reminder_encryption == "configured" else "fail", reminder_encryption))

        if admin_token:
            admin_status = str(components.get("admin_overview") or "missing")
            checks.append(SmokeCheck("admin_secret", "pass" if admin_status == "configured" else "fail", admin_status))

        for component in ("privacy_retention", "provider_telemetry", "abuse_guard"):
            value = str(components.get(component) or "missing")
            okay = value not in {"missing", "disabled", "unavailable"}
            checks.append(SmokeCheck(component, "pass" if okay else "fail", value))
    except SmokeError as exc:
        checks.append(SmokeCheck("readiness", "fail", str(exc)))
        return checks

    if admin_token:
        try:
            launch_status, launch = fetch_json(base_url, "/admin/launch-readiness", token=admin_token, timeout=timeout)
            decision = str(launch.get("status") or "missing")
            endpoint_ok = launch_status == 200 and decision in {"ready", "warning", "blocked"}
            checks.append(SmokeCheck("launch_report_endpoint", "pass" if endpoint_ok else "fail", f"HTTP {launch_status}; status={decision}"))
            launch_ok = decision == "ready" or (decision == "warning" and not require_launch_ready)
            checks.append(SmokeCheck("launch_decision", "pass" if launch_ok else "fail", decision))
        except SmokeError as exc:
            checks.append(SmokeCheck("launch_report_endpoint", "fail", str(exc)))

    return checks


def _flag(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().casefold() in {"1", "true", "yes", "on"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run non-mutating AmtHero24 production smoke checks.")
    parser.add_argument("--base-url", default=os.getenv("PRODUCTION_BASE_URL", ""))
    parser.add_argument("--admin-token", default=os.getenv("ADMIN_API_TOKEN", ""))
    parser.add_argument("--expected-version", default=os.getenv("EXPECTED_APP_VERSION", ""))
    parser.add_argument("--timeout", type=float, default=float(os.getenv("SMOKE_TIMEOUT_SECONDS", "15")))
    parser.add_argument("--allow-json", action="store_true", help="Do not require PostgreSQL. Intended only for non-production environments.")
    parser.add_argument("--require-signature", action="store_true", default=_flag(os.getenv("SMOKE_REQUIRE_SIGNATURE"), False))
    parser.add_argument("--require-launch-ready", action="store_true", default=_flag(os.getenv("SMOKE_REQUIRE_LAUNCH_READY"), False))
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args(argv)

    if not str(args.base_url).strip():
        parser.error("--base-url or PRODUCTION_BASE_URL is required")

    try:
        checks = run_smoke(
            args.base_url,
            admin_token=str(args.admin_token or ""),
            expected_version=str(args.expected_version or ""),
            require_postgresql=not args.allow_json,
            require_signature=bool(args.require_signature),
            require_launch_ready=bool(args.require_launch_ready),
            timeout=args.timeout,
        )
    except ValueError as exc:
        parser.error(str(exc))

    if args.json:
        print(json.dumps({"checks": [asdict(item) for item in checks], "passed": all(item.passed for item in checks)}, ensure_ascii=False))
    else:
        for item in checks:
            marker = "PASS" if item.passed else "FAIL"
            print(f"[{marker}] {item.name}: {item.detail}")

    return 0 if checks and all(item.passed for item in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
