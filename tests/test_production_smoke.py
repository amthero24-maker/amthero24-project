"""Tests for non-mutating production smoke checks."""
from __future__ import annotations

from unittest.mock import patch

import production_smoke
import production_surface


def _healthy(path: str) -> tuple[int, dict]:
    if path == "/health":
        return 200, {"status": "ok", "version": "3.2.0", "storage": "postgresql"}
    if path == "/ready":
        return 200, {
            "status": "ready",
            "components": {
                "storage_backend": "postgresql",
                "postgresql_schemas": "initialized",
                "database_schema_migrations": "current",
                "database_schema_version": 1,
                "database_fallback": "fail-closed",
                "process_lifecycle": "accepting",
                "webhook_signature": "enforced",
                "webhook_idempotency": "retry-safe",
                "durable_inbound_queue": "configured",
                "outbound_delivery_receipts": "enabled",
                "reminders": "enabled",
                "reminder_worker": "running",
                "reminder_encryption": "configured",
                "admin_overview": "configured",
                "privacy_retention": "enabled",
                "provider_telemetry": "enabled",
                "abuse_guard": "enforced",
            },
        }
    if path == "/admin/overview":
        return 200, {
            "reminders": {
                "total": 1,
                "by_status": {"pending": 1},
                "due_unsent": 1,
                "unsent_recipients": 1,
                "latest": {
                    "status": "pending",
                    "scheduled_at": "2026-08-05T00:12:00+00:00",
                    "attempt_count": 0,
                    "last_error_code": "",
                    "next_attempt_at": "2026-08-05T00:12:00+00:00",
                    "lease_until": None,
                    "sent_at": None,
                },
            }
        }
    return 200, {"status": "ready"}


def test_smoke_passes_for_healthy_production() -> None:
    with patch("production_smoke.fetch_json", side_effect=lambda base, path, **kwargs: _healthy(path)):
        checks = production_smoke.run_smoke(
            "https://example.test",
            admin_token="secret",
            expected_version="3.2.0",
            require_signature=True,
            require_launch_ready=True,
        )
    assert checks
    assert all(item.passed for item in checks)
    assert {item.name for item in checks} >= {
        "health",
        "readiness",
        "storage_backend",
        "postgresql_schemas",
        "database_schema_migrations",
        "database_schema_version",
        "database_fallback",
        "process_lifecycle",
        "webhook_idempotency",
        "durable_inbound_queue",
        "outbound_delivery_receipts",
        "reminders",
        "reminder_worker",
        "reminder_encryption",
        "admin_secret",
        "launch_decision",
        "reminder_diagnostics",
    }
    diagnostics = next(item for item in checks if item.name == "reminder_diagnostics")
    assert diagnostics.detail.startswith("total=1; by_status=pending:1; due_unsent=1")


def test_smoke_fails_on_storage_schema_signature_delivery_and_lifecycle_misconfiguration() -> None:
    def response(base: str, path: str, **kwargs):
        if path == "/health":
            return 200, {"status": "ok", "version": "3.2.0"}
        return 503, {
            "status": "not_ready",
            "components": {
                "storage_backend": "json-fallback",
                "postgresql_schemas": "unavailable",
                "database_schema_migrations": "unverified",
                "database_schema_version": 0,
                "database_fallback": "allowed",
                "process_lifecycle": "draining",
                "webhook_signature": "optional",
                "webhook_idempotency": "missing",
                "durable_inbound_queue": "misconfigured",
                "outbound_delivery_receipts": "missing",
                "reminders": "misconfigured",
                "reminder_worker": "stopped",
                "reminder_encryption": "weak",
                "privacy_retention": "enabled",
                "provider_telemetry": "enabled",
                "abuse_guard": "enforced",
            },
        }

    with patch("production_smoke.fetch_json", side_effect=response):
        checks = production_smoke.run_smoke("https://example.test", require_signature=True)
    failed = {item.name for item in checks if not item.passed}
    assert failed == {
        "readiness",
        "storage_backend",
        "postgresql_schemas",
        "database_schema_migrations",
        "database_schema_version",
        "database_fallback",
        "process_lifecycle",
        "webhook_signature",
        "webhook_idempotency",
        "durable_inbound_queue",
        "outbound_delivery_receipts",
        "reminders",
        "reminder_encryption",
    }


def test_reminder_worker_gate_is_optional_by_default() -> None:
    def response(base: str, path: str, **kwargs):
        status, payload = _healthy(path)
        if path == "/ready":
            payload["components"]["reminder_worker"] = "stopped"
        return status, payload

    with patch("production_smoke.fetch_json", side_effect=response):
        checks = production_smoke.run_smoke("https://example.test")

    worker = next(item for item in checks if item.name == "reminder_worker")
    assert worker.passed is True
    assert worker.detail == "stopped"


def test_reminder_worker_gate_fails_when_required_and_not_running() -> None:
    def response(base: str, path: str, **kwargs):
        status, payload = _healthy(path)
        if path == "/ready":
            payload["components"]["reminder_worker"] = "stopped"
        return status, payload

    with patch("production_smoke.fetch_json", side_effect=response):
        checks = production_smoke.run_smoke(
            "https://example.test",
            require_reminder_worker=True,
        )

    worker = next(item for item in checks if item.name == "reminder_worker")
    assert worker.passed is False
    assert worker.detail == "stopped"


def test_reminder_worker_gate_passes_only_for_running_worker_when_required() -> None:
    with patch("production_smoke.fetch_json", side_effect=lambda base, path, **kwargs: _healthy(path)):
        checks = production_smoke.run_smoke(
            "https://example.test",
            require_reminder_worker=True,
        )

    worker = next(item for item in checks if item.name == "reminder_worker")
    assert worker.passed is True
    assert worker.detail == "running"


def test_disabled_durable_queue_is_allowed_during_safe_rollout() -> None:
    def response(base: str, path: str, **kwargs):
        status, payload = _healthy(path)
        if path == "/ready":
            payload["components"]["durable_inbound_queue"] = "disabled"
        return status, payload

    with patch("production_smoke.fetch_json", side_effect=response):
        checks = production_smoke.run_smoke("https://example.test", require_signature=True)

    assert next(item for item in checks if item.name == "durable_inbound_queue").passed is True


def test_disabled_reminders_pass_only_with_strict_ready_launch_report() -> None:
    def response(base: str, path: str, **kwargs):
        status, payload = _healthy(path)
        if path == "/ready":
            payload["components"]["reminders"] = "disabled"
        return status, payload

    with patch("production_smoke.fetch_json", side_effect=response):
        checks = production_smoke.run_smoke(
            "https://example.test",
            admin_token="secret",
            require_launch_ready=True,
        )

    assert next(item for item in checks if item.name == "launch_decision").passed is True
    assert next(item for item in checks if item.name == "reminders").passed is True


def test_disabled_reminders_still_fail_without_strict_ready_launch_report() -> None:
    def response(base: str, path: str, **kwargs):
        status, payload = _healthy(path)
        if path == "/ready":
            payload["components"]["reminders"] = "disabled"
        if path == "/admin/launch-readiness":
            return 200, {"status": "warning"}
        return status, payload

    with patch("production_smoke.fetch_json", side_effect=response):
        relaxed = production_smoke.run_smoke("https://example.test", admin_token="secret")
        strict = production_smoke.run_smoke(
            "https://example.test",
            admin_token="secret",
            require_launch_ready=True,
        )

    assert next(item for item in relaxed if item.name == "reminders").passed is False
    assert next(item for item in strict if item.name == "reminders").passed is False
    assert next(item for item in strict if item.name == "launch_decision").passed is False


def test_non_postgres_smoke_can_explicitly_relax_schema_gate() -> None:
    def response(base: str, path: str, **kwargs):
        status, payload = _healthy(path)
        if path == "/ready":
            payload["components"]["storage_backend"] = "json"
            payload["components"]["postgresql_schemas"] = "not-applicable"
            payload["components"]["database_schema_migrations"] = "not-applicable"
            payload["components"]["database_schema_version"] = 0
            payload["components"]["database_fallback"] = "allowed"
        return status, payload

    with patch("production_smoke.fetch_json", side_effect=response):
        checks = production_smoke.run_smoke("https://example.test", require_postgresql=False)

    assert next(item for item in checks if item.name == "database_schema_migrations").passed is True
    assert next(item for item in checks if item.name == "database_schema_version").passed is True


def test_smoke_stops_cleanly_when_health_is_unavailable() -> None:
    with patch("production_smoke.fetch_json", side_effect=production_smoke.SmokeError("endpoint unavailable")):
        checks = production_smoke.run_smoke("https://example.test")
    assert len(checks) == 1
    assert checks[0].name == "health"
    assert checks[0].passed is False


def test_launch_warning_is_allowed_unless_strict_gate_requested() -> None:
    def response(base: str, path: str, **kwargs):
        if path == "/admin/launch-readiness":
            return 200, {"status": "warning"}
        return _healthy(path)

    with patch("production_smoke.fetch_json", side_effect=response):
        relaxed = production_smoke.run_smoke("https://example.test", admin_token="secret")
        strict = production_smoke.run_smoke(
            "https://example.test",
            admin_token="secret",
            require_launch_ready=True,
        )
    assert next(item for item in relaxed if item.name == "launch_decision").passed is True
    assert next(item for item in strict if item.name == "launch_decision").passed is False


def test_non_indexable_surface_contract_passes_without_report_leakage() -> None:
    responses = {
        "/robots.txt": production_surface.SurfaceResponse(
            200,
            b"User-agent: *\nDisallow: /\n",
            "no-store",
            "noindex, nofollow, noarchive",
        ),
        "/docs": production_surface.SurfaceResponse(
            404,
            b"private body",
            "no-store",
            "noindex, nofollow, noarchive",
        ),
        "/redoc": production_surface.SurfaceResponse(
            404,
            b"private body",
            "no-store",
            "noindex, nofollow, noarchive",
        ),
        "/openapi.json": production_surface.SurfaceResponse(
            404,
            b"private body",
            "no-store",
            "noindex, nofollow, noarchive",
        ),
        "/health": production_surface.SurfaceResponse(
            200,
            b'{"status":"ok"}',
            "",
            "noindex, nofollow, noarchive",
        ),
    }

    checks = production_surface.run_non_indexable_surface_checks(
        "https://private.example",
        fetcher=lambda base, path, **kwargs: responses[path],
    )
    encoded = " ".join(item.detail for item in checks)

    assert [item.name for item in checks] == [
        "crawler_policy",
        "framework_discovery",
        "global_noindex",
    ]
    assert all(item.passed for item in checks)
    assert "private.example" not in encoded
    assert "private body" not in encoded
    assert "status\":\"ok" not in encoded


def test_non_indexable_surface_contract_fails_when_discovery_is_exposed() -> None:
    def fetcher(base: str, path: str, **kwargs):
        if path == "/robots.txt":
            return production_surface.SurfaceResponse(
                200,
                b"User-agent: *\nDisallow: /\n",
                "no-store",
                "noindex, nofollow, noarchive",
            )
        if path == "/health":
            return production_surface.SurfaceResponse(
                200,
                b"{}",
                "",
                "noindex, nofollow, noarchive",
            )
        return production_surface.SurfaceResponse(
            200,
            b"framework discovery must remain private",
            "public, max-age=3600",
            "",
        )

    checks = production_surface.run_non_indexable_surface_checks(
        "https://private.example",
        fetcher=fetcher,
    )

    discovery = next(item for item in checks if item.name == "framework_discovery")
    assert discovery.passed is False
    assert discovery.detail == "blocked=0/3; no_store=0/3; noindex=0/3; errors=0"
    assert "framework discovery must remain private" not in discovery.detail


def test_surface_network_errors_are_reduced_to_safe_error_types() -> None:
    def fetcher(base: str, path: str, **kwargs):
        raise production_surface.SurfaceError(
            "https://private.example?token=must-not-appear"
        )

    checks = production_surface.run_non_indexable_surface_checks(
        "https://private.example",
        fetcher=fetcher,
    )
    encoded = " ".join(item.detail for item in checks)

    assert all(not item.passed for item in checks)
    assert "private.example" not in encoded
    assert "must-not-appear" not in encoded
    assert "SurfaceError" in encoded
