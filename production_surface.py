"""Privacy-safe live checks for AmtHero24's non-indexable production bot surface.

The checker performs bounded, read-only HTTP GETs. It never sends WhatsApp messages,
uses credentials, writes application data, or includes URLs, headers, or response bodies
in its returned smoke-check details.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from production_smoke import SmokeCheck

_MAX_BODY_BYTES = 4096
_ROBOTS_BODY = b"User-agent: *\nDisallow: /\n"
_DISCOVERY_PATHS = ("/docs", "/redoc", "/openapi.json")
_REQUIRED_ROBOTS_TOKENS = {"noindex", "nofollow", "noarchive"}


class SurfaceError(RuntimeError):
    """Raised when a public-surface endpoint cannot be inspected safely."""


@dataclass(frozen=True)
class SurfaceResponse:
    status: int
    body: bytes
    cache_control: str
    robots_tag: str


def _base_url(value: str) -> str:
    cleaned = str(value or "").strip()
    if not cleaned.startswith(("https://", "http://")):
        raise ValueError("base URL must start with https:// or http://")
    return cleaned.rstrip("/") + "/"


def _bounded_header(value: str | None) -> str:
    return " ".join(str(value or "").strip().casefold().split())[:240]


def _has_no_store(value: str) -> bool:
    return "no-store" in {token.strip() for token in value.split(",") if token.strip()}


def _has_required_robots_tag(value: str) -> bool:
    tokens = {
        token
        for token in re.split(r"[\s,;]+", value.casefold())
        if token
    }
    return _REQUIRED_ROBOTS_TOKENS.issubset(tokens)


def fetch_surface(
    base_url: str,
    path: str,
    *,
    timeout: float = 15.0,
) -> SurfaceResponse:
    """Read one bounded public response without emitting its URL, body, or headers."""
    url = urljoin(_base_url(base_url), path.lstrip("/"))
    request = Request(
        url,
        headers={
            "Accept": "text/plain, application/json;q=0.5, */*;q=0.1",
            "User-Agent": "AmtHero24-Surface/1.0",
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=max(1.0, min(float(timeout), 60.0))) as response:
            status = int(response.status)
            body = response.read(_MAX_BODY_BYTES + 1)
            headers = response.headers
    except HTTPError as exc:
        status = int(exc.code)
        body = exc.read(_MAX_BODY_BYTES + 1)
        headers = exc.headers
    except (URLError, TimeoutError, OSError) as exc:
        raise SurfaceError(f"endpoint unavailable: {type(exc).__name__}") from exc

    if len(body) > _MAX_BODY_BYTES:
        raise SurfaceError("response exceeded bounded inspection limit")
    return SurfaceResponse(
        status=status,
        body=body,
        cache_control=_bounded_header(headers.get("Cache-Control")),
        robots_tag=_bounded_header(headers.get("X-Robots-Tag")),
    )


def run_non_indexable_surface_checks(
    base_url: str,
    *,
    timeout: float = 15.0,
    fetcher: Callable[..., SurfaceResponse] = fetch_surface,
) -> list[SmokeCheck]:
    """Verify crawler policy, blocked framework discovery, and global noindex."""
    checks: list[SmokeCheck] = []

    try:
        robots = fetcher(base_url, "/robots.txt", timeout=timeout)
        policy_ok = robots.body == _ROBOTS_BODY
        no_store = _has_no_store(robots.cache_control)
        noindex = _has_required_robots_tag(robots.robots_tag)
        passed = robots.status == 200 and policy_ok and no_store and noindex
        checks.append(
            SmokeCheck(
                "crawler_policy",
                "pass" if passed else "fail",
                (
                    f"HTTP {robots.status}; disallow_all={'yes' if policy_ok else 'no'}; "
                    f"no_store={'yes' if no_store else 'no'}; "
                    f"noindex={'yes' if noindex else 'no'}"
                ),
            )
        )
    except (SurfaceError, ValueError) as exc:
        checks.append(
            SmokeCheck(
                "crawler_policy",
                "fail",
                f"unavailable; {type(exc).__name__}",
            )
        )

    blocked = 0
    no_store_count = 0
    noindex_count = 0
    errors = 0
    for path in _DISCOVERY_PATHS:
        try:
            response = fetcher(base_url, path, timeout=timeout)
        except (SurfaceError, ValueError):
            errors += 1
            continue
        blocked += int(response.status == 404)
        no_store_count += int(_has_no_store(response.cache_control))
        noindex_count += int(_has_required_robots_tag(response.robots_tag))
    total = len(_DISCOVERY_PATHS)
    discovery_ok = (
        blocked == total
        and no_store_count == total
        and noindex_count == total
        and errors == 0
    )
    checks.append(
        SmokeCheck(
            "framework_discovery",
            "pass" if discovery_ok else "fail",
            (
                f"blocked={blocked}/{total}; no_store={no_store_count}/{total}; "
                f"noindex={noindex_count}/{total}; errors={errors}"
            ),
        )
    )

    try:
        health = fetcher(base_url, "/health", timeout=timeout)
        noindex = _has_required_robots_tag(health.robots_tag)
        passed = health.status == 200 and noindex
        checks.append(
            SmokeCheck(
                "global_noindex",
                "pass" if passed else "fail",
                f"HTTP {health.status}; noindex={'yes' if noindex else 'no'}",
            )
        )
    except (SurfaceError, ValueError) as exc:
        checks.append(
            SmokeCheck(
                "global_noindex",
                "fail",
                f"unavailable; {type(exc).__name__}",
            )
        )

    return checks
