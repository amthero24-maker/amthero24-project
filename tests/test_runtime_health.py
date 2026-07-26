"""Production readiness tests."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

import runtime_health


class _JsonStore:
    backend_name = "json"

    def __init__(self, path: Path) -> None:
        self.path = path


class _PostgresStore:
    backend_name = "postgresql"

    def __init__(self, healthy: bool = True) -> None:
        row = {"healthy": 1} if healthy else None
        connection = MagicMock()
        connection.execute.return_value.fetchone.return_value = row
        context = MagicMock()
        context.__enter__.return_value = connection
        self.pool = MagicMock()
        self.pool.connection.return_value = context


def test_configuration_readiness_does_not_expose_values() -> None:
    env = {
        "GROQ_API_KEY": "secret-groq",
        "WHATSAPP_TOKEN": "secret-whatsapp",
        "PHONE_NUMBER_ID": "123",
        "VERIFY_TOKEN": "secret-verify",
    }
    with patch.dict("os.environ", env, clear=True):
        assert runtime_health.configuration_ready() is True
        payload, status = runtime_health.readiness_payload(_JsonStore(Path("/tmp/store.json")), version="1", model="model")
    assert status == 200
    assert "secret" not in str(payload)


def test_missing_configuration_fails_readiness(tmp_path) -> None:
    with patch.dict("os.environ", {}, clear=True):
        payload, status = runtime_health.readiness_payload(_JsonStore(tmp_path / "store.json"), version="1", model="model")
    assert status == 503
    assert payload["components"]["configuration"] == "missing"


def test_postgres_ping_reports_ready() -> None:
    env = {
        "GROQ_API_KEY": "x", "WHATSAPP_TOKEN": "x", "PHONE_NUMBER_ID": "x", "VERIFY_TOKEN": "x",
        "DATABASE_URL": "postgresql://private",
    }
    with patch.dict("os.environ", env, clear=True):
        payload, status = runtime_health.readiness_payload(_PostgresStore(), version="1", model="model")
    assert status == 200
    assert payload["components"]["storage_backend"] == "postgresql"


def test_database_expected_but_json_fallback_is_not_ready(tmp_path) -> None:
    env = {
        "GROQ_API_KEY": "x", "WHATSAPP_TOKEN": "x", "PHONE_NUMBER_ID": "x", "VERIFY_TOKEN": "x",
        "DATABASE_URL": "postgresql://private",
    }
    with patch.dict("os.environ", env, clear=True):
        payload, status = runtime_health.readiness_payload(_JsonStore(tmp_path / "store.json"), version="1", model="model")
    assert status == 503
    assert payload["components"]["storage_backend"] == "json-fallback"


def test_ready_endpoint_returns_safe_component_status(monkeypatch) -> None:
    monkeypatch.setattr(runtime_health, "readiness_payload", lambda *args, **kwargs: ({"status": "ready", "components": {}}, 200))
    response = TestClient(runtime_health.app).get("/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"
