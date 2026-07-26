"""Document AI extraction tests."""
from __future__ import annotations

import io

import pytest
from pypdf import PdfWriter

from document_service import DocumentServiceError, PdfExtraction, build_pdf_request, extract_pdf_text


def _pdf_with_text(text: str) -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=300)
    # pypdf cannot author page text directly; attach a metadata field to keep this
    # helper focused on validating PDF structure. Text extraction behavior is
    # tested through a patched reader below.
    writer.add_metadata({"/Subject": text})
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_empty_and_invalid_pdf_are_rejected() -> None:
    with pytest.raises(DocumentServiceError, match="empty"):
        extract_pdf_text(b"")
    with pytest.raises(DocumentServiceError, match="invalid"):
        extract_pdf_text(b"not-a-pdf")


def test_scanned_or_blank_pdf_requests_clearer_input() -> None:
    with pytest.raises(DocumentServiceError, match="scanned"):
        extract_pdf_text(_pdf_with_text("blank"))


def test_extraction_is_clean_bounded_and_reports_metadata(monkeypatch) -> None:
    class Page:
        def extract_text(self) -> str:
            return "  Mahnung   vom Jobcenter\n\n\nFrist: 10.08.2026  "

    class Reader:
        is_encrypted = False
        pages = [Page(), Page()]

    monkeypatch.setattr("document_service.PdfReader", lambda *_args, **_kwargs: Reader())
    result = extract_pdf_text(b"%PDF-test")

    assert result.page_count == 2
    assert result.pages_read == 2
    assert "Mahnung vom Jobcenter" in result.text
    assert "Frist: 10.08.2026" in result.text
    assert result.truncated is False


def test_pdf_request_uses_user_language_and_document_text() -> None:
    extraction = PdfExtraction(
        text="Rechnung 123. Frist 10.08.2026.",
        page_count=3,
        pages_read=3,
        truncated=False,
    )
    prompt = build_pdf_request(extraction, language="ar", note="rechnung.pdf")

    assert "بالعربية فقط" in prompt
    assert "rechnung.pdf" in prompt
    assert "Rechnung 123" in prompt
    assert "PDF pages: 3" in prompt
