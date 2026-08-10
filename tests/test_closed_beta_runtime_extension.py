from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from closed_beta_admission import AdmissionPolicy
from closed_beta_admission_repository import ClosedBetaAdmissionRepository
from closed_beta_privacy import is_delete_request
from closed_beta_runtime_extension import install, runtime_state
from data_store import JsonDataStore


class _Memory:
    def __init__(self, store: JsonDataStore) -> None:
        self.store = store

    def export_user_data(self, phone: str) -> dict[str, Any]:
        return {"profile": self.store.get_user(phone), "missions": []}

    def delete_all_user_data(self, phone: str) -> bool:
        return self.store.delete_user(phone)


class _Core:
    def __init__(self, store: JsonDataStore) -> None:
        self.store = store
        self.original_calls: list[Any] = []
        self.replies: list[tuple[str, str, str]] = []
        self.memory = _Memory(store)

        async def original(message: Any) -> None:
            self.original_calls.append(message)

        self.process_incoming = original

    @staticmethod
    def detect_language(text: str, previous: str) -> str:
        lowered = str(text or "").casefold()
        if any(character in lowered for character in "ابتثجحخدذرزسشصضطظعغفقكلمنهوي"):
            return "ar"
        return previous or "en"

    async def _finish(self, message_id: str, reply: str, sender: str) -> None:
        self.replies.append((message_id, reply, sender))

    def _hero_memory(self) -> _Memory:
        return self.memory

    @staticmethod
    def _is_export_request(text: str) -> bool:
        return "export my data" in str(text or "").casefold()

    @staticmethod
    def _export_reply(language: str, payload: dict[str, Any]) -> str:
        profile = payload.get("profile", {}) if isinstance(payload.get("profile"), dict) else {}
        return "BASE_EXPORT " + " ".join(f"{key}={value}" for key, value in sorted(profile.items()))

    @staticmethod
    def _deletion_confirmation(language: str) -> str:
        return "ALL_DATA_DELETED"


def _message(
    message_id: str,
    sender: str,
    text: str,
    *,
    internal_context: str = "",
) -> SimpleNamespace:
    return SimpleNamespace(
        message_id=message_id,
        sender=sender,
        text=text,
        message_type="text",
        internal_context=internal_context,
    )


def _admit(store: JsonDataStore, phone: str, *, capacity: int = 5) -> ClosedBetaAdmissionRepository:
    repository = ClosedBetaAdmissionRepository(store)
    result = repository.claim(
        phone,
        policy=AdmissionPolicy(enabled=True, capacity=capacity),
        beta_opt_in=True,
        consent_version="2026-08-wave1-v1",
    )
    assert result.decision.value == "admitted"
    return repository


@pytest.mark.anyio
async def test_disabled_gate_is_exact_pass_through_for_ordinary_messages(tmp_path) -> None:
    store = JsonDataStore(tmp_path / "store.json")
    core = _Core(store)
    install(core, env_provider=lambda: {})

    message = _message("one", "+490000000001", "hello")
    await core.process_incoming(message)

    assert core.original_calls == [message]
    assert core.replies == []
    assert "closed_beta_admissions" not in store.snapshot()


@pytest.mark.anyio
async def test_enabled_gate_requires_notice_then_claims_one_slot_idempotently(tmp_path) -> None:
    store = JsonDataStore(tmp_path / "store.json")
    core = _Core(store)
    env = {
        "CLOSED_BETA_ADMISSION_ENABLED": "true",
        "CLOSED_BETA_ADMISSION_CAPACITY": "5",
    }
    install(core, env_provider=lambda: env)

    await core.process_incoming(_message("notice", "+490000000002", "hello"))
    assert core.original_calls == []
    assert "Closed Beta" in core.replies[-1][1]

    await core.process_incoming(_message("yes", "+490000000002", "yes"))
    assert "Closed Beta" in core.replies[-1][1]

    follow_up = _message("continue", "+490000000002", "I need help")
    await core.process_incoming(follow_up)
    assert core.original_calls == [follow_up]

    repository = ClosedBetaAdmissionRepository(store)
    assert repository.is_admitted("+490000000002") is True
    assert repository.status(AdmissionPolicy(enabled=True, capacity=5)).admitted_count == 1


@pytest.mark.anyio
async def test_internal_document_text_cannot_become_beta_opt_in(tmp_path) -> None:
    store = JsonDataStore(tmp_path / "store.json")
    core = _Core(store)
    install(
        core,
        env_provider=lambda: {"CLOSED_BETA_ADMISSION_ENABLED": "true"},
    )

    await core.process_incoming(
        _message(
            "document",
            "+490000000003",
            "yes",
            internal_context="document_analysis",
        )
    )

    assert ClosedBetaAdmissionRepository(store).is_admitted("+490000000003") is False
    assert core.original_calls == []
    assert "Closed Beta" in core.replies[-1][1]


@pytest.mark.anyio
async def test_explicit_leave_releases_slot_without_deleting_other_user_data(tmp_path) -> None:
    store = JsonDataStore(tmp_path / "store.json")
    phone = "+490000000004"
    store.update_user(
        phone,
        {
            "memory_consent": "granted",
            "first_name": "Synthetic",
            "preferred_language": "en",
        },
    )
    repository = _admit(store, phone)
    core = _Core(store)
    install(core, env_provider=lambda: {})

    await core.process_incoming(_message("leave", phone, "leave beta"))

    assert repository.is_admitted(phone) is False
    assert store.get_user(phone)["first_name"] == "Synthetic"
    assert "slot was released" in core.replies[-1][1]
    assert core.original_calls == []


@pytest.mark.anyio
async def test_export_contains_identifier_free_beta_metadata(tmp_path) -> None:
    store = JsonDataStore(tmp_path / "store.json")
    phone = "+490000000005"
    store.update_user(
        phone,
        {
            "memory_consent": "granted",
            "first_name": "Synthetic",
            "preferred_language": "en",
        },
    )
    _admit(store, phone)
    core = _Core(store)
    install(core, env_provider=lambda: {})

    await core.process_incoming(_message("export", phone, "export my data"))

    reply = core.replies[-1][1]
    assert "BASE_EXPORT" in reply
    assert "Closed Beta participation data" in reply
    assert "status=active" in reply
    assert phone not in reply
    assert "phone_hash" not in reply
    assert "tenant_key" not in reply
    assert core.original_calls == []


@pytest.mark.anyio
async def test_delete_removes_beta_and_other_user_data_before_confirmation(tmp_path) -> None:
    store = JsonDataStore(tmp_path / "store.json")
    phone = "+490000000006"
    store.update_user(
        phone,
        {
            "memory_consent": "granted",
            "first_name": "Synthetic",
            "preferred_language": "en",
        },
    )
    repository = _admit(store, phone)
    core = _Core(store)
    install(core, env_provider=lambda: {})

    await core.process_incoming(_message("delete", phone, "delete my data"))

    assert store.get_user(phone) == {}
    assert repository.export_user_status(phone)["records"] == []
    assert core.replies[-1][1] == "ALL_DATA_DELETED"
    assert core.original_calls == []


@pytest.mark.parametrize(
    "text",
    [
        "delete my data",
        "Lösch meine Daten",
        "احذف بياناتي",
        "видали мої дані",
        "διαγραφή δεδομένων μου",
    ],
)
def test_delete_command_is_supported_in_all_product_languages(text: str) -> None:
    assert is_delete_request(text) is True


def test_invalid_configuration_blocks_readiness_without_exposing_values(tmp_path) -> None:
    store = JsonDataStore(tmp_path / "store.json")
    ready, state = runtime_state(
        store,
        {"CLOSED_BETA_ADMISSION_ENABLED": "synthetic-invalid-value"},
    )
    assert ready is False
    assert state == "misconfigured"
    assert "synthetic-invalid-value" not in state


def test_disabled_configuration_is_visible_but_ready(tmp_path) -> None:
    ready, state = runtime_state(JsonDataStore(tmp_path / "store.json"), {})
    assert ready is True
    assert state == "disabled"


def test_readiness_wrapper_marks_invalid_beta_configuration_not_ready(tmp_path) -> None:
    store = JsonDataStore(tmp_path / "store.json")
    core = _Core(store)
    health = SimpleNamespace(
        readiness_payload=lambda selected_store, **kwargs: (
            {"status": "ready", "components": {}},
            200,
        )
    )
    install(
        core,
        runtime_health=health,
        env_provider=lambda: {"CLOSED_BETA_ADMISSION_ENABLED": "invalid"},
    )

    payload, status = health.readiness_payload(store, version="1", model="model")
    assert status == 503
    assert payload["status"] == "not_ready"
    assert payload["components"]["closed_beta_admission"] == "misconfigured"


def test_production_entrypoint_installs_the_final_wrapper() -> None:
    source = Path("webhook_security.py").read_text(encoding="utf-8")
    assert "closed_beta_runtime_layer.install(" in source
    assert "runtime_health=runtime_health" in source
