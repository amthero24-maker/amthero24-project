"""Protected admin overview API tests."""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

from starlette.testclient import TestClient

import admin_extensions
from data_store import JsonDataStore


def _install_store(tmp_path) -> JsonDataStore:
    store = JsonDataStore(tmp_path / "store.json")
    store.update_user("49123", {
        "preferred_language": "de",
        "memory_consent": "granted",
        "last_seen": datetime.now(UTC).isoformat(),
        "first_name": "Private Name",
    })
    admin_extensions.core.store = store
    admin_extensions.core._hero_memory_store = admin_extensions.core.HeroMemory(store)
    return store


def test_admin_endpoint_is_hidden_when_not_configured(tmp_path) -> None:
    _install_store(tmp_path)
    client = TestClient(admin_extensions.core.app)
    with patch.dict("os.environ", {}, clear=True):
        response = client.get("/admin/overview")
    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"


def test_admin_endpoint_rejects_wrong_token(tmp_path) -> None:
    _install_store(tmp_path)
    client = TestClient(admin_extensions.core.app)
    with patch.dict("os.environ", {"ADMIN_API_TOKEN": "correct-secret"}, clear=True):
        response = client.get("/admin/overview", headers={"Authorization": "Bearer wrong-secret"})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_admin_endpoint_returns_only_aggregates_with_valid_token(tmp_path) -> None:
    _install_store(tmp_path)
    client = TestClient(admin_extensions.core.app)
    with patch.dict("os.environ", {"ADMIN_API_TOKEN": "correct-secret"}, clear=True):
        response = client.get("/admin/overview", headers={"X-Admin-Token": "correct-secret"})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    payload = response.json()
    assert payload["users"]["total"] == 1
    assert "Private Name" not in response.text
    assert "49123" not in response.text
