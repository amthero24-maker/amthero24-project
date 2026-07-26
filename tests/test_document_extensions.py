"""Application-level Word and text document tests."""
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

import document_extensions
from data_store import JsonDataStore
from document_service import DocumentExtraction, DocumentServiceError


core = document_extensions.core


def _seed_user(store: JsonDataStore) -> None:
    store.update_user("49123", {
        "memory_consent": "granted",
        "memory_consent_at": datetime.now(UTC).isoformat(),
        "memory_consent_version": "test-v1",
        "onboarding_stage": "complete",
        "intro_sent_at": datetime.now(UTC).isoformat(),
        "preferred_language": "ar",
        "first_name": "وسام",
    })


def test_document_kind_uses_mime_and_filename_fallback() -> None:
    assert document_extensions._kind(core.IncomingMessage("1", "491", "file.docx", "document", "m", "application/octet-stream")) == "docx"
    assert document_extensions._kind(core.IncomingMessage("2", "491", "file.txt", "document", "m", "application/octet-stream")) == "text"
    assert document_extensions._kind(core.IncomingMessage("3", "491", "file.doc", "document", "m", "application/msword")) == "legacy_word"


@pytest.mark.anyio
async def test_docx_is_explained_without_persisting_content(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    core.store = JsonDataStore(tmp_path / "store.json")
    document_extensions.composed.core.store = core.store
    document_extensions.composed.store = core.store
    core._hero_memory_store = core.HeroMemory(core.store)
    _seed_user(core.store)
    message = core.IncomingMessage(
        "docx-ok", "49123", "jobcenter.docx", "document", "docx-media", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    core.store.claim_message("docx-ok", "49123", message.text, message_type="document", media_id="docx-media")
    extraction = DocumentExtraction(
        text="IBAN DE001234567. Bitte zahlen Sie 40 Euro bis 10.08.2026.",
        kind="docx",
        units_total=4,
        units_read=4,
        truncated=False,
    )

    with patch.object(core, "get_media_url", new=AsyncMock(return_value="https://media.test/docx")), patch.object(
        core, "download_media_bytes", new=AsyncMock(return_value=b"docx")
    ), patch.object(document_extensions, "extract_docx_text", return_value=extraction), patch.object(
        core, "generate_reply", return_value="الملف يطلب دفع 40 يورو قبل 10.08.2026."
    ), patch.object(core, "send_whatsapp_message", new=AsyncMock()) as send:
        await document_extensions.process_incoming(message)

    assert "40 يورو" in send.await_args.args[1]
    profile = core.store.get_user("49123")
    assert profile["last_message"] == "DOCX document processed transiently"
    assert "DE001234567" not in profile["conversation_summary"]
    assert core.store.snapshot()["messages"]["docx-ok"]["status"] == "sent"


@pytest.mark.anyio
async def test_legacy_doc_receives_conversion_instruction(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    core.store = JsonDataStore(tmp_path / "store.json")
    document_extensions.composed.core.store = core.store
    document_extensions.composed.store = core.store
    core._hero_memory_store = core.HeroMemory(core.store)
    _seed_user(core.store)
    message = core.IncomingMessage("old-doc", "49123", "legacy.doc", "document", "old-media", "application/msword")
    core.store.claim_message("old-doc", "49123", message.text, message_type="document", media_id="old-media")

    with patch.object(core, "send_whatsapp_message", new=AsyncMock()) as send:
        await document_extensions.process_incoming(message)

    assert ".docx أو PDF" in send.await_args.args[1]
    assert core.store.snapshot()["messages"]["old-doc"]["status"] == "failed"


@pytest.mark.anyio
async def test_unsafe_docx_fails_locally(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    core.store = JsonDataStore(tmp_path / "store.json")
    document_extensions.composed.core.store = core.store
    document_extensions.composed.store = core.store
    core._hero_memory_store = core.HeroMemory(core.store)
    _seed_user(core.store)
    message = core.IncomingMessage(
        "unsafe-docx", "49123", "unsafe.docx", "document", "unsafe-media", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    core.store.claim_message("unsafe-docx", "49123", message.text, message_type="document", media_id="unsafe-media")

    with patch.object(core, "get_media_url", new=AsyncMock(return_value="https://media.test/unsafe")), patch.object(
        core, "download_media_bytes", new=AsyncMock(return_value=b"unsafe")
    ), patch.object(document_extensions, "extract_docx_text", side_effect=DocumentServiceError("unsafe_archive")), patch.object(
        core, "send_whatsapp_message", new=AsyncMock()
    ) as send:
        await document_extensions.process_incoming(message)

    assert "بنيته غير آمنة" in send.await_args.args[1]
    assert core.store.snapshot()["messages"]["unsafe-docx"]["status"] == "failed"
