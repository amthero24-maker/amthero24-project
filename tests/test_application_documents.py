"""Application-level PDF document tests."""
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

import application
from data_store import JsonDataStore
from document_service import DocumentServiceError, PdfExtraction


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


@pytest.mark.anyio
async def test_text_pdf_is_explained_without_retaining_raw_content(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    application.core.store = JsonDataStore(tmp_path / "store.json")
    application.core._hero_memory_store = application.core.HeroMemory(application.core.store)
    _seed_user(application.core.store)
    message = application.core.IncomingMessage(
        "pdf-ok", "49123", "rechnung.pdf", "document", "pdf-media", "application/pdf"
    )
    application.core.store.claim_message(
        "pdf-ok", "49123", "rechnung.pdf", message_type="document", media_id="pdf-media"
    )
    extraction = PdfExtraction(
        text="IBAN DE001234567890. Rechnung 20251478. Bitte zahlen Sie 40 Euro bis 10.08.2026.",
        page_count=2,
        pages_read=2,
        truncated=False,
    )

    with patch.object(application.core, "get_media_url", new=AsyncMock(return_value="https://media.test/pdf")), patch.object(
        application.core, "download_media_bytes", new=AsyncMock(return_value=b"%PDF")
    ), patch.object(application, "extract_pdf_text", return_value=extraction), patch.object(
        application.core, "generate_reply", return_value="المستند يطلب دفع 40 يورو قبل 10.08.2026. الخطوة التالية: تأكد من صحة الفاتورة."
    ), patch.object(application.core, "send_whatsapp_message", new=AsyncMock()) as send:
        await application.process_incoming(message)

    assert "40 يورو" in send.await_args.args[1]
    profile = application.core.store.get_user("49123")
    assert profile["current_topic"] == "document"
    assert profile["last_message"] == "PDF document processed transiently"
    assert "DE001234567890" not in profile["conversation_summary"]
    assert "PDF content processed transiently" in profile["conversation_summary"]
    assert application.core.store.snapshot()["messages"]["pdf-ok"]["status"] == "sent"


@pytest.mark.anyio
async def test_scanned_pdf_returns_clear_localized_instruction(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    application.core.store = JsonDataStore(tmp_path / "store.json")
    application.core._hero_memory_store = application.core.HeroMemory(application.core.store)
    _seed_user(application.core.store)
    message = application.core.IncomingMessage(
        "pdf-scan", "49123", "scan.pdf", "document", "pdf-scan-media", "application/pdf"
    )
    application.core.store.claim_message(
        "pdf-scan", "49123", "scan.pdf", message_type="document", media_id="pdf-scan-media"
    )

    with patch.object(application.core, "get_media_url", new=AsyncMock(return_value="https://media.test/scan")), patch.object(
        application.core, "download_media_bytes", new=AsyncMock(return_value=b"%PDF")
    ), patch.object(application, "extract_pdf_text", side_effect=DocumentServiceError("scanned")), patch.object(
        application.core, "send_whatsapp_message", new=AsyncMock()
    ) as send:
        await application.process_incoming(message)

    assert "ابعت الصفحات المهمة كصور" in send.await_args.args[1]
    assert application.core.store.snapshot()["messages"]["pdf-scan"]["status"] == "failed"
