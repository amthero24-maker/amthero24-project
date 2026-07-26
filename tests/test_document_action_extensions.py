"""Application-level Document Intelligence v3 tests."""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

import document_action_extensions
from data_store import JsonDataStore
from document_service import DocumentExtraction


def _seed_user(store: JsonDataStore) -> None:
    store.update_user("49123", {
        "memory_consent": "granted",
        "memory_consent_at": datetime.now(UTC).isoformat(),
        "memory_consent_version": "test-v1",
        "onboarding_stage": "complete",
        "preferred_language": "ar",
        "first_name": "وسام",
    })


def _install_store(store: JsonDataStore) -> None:
    document_action_extensions.core.store = store
    document_action_extensions.core._hero_memory_store = document_action_extensions.core.HeroMemory(store)
    document_action_extensions._PENDING_REPOSITORY = None


@pytest.mark.anyio
async def test_office_document_creates_only_transient_action_proposal(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    store = JsonDataStore(tmp_path / "store.json")
    _install_store(store)
    _seed_user(store)
    message = document_action_extensions.core.IncomingMessage(
        "doc-1", "49123", "jobcenter.docx", "document", "media-1",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    extraction = DocumentExtraction(
        text="Jobcenter. Bitte reichen Sie Unterlagen bis zum 10.08.2099 ein. Aktenzeichen JC/123.",
        kind="docx",
        units_total=2,
        units_read=2,
        truncated=False,
    )

    with patch.object(document_action_extensions.core, "get_media_url", new=AsyncMock(return_value="https://media.test/doc")), patch.object(
        document_action_extensions.core, "download_media_bytes", new=AsyncMock(return_value=b"docx")
    ), patch.object(document_action_extensions, "extract_docx_text", return_value=extraction):
        normalized = await document_action_extensions._normalize_office_document(message, "docx", "ar")

    assert "2029" not in normalized.text
    assert "2099-08-10" in normalized.text
    assert "نعم سجّلها" in normalized.text
    pending = document_action_extensions._repository().get("49123")
    assert pending is not None
    assert pending["topic"] == "jobcenter"
    assert pending["due_at"] == "2099-08-10"
    serialized = (tmp_path / "store.json").read_text(encoding="utf-8")
    assert "JC/123" not in serialized


@pytest.mark.anyio
async def test_user_confirmation_creates_mission_and_clears_pending_action(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    store = JsonDataStore(tmp_path / "store.json")
    _install_store(store)
    _seed_user(store)
    document_action_extensions._repository().put("49123", {
        "title": "متابعة ملف Jobcenter – Jobcenter",
        "topic": "jobcenter",
        "due_at": "2099-08-10",
        "next_step": "إرسال المستندات قبل المهلة",
        "authority": "Jobcenter",
        "source_kind": "pdf",
    })
    message = document_action_extensions.core.IncomingMessage("confirm-1", "49123", "نعم سجّلها", "text")
    store.claim_message("confirm-1", "49123", message.text)

    with patch.object(document_action_extensions.core, "send_whatsapp_message", new=AsyncMock()) as send:
        await document_action_extensions.process_incoming(message)

    missions = document_action_extensions.core._hero_memory().list_missions("49123", status="open", limit=5)
    assert len(missions) == 1
    assert missions[0]["topic"] == "jobcenter"
    assert missions[0]["due_at"] == "2099-08-10"
    assert missions[0]["next_step"] == "إرسال المستندات قبل المهلة"
    assert document_action_extensions._repository().get("49123") is None
    assert "سجّلت" in send.await_args.args[1]
    assert "ذكرني قبلها بيوم" in send.await_args.args[1]


@pytest.mark.anyio
async def test_user_decline_clears_proposal_without_creating_mission(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    store = JsonDataStore(tmp_path / "store.json")
    _install_store(store)
    _seed_user(store)
    document_action_extensions._repository().put("49123", {
        "title": "متابعة فاتورة",
        "topic": "invoice",
        "next_step": "مراجعة الفاتورة",
        "source_kind": "pdf",
    })
    message = document_action_extensions.core.IncomingMessage("decline-1", "49123", "لا شكراً", "text")
    store.claim_message("decline-1", "49123", message.text)

    with patch.object(document_action_extensions.core, "send_whatsapp_message", new=AsyncMock()) as send:
        await document_action_extensions.process_incoming(message)

    assert document_action_extensions.core._hero_memory().list_missions("49123", status="all", limit=5) == []
    assert document_action_extensions._repository().get("49123") is None
    assert "ما حفظت شي" in send.await_args.args[1]


def test_privacy_deletion_also_removes_pending_document_action(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REMINDER_ENCRYPTION_KEY", "test-secret")
    store = JsonDataStore(tmp_path / "store.json")
    _install_store(store)
    _seed_user(store)
    document_action_extensions._repository().put("49123", {
        "title": "متابعة فاتورة",
        "topic": "invoice",
        "source_kind": "pdf",
    })

    assert document_action_extensions._privacy_delete_with_pending(store, "49123") is True
    assert document_action_extensions._repository().get("49123") is None
    assert store.get_user("49123") == {}
