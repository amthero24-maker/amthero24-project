"""Privacy-conscious PDF extraction for AmtHero24 document understanding."""
from __future__ import annotations

import io
import re
import unicodedata
from dataclasses import dataclass

from pypdf import PdfReader

_MAX_PDF_BYTES = 20 * 1024 * 1024
_MAX_PAGES = 30
_MAX_TEXT_CHARACTERS = 16_000


class DocumentServiceError(RuntimeError):
    """PDF processing failure with a stable user-facing category."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PdfExtraction:
    text: str
    page_count: int
    pages_read: int
    truncated: bool


def _clean_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "")
    text = "".join(
        character
        for character in text
        if unicodedata.category(character) not in {"Cf", "Cs"} or character in {"\n", "\t"}
    )
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_pdf_text(pdf_bytes: bytes) -> PdfExtraction:
    """Extract bounded text from a text-based PDF without storing the file."""
    if not pdf_bytes:
        raise DocumentServiceError("empty")
    if len(pdf_bytes) > _MAX_PDF_BYTES:
        raise DocumentServiceError("too_large")

    try:
        reader = PdfReader(io.BytesIO(pdf_bytes), strict=False)
    except Exception as exc:
        raise DocumentServiceError("invalid") from exc

    if reader.is_encrypted:
        try:
            unlocked = reader.decrypt("")
        except Exception as exc:
            raise DocumentServiceError("encrypted") from exc
        if not unlocked:
            raise DocumentServiceError("encrypted")

    page_count = len(reader.pages)
    pages_read = min(page_count, _MAX_PAGES)
    chunks: list[str] = []
    character_count = 0
    truncated = page_count > _MAX_PAGES

    for page in reader.pages[:pages_read]:
        try:
            page_text = _clean_text(page.extract_text() or "")
        except Exception:
            page_text = ""
        if not page_text:
            continue
        remaining = _MAX_TEXT_CHARACTERS - character_count
        if remaining <= 0:
            truncated = True
            break
        chunks.append(page_text[:remaining])
        character_count += len(chunks[-1])
        if len(page_text) > remaining:
            truncated = True
            break

    text = _clean_text("\n\n".join(chunks))
    if len(text) < 40:
        raise DocumentServiceError("scanned")
    return PdfExtraction(text=text, page_count=page_count, pages_read=pages_read, truncated=truncated)


def build_pdf_request(extraction: PdfExtraction, *, language: str, note: str = "") -> str:
    """Create an instruction that preserves the user's preferred reply language."""
    safe_note = " ".join((note or "").split())[:180]
    headers = {
        "ar": "اشرح هذا المستند بالعربية فقط. أعطني المعنى الأساسي، أهم موعد أو مبلغ إن وُجد، والخطوة العملية التالية.",
        "de": "Erkläre dieses Dokument auf Deutsch. Nenne die Kernaussage, wichtige Fristen oder Beträge und den nächsten praktischen Schritt.",
        "en": "Please explain this document in English. Give the main meaning, any important deadline or amount, and the next practical step.",
        "uk": "Поясни цей документ українською. Назви головний зміст, важливий термін або суму та наступний практичний крок.",
        "el": "Εξήγησε αυτό το έγγραφο στα Ελληνικά. Δώσε το βασικό νόημα, σημαντική προθεσμία ή ποσό και το επόμενο πρακτικό βήμα.",
    }
    header = headers.get(language, headers["de"])
    metadata = f"PDF pages: {extraction.page_count}; pages read: {extraction.pages_read}; truncated: {str(extraction.truncated).lower()}."
    note_line = f"User note or filename: {safe_note}\n" if safe_note else ""
    return f"{header}\n{metadata}\n{note_line}Extracted PDF text:\n{extraction.text}"
