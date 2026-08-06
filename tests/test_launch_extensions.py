"""Protected Beta launch report endpoint tests."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from starlette.testclient import TestClient

import launch_extensions
from data_store import JsonDataStore

ADMIN_TOKEN = "admin-token-2026-unique-8xK2mP7qR4vN"


def _install_store(tmp_path) -> JsonDataStore:
    store = JsonDataStore(tmp_path / "store.json")
    launch_extensions.core.store = store
    launch_extensions.core._hero_memory_store = launch_extensions.core.HeroMemory(store)
    return store


def _overview() -> dict:
    return {
        "storage_backend": "postgresql",
        "messages_24h": {"total": 20, "failed": 0},
        "providers": {
            "groq": {"total": 10, "success": 10, "failure": 0, "circuit_rejected": 0, "circuit": "closed", "latency_ms": {"p95": 500}},
            "whatsapp": {"total": 10, "success": 10, "failure": 0, "circuit_rejected": 0, "latency_ms": {"p95": 300}},
        },
        "reminders": {"by_status": {"sent": 1}},
        "abuse_guard": {"active_blocks": 0},
        "entitlements": {"mode": "observe-only"},
    }


def _env() -> dict[str, str]:
    return {
        "ADMIN_API_TOKEN": ADMIN_TOKEN,
        "META_APP_SECRET": "configured",
        "WEBHOOK_SIGNATURE_REQUIRED": "true",
        "PRIVACY_RETENTION_ENABLED": "true",
        "REMINDER_WORKER_ENABLED": "true",
        "REMINDER_CANARY_SENDERS": "+491701234567",
        "REMINDER_ENCRYPTION_KEY": "reminder-key-2026-unique-7fA9xQ2mLp8V",
        "REMINDER_LEGACY_TOKEN_DECRYPTION_ENABLED": "false",
        "WHATSAPP_REMINDER_TEMPLATE": "utility_template",
        "HUMAN_SUPPORT_ENABLED": "false",
    }


def test_launch_endpoint_is_hidden_without_admin_token(tmp_path) -> None:
    _install_store(tmp_path)
    client = TestClient(launch_extensions.core.app)
    with patch.dict("os.environ", {}, clear=True):
        response = client.get("/admin/launch-readiness")
    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"


def test_launch_endpoint_rejects_wrong_token(tmp_path) -> None:
    _install_store(tmp_path)
    client = TestClient(launch_extensions.core.app)
    with patch.dict("os.environ", _env(), clear=True):
        response = client.get("/admin/launch-readiness", headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 401


def test_launch_endpoint_returns_actionable_report_without_personal_data(tmp_path) -> None:
    _install_store(tmp_path)
    client = TestClient(launch_extensions.core.app)
    with patch.dict("os.environ", _env(), clear=True), patch.object(
        launch_extensions.admin_module, "build_overview", return_value=_overview()
    ):
        response = client.get("/admin/launch-readiness", headers={"X-Admin-Token": ADMIN_TOKEN})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    payload = response.json()
    assert payload["status"] == "ready"
    assert payload["launch_scope"] == "controlled_beta"
    assert "49123" not in response.text
    assert "first_name" not in response.text


def test_launch_endpoint_blocks_invalid_runtime_flag_without_echoing_it(
    tmp_path,
) -> None:
    _install_store(tmp_path)
    client = TestClient(launch_extensions.core.app)
    environment = _env()
    sensitive_value = "synthetic-sensitive-invalid-runtime-value"
    environment["BRIEF_SCANNER_RUNTIME_ENABLED"] = sensitive_value

    with patch.dict("os.environ", environment, clear=True), patch.object(
        launch_extensions.admin_module, "build_overview", return_value=_overview()
    ):
        response = client.get(
            "/admin/launch-readiness",
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "blocked"
    assert sensitive_value not in response.text


def test_disabled_reminders_and_queue_are_ready_for_read_only_canary(tmp_path) -> None:
    _install_store(tmp_path)
    client = TestClient(launch_extensions.core.app)
    environment = _env()
    environment.update({
        "REMINDER_WORKER_ENABLED": "false",
        "REMINDER_LEGACY_TOKEN_DECRYPTION_ENABLED": "false",
        "DURABLE_QUEUE_ENABLED": "false",
    })
    environment.pop("WHATSAPP_REMINDER_TEMPLATE")

    with patch.dict("os.environ", environment, clear=True), patch.object(
        launch_extensions.admin_module, "build_overview", return_value=_overview()
    ):
        response = client.get(
            "/admin/launch-readiness",
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )

    assert response.status_code == 200
    payload = response.json()
    checks = {item["code"]: item for item in payload["checks"]}
    assert checks["reminder_encryption"]["status"] == "ready"
    assert checks["reminder_delivery"]["status"] == "ready"
    assert checks["reminder_legacy_decryption"]["status"] == "ready"
    assert checks["durable_queue"]["status"] == "ready"
    assert payload["status"] == "ready"


def test_exact_sender_canary_is_ready_inside_service_window_without_template(tmp_path) -> None:
    _install_store(tmp_path)
    client = TestClient(launch_extensions.core.app)
    environment = _env()
    environment.pop("WHATSAPP_REMINDER_TEMPLATE")

    with patch.dict("os.environ", environment, clear=True), patch.object(
        launch_extensions.admin_module, "build_overview", return_value=_overview()
    ):
        response = client.get(
            "/admin/launch-readiness",
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )

    assert response.status_code == 200
    payload = response.json()
    checks = {item["code"]: item for item in payload["checks"]}
    assert checks["reminder_delivery"]["status"] == "ready"
    assert "24-hour service window" in checks["reminder_delivery"]["detail"]
    assert payload["status"] == "ready"


def test_worker_without_canary_remains_blocked_without_template(tmp_path) -> None:
    _install_store(tmp_path)
    client = TestClient(launch_extensions.core.app)
    environment = _env()
    environment.pop("REMINDER_CANARY_SENDERS")
    environment.pop("WHATSAPP_REMINDER_TEMPLATE")

    with patch.dict("os.environ", environment, clear=True), patch.object(
        launch_extensions.admin_module, "build_overview", return_value=_overview()
    ):
        response = client.get(
            "/admin/launch-readiness",
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )

    assert response.status_code == 200
    payload = response.json()
    checks = {item["code"]: item for item in payload["checks"]}
    assert checks["reminder_canary"]["status"] == "blocked"
    assert checks["reminder_delivery"]["status"] == "blocked"
    assert payload["status"] == "blocked"


def test_explicit_queue_activation_preserves_encryption_gate(tmp_path) -> None:
    _install_store(tmp_path)
    client = TestClient(launch_extensions.core.app)
    environment = _env()
    environment["DURABLE_QUEUE_ENABLED"] = "true"
    environment.pop("MESSAGE_QUEUE_ENCRYPTION_KEY", None)

    with patch.dict("os.environ", environment, clear=True), patch.object(
        launch_extensions.admin_module, "build_overview", return_value=_overview()
    ):
        response = client.get(
            "/admin/launch-readiness",
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )

    assert response.status_code == 200
    checks = {item["code"]: item for item in response.json()["checks"]}
    assert checks["durable_queue"]["status"] == "blocked"


def test_reminder_preflight_is_hidden_without_admin_token(tmp_path) -> None:
    _install_store(tmp_path)
    client = TestClient(launch_extensions.core.app)
    with patch.dict("os.environ", {}, clear=True):
        response = client.get("/admin/reminder-encryption-preflight")
    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"


def test_reminder_preflight_returns_aggregate_only_read_only_report(tmp_path) -> None:
    _install_store(tmp_path)
    client = TestClient(launch_extensions.core.app)
    aggregate = {
        "mode": "dry-run",
        "total": 3,
        "already_current": 2,
        "decryptable_old_key": 0,
        "decryptable_legacy_token": 1,
        "unreadable": 0,
        "migrated": 0,
        "safe_to_apply": True,
        "reminder_id": "must-not-leak",
        "recipient_ciphertext": "must-not-leak",
    }
    with patch.dict("os.environ", _env(), clear=True), patch.object(
        launch_extensions,
        "migrate_reminder_ciphertexts",
        return_value=SimpleNamespace(as_dict=lambda: aggregate),
    ) as migration:
        response = client.get(
            "/admin/reminder-encryption-preflight",
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "mode": "dry-run",
        "total": 3,
        "already_current": 2,
        "decryptable_old_key": 0,
        "decryptable_legacy_token": 1,
        "unreadable": 0,
        "migrated": 0,
        "safe_to_apply": True,
    }
    assert "must-not-leak" not in response.text
    assert migration.call_args.kwargs["apply"] is False
