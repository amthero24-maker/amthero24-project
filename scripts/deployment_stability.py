"""Consecutive, read-only post-deployment certification for AmtHero24.

Unlike the recovery-oriented production monitor, this gate requires every configured
sample to pass. A transient failure is therefore enough to reject the deployment from
Beta expansion. Reports contain only bounded smoke-check names, statuses, and sanitized
details; URLs, tokens, headers, and response bodies are never emitted.
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
from typing import Callable

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from production_smoke import SmokeCheck, run_smoke


@dataclass(frozen=True)
class StabilitySample:
    number: int
    passed: bool
    checks: list[dict[str, str]]


@dataclass(frozen=True)
class StabilityReport:
    status: str
    generated_at: str
    samples_configured: int
    samples_run: int
    consecutive_passes: int
    samples: list[StabilitySample]

    @property
    def passed(self) -> bool:
        return self.status == "stable"


def _bounded_samples(value: int) -> int:
    return max(2, min(int(value), 6))


def _bounded_delay(value: float) -> float:
    return max(0.0, min(float(value), 300.0))


def _safe_failure(name: str, detail: str) -> list[SmokeCheck]:
    return [SmokeCheck(name, "fail", detail[:240])]


def run_stability_gate(
    base_url: str,
    *,
    admin_token: str,
    expected_version: str,
    samples: int = 3,
    delay_seconds: float = 30.0,
    require_launch_ready: bool = True,
    timeout: float = 20.0,
    sleep: Callable[[float], None] = time.sleep,
    smoke_runner: Callable[..., list[SmokeCheck]] = run_smoke,
) -> StabilityReport:
    """Require consecutive healthy samples; stop immediately on the first failure."""
    sample_count = _bounded_samples(samples)
    delay = _bounded_delay(delay_seconds)
    reports: list[StabilitySample] = []
    consecutive = 0

    if not str(base_url or "").strip():
        checks = _safe_failure("base_url", "PRODUCTION_BASE_URL is missing")
        reports.append(StabilitySample(1, False, [asdict(item) for item in checks]))
    elif not str(admin_token or "").strip():
        checks = _safe_failure("admin_token", "ADMIN_API_TOKEN is missing")
        reports.append(StabilitySample(1, False, [asdict(item) for item in checks]))
    elif not str(expected_version or "").strip():
        checks = _safe_failure("expected_version", "EXPECTED_APP_VERSION is missing")
        reports.append(StabilitySample(1, False, [asdict(item) for item in checks]))
    else:
        for number in range(1, sample_count + 1):
            try:
                checks = smoke_runner(
                    base_url,
                    admin_token=admin_token,
                    expected_version=expected_version,
                    require_postgresql=True,
                    require_signature=True,
                    require_launch_ready=require_launch_ready,
                    timeout=max(1.0, min(float(timeout), 60.0)),
                )
            except Exception as exc:
                checks = _safe_failure("stability_execution", f"smoke runner raised {type(exc).__name__}")
            passed = bool(checks) and all(item.passed for item in checks)
            reports.append(StabilitySample(number, passed, [asdict(item) for item in checks]))
            if not passed:
                break
            consecutive += 1
            if number < sample_count and delay:
                sleep(delay)

    stable = consecutive == sample_count and len(reports) == sample_count
    return StabilityReport(
        status="stable" if stable else "unstable",
        generated_at=datetime.now(UTC).isoformat(),
        samples_configured=sample_count,
        samples_run=len(reports),
        consecutive_passes=consecutive,
        samples=reports,
    )


def write_report(report: StabilityReport, output: Path | None = None) -> str:
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
    parser = argparse.ArgumentParser(description="Require consecutive healthy AmtHero24 production samples.")
    parser.add_argument("--base-url", default=os.getenv("PRODUCTION_BASE_URL", ""))
    parser.add_argument("--admin-token", default=os.getenv("ADMIN_API_TOKEN", ""))
    parser.add_argument("--expected-version", default=os.getenv("EXPECTED_APP_VERSION", ""))
    parser.add_argument("--samples", type=int, default=int(os.getenv("DEPLOYMENT_STABILITY_SAMPLES", "3")))
    parser.add_argument("--delay-seconds", type=float, default=float(os.getenv("DEPLOYMENT_STABILITY_DELAY_SECONDS", "30")))
    parser.add_argument("--timeout", type=float, default=float(os.getenv("SMOKE_TIMEOUT_SECONDS", "20")))
    parser.add_argument(
        "--require-launch-ready",
        action="store_true",
        default=_flag(os.getenv("DEPLOYMENT_STABILITY_REQUIRE_LAUNCH_READY"), True),
    )
    parser.add_argument("--output", type=Path, default=Path("deployment-stability.json"))
    args = parser.parse_args(argv)

    report = run_stability_gate(
        str(args.base_url or ""),
        admin_token=str(args.admin_token or ""),
        expected_version=str(args.expected_version or ""),
        samples=args.samples,
        delay_seconds=args.delay_seconds,
        require_launch_ready=bool(args.require_launch_ready),
        timeout=args.timeout,
    )
    print(write_report(report, args.output))
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
