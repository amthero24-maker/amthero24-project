"""Read the protected launch report and emit only bounded non-ready check codes."""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from production_smoke import SmokeError, fetch_json


def _code(value: object) -> str:
    cleaned = re.sub(r"[^a-z0-9_-]", "", str(value or "").casefold())
    return cleaned[:80]


def main() -> int:
    output = Path("launch-readiness-codes.json")
    payload: dict[str, object] = {"status": "unavailable", "codes": []}
    try:
        status, report = fetch_json(
            os.getenv("PRODUCTION_BASE_URL", ""),
            "/admin/launch-readiness",
            token=os.getenv("ADMIN_API_TOKEN", ""),
            timeout=float(os.getenv("SMOKE_TIMEOUT_SECONDS", "20")),
        )
        codes: list[str] = []
        checks = report.get("checks") if isinstance(report.get("checks"), list) else []
        for check in checks:
            if not isinstance(check, dict) or str(check.get("status")) not in {"warning", "blocked"}:
                continue
            code = _code(check.get("code"))
            if code and code not in codes:
                codes.append(code)
        payload = {
            "status": _code(report.get("status")) or "unknown",
            "http_status": int(status),
            "codes": sorted(codes)[:30],
        }
    except (SmokeError, ValueError, TypeError, OSError):
        pass
    output.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
