"""Retry AmtHero24's read-only production smoke checks and emit a safe report.

The monitor never sends WhatsApp messages and never writes application data. Its JSON
output intentionally excludes the production URL, credentials, headers, and response
bodies. It is designed for scheduled GitHub Actions incident automation.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Sequence

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from production_smoke import SmokeCheck, SmokeError, fetch_json, run_smoke


@dataclass(frozen=True)
class MonitorReport:
    status: str
    generated_at: str
    attempts_configured: int
    attempts_run: int
    recovered_after_retry: bool
    checks: list[dict[str, str]]

    @property
    def healthy(self) -> bool:
        return self.status == "healthy"


def _bounded_attempts(value: int) -> int:
    return max(1, min(int(value), 5))


def _bounded_delay(value: float) -> float:
    return max(0.0, min(float(value), 300.0))


def _safe_failure(detail: str) -> list[SmokeCheck]:
    return [SmokeCheck("monitor_execution", "fail", detail[:240])]


def _safe_nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _safe_percent(value: Any) -> float:
    try:
        return max(0.0, min(100.0, float(value or 0.0)))
    except (TypeError, ValueError):
        return 0.0


def _safe_failure_code(value: Any) -> str:
    raw = str(value or "").strip()
    clean = "".join(character for character in raw if character.isalnum() or character in {"_", "-"})[:40]
    return clean or "unknown"


def _needs_outbound_diagnostics(checks: Sequence[SmokeCheck]) -> bool:
    return any(
        check.name == "launch_decision"
        and not check.passed
        and "outbound_delivery" in str(check.detail)
        for check in checks
    )


def _outbound_delivery_detail(payload: dict[str, Any]) -> str:
    """Return only aggregate delivery counts/ages/codes; never identifiers or content."""
    raw = payload.get("outbound_delivery")
    delivery = raw if isinstance(raw, dict) else {}
    raw_statuses = delivery.get("by_status")
    statuses = raw_statuses if isinstance(raw_statuses, dict) else {}
    ordered = ("accepted", "sent", "delivered", "read", "failed")
    encoded_statuses = ",".join(
        f"{name}:{_safe_nonnegative_int(statuses.get(name))}" for name in ordered
    )
    raw_codes = delivery.get("failure_codes")
    codes = raw_codes if isinstance(raw_codes, dict) else {}
    encoded_codes = ",".join(
        f"{_safe_failure_code(code)}:{_safe_nonnegative_int(count)}"
        for code, count in sorted(codes.items(), key=lambda item: str(item[0]))[:10]
    ) or "none"
    return (
        f"tracked_24h={_safe_nonnegative_int(delivery.get('tracked_24h'))}; "
        f"by_status={encoded_statuses}; "
        f"failure_codes={encoded_codes}; "
        f"terminal_24h={_safe_nonnegative_int(delivery.get('terminal_24h'))}; "
        f"delivery_success_pct={_safe_percent(delivery.get('delivery_success_pct')):.1f}; "
        f"pending_over_15m={_safe_nonnegative_int(delivery.get('pending_over_15m'))}; "
        f"oldest_pending_age_seconds={_safe_nonnegative_int(delivery.get('oldest_pending_age_seconds'))}"
    )[:600]


def _append_outbound_diagnostics(
    checks: Sequence[SmokeCheck],
    *,
    base_url: str,
    admin_token: str,
) -> list[SmokeCheck]:
    result = list(checks)
    if not admin_token or not _needs_outbound_diagnostics(result):
        return result
    try:
        status, overview = fetch_json(
            base_url,
            "/admin/overview",
            token=admin_token,
            timeout=float(os.getenv("SMOKE_TIMEOUT_SECONDS", "15")),
        )
        delivery = overview.get("outbound_delivery")
        if status == 200 and isinstance(delivery, dict):
            result.append(SmokeCheck("outbound_delivery_diagnostics", "pass", _outbound_delivery_detail(overview)))
        else:
            result.append(SmokeCheck("outbound_delivery_diagnostics", "pass", f"unavailable; HTTP {status}"))
    except SmokeError as exc:
        result.append(
            SmokeCheck(
                "outbound_delivery_diagnostics",
                "pass",
                f"unavailable; {type(exc).__name__}",
            )
        )
    return result


def run_monitor(
    base_url: str,
    *,
    admin_token: str = "",
    expected_version: str = "",
    attempts: int = 3,
    delay_seconds: float = 20.0,
    require_launch_ready: bool = False,
    sleep: Callable[[float], None] = time.sleep,
    smoke_runner: Callable[..., list[SmokeCheck]] = run_smoke,
) -> MonitorReport:
    """Run bounded retries and return only sanitized aggregate check details."""
    total_attempts = _bounded_attempts(attempts)
    delay = _bounded_delay(delay_seconds)
    final_checks: Sequence[SmokeCheck] = ()
    attempts_run = 0

    for attempt in range(1, total_attempts + 1):
        attempts_run = attempt
        try:
            final_checks = smoke_runner(
                base_url,
                admin_token=admin_token,
                expected_version=expected_version,
                require_postgresql=True,
                require_signature=True,
                require_launch_ready=require_launch_ready,
                timeout=float(os.getenv("SMOKE_TIMEOUT_SECONDS", "15")),
            )
        except Exception as exc:  # The report must still exist for incident automation.
            final_checks = _safe_failure(f"monitor raised {type(exc).__name__}")

        if final_checks and all(check.passed for check in final_checks):
            break
        if attempt < total_attempts and delay:
            sleep(delay)

    final_checks = _append_outbound_diagnostics(
        final_checks,
        base_url=base_url,
        admin_token=admin_token,
    )
    healthy = bool(final_checks) and all(check.passed for check in final_checks)
    return MonitorReport(
        status="healthy" if healthy else "unhealthy",
        generated_at=datetime.now(UTC).isoformat(),
        attempts_configured=total_attempts,
        attempts_run=attempts_run,
        recovered_after_retry=healthy and attempts_run > 1,
        checks=[asdict(check) for check in final_checks],
    )


def write_report(report: MonitorReport, output: Path | None = None) -> str:
    payload = json.dumps(asdict(report), ensure_ascii=False, sort_keys=True)
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload + "\n", encoding="utf-8")
    return payload


def _flag(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().casefold() in {"1", "true", "yes", "on"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Retry read-only production checks and emit a safe incident report.")
    parser.add_argument("--base-url", default=os.getenv("PRODUCTION_BASE_URL", ""))
    parser.add_argument("--admin-token", default=os.getenv("ADMIN_API_TOKEN", ""))
    parser.add_argument("--expected-version", default=os.getenv("EXPECTED_APP_VERSION", ""))
    parser.add_argument("--attempts", type=int, default=int(os.getenv("PRODUCTION_MONITOR_ATTEMPTS", "3")))
    parser.add_argument("--delay-seconds", type=float, default=float(os.getenv("PRODUCTION_MONITOR_DELAY_SECONDS", "20")))
    parser.add_argument(
        "--require-launch-ready",
        action="store_true",
        default=_flag(os.getenv("PRODUCTION_MONITOR_REQUIRE_LAUNCH_READY"), False),
    )
    parser.add_argument("--output", type=Path, default=Path("production-monitor.json"))
    args = parser.parse_args(argv)

    if not str(args.base_url).strip():
        report = MonitorReport(
            status="unhealthy",
            generated_at=datetime.now(UTC).isoformat(),
            attempts_configured=_bounded_attempts(args.attempts),
            attempts_run=0,
            recovered_after_retry=False,
            checks=[asdict(item) for item in _safe_failure("PRODUCTION_BASE_URL is missing")],
        )
    else:
        report = run_monitor(
            str(args.base_url),
            admin_token=str(args.admin_token or ""),
            expected_version=str(args.expected_version or ""),
            attempts=args.attempts,
            delay_seconds=args.delay_seconds,
            require_launch_ready=bool(args.require_launch_ready),
        )

    print(write_report(report, args.output))
    return 0 if report.healthy else 1


if __name__ == "__main__":
    sys.exit(main())
