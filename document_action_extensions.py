"""Document Intelligence v3 composition for structured, opt-in follow-up actions."""
from __future__ import annotations

import re
from typing import Any

import application as application_layer
import document_extensions as office_layer
import privacy_engine as privacy_module
import privacy_extensions as composed
from document_action_repository import PendingDocumentRepository
from document_intelligence import analyze_document_text, prompt_facts
from document_service import (
    build_document_request,
    build_pdf_request,
    extract_docx_text,
    extract_pdf_text,
    extract_plain_text,
)

core = application_layer.core
_ORIGINAL_PROCESS_INCOMING = core.process_incoming
_ORIGINAL_PRIVACY_DELETE = composed.delete_all_user_data
_ORIGINAL_PRIVACY_CLEANUP = privacy_module.cleanup_retention
_PENDING_REPOSITORY: PendingDocumentRepository | None = None


def _repository(store: Any | None = None) -> PendingDocumentRepository:
    global _PENDING_REPOSITORY
    target = store or core.store
    if _PENDING_REPOSITORY is None or _PENDING_REPOSITORY.store is not target:
        _PENDING_REPOSITORY = PendingDocumentRepository(target)
    return _PENDING_REPOSITORY


def _normalize_command(text: str) -> str:
    value = str(text or "").casefold().strip()
    value = value.translate(str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا"}))
    value = re.sub(r"[؟،؛!?.,:;]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _confirmation(text: str) -> bool:
    normalized = _normalize_command(text)
    exact = {
        "نعم", "اي", "ايوه", "اه", "سجلها", "احفظها", "نعم سجلها", "اي سجلها",
        "ja", "ja speichern", "als aufgabe speichern", "speichern",
        "yes", "yes save it", "save it", "save as task",
        "так", "так зберегти", "збережи", "зберегти як завдання",
        "ναι", "ναι αποθηκευση", "αποθηκευσε το", "αποθηκευση ως εργασια",
    }
    return normalized in exact or any(
        phrase in normalized
        for phrase in (
            "سجلها كمهمة", "احفظها كمهمة", "ضيفها ع مهامي", "اضفها الى مهامي",
            "als aufgabe speichern", "save it as a task", "save as a task",
            "збережи як завдання", "αποθηκευσε ως εργασια",
        )
    )


def _decline(text: str) -> bool:
    normalized = _normalize_command(text)
    return normalized in {
        "لا", "لا شكرا", "مو لازم", "مش لازم", "لا تسجلها", "تجاهلها",
        "nein", "nein danke", "nicht speichern", "abbrechen",
        "no", "no thanks", "do not save", "cancel",
        "ні", "не зберігати", "скасувати",
        "οχι", "οχι ευχαριστω", "μην το αποθηκευσεις", "ακυρωση",
    }


def _saved_message(language: str, mission: dict[str, Any]) -> str:
    title = str(mission.get("title") or "")
    due = str(mission.get("due_at") or "")
    if language == "ar":
        suffix = f" والمهلة {due}" if due else ""
        reminder = "\nإذا بدك، قلّي «ذكرني قبلها بيوم»." if due else ""
        return f"تم ✅ سجّلت «{title}» كمهمة للمتابعة{suffix}.{reminder}"
    if language == "de":
        suffix = f" mit Frist {due}" if due else ""
        reminder = "\nDu kannst schreiben: „Erinnere mich einen Tag vorher“." if due else ""
        return f"Erledigt ✅ „{title}“ wurde als Aufgabe gespeichert{suffix}.{reminder}"
    if language == "en":
        suffix = f" with deadline {due}" if due else ""
        reminder = "\nYou can say: “Remind me one day before”." if due else ""
        return f"Done ✅ “{title}” was saved as a follow-up task{suffix}.{reminder}"
    if language == "uk":
        suffix = f" із терміном {due}" if due else ""
        reminder = "\nМожеш написати: «Нагадай за один день»." if due else ""
        return f"Готово ✅ «{title}» збережено як завдання{suffix}.{reminder}"
    suffix = f" με προθεσμία {due}" if due else ""
    reminder = "\nΜπορείς να γράψεις: «Θύμισέ μου μία ημέρα πριν»." if due else ""
    return f"Έγινε ✅ Το «{title}» αποθηκεύτηκε ως εργασία{suffix}.{reminder}"


def _declined_message(language: str) -> str:
    return {
        "ar": "تمام، ما حفظت شي من المستند. الشرح بيضل ضمن هالمحادثة فقط.",
        "de": "Alles klar, aus dem Dokument wurde keine Aufgabe gespeichert.",
        "en": "Understood. Nothing from the document was saved as a task.",
        "uk": "Добре. З документа нічого не збережено як завдання.",
        "el": "Εντάξει. Δεν αποθηκεύτηκε εργασία από το έγγραφο.",
    }.get(language, "Nothing was saved.")


def _preferred_language(profile: dict[str, Any]) -> str:
    language = str(
        profile.get("preferred_language")
        if profile.get("memory_consent") == "granted"
        else profile.get("session_language") or profile.get("preferred_language") or "de"
    )
    return language if language in {"de", "ar", "en", "uk", "el"} else "de"


async def _extract_pdf_message(message: core.IncomingMessage, language: str) -> core.IncomingMessage:
    media_url = await core.get_media_url(str(message.media_id))
    pdf_bytes = await core.download_media_bytes(media_url)
    extraction = await core.anyio.to_thread.run_sync(lambda: extract_pdf_text(pdf_bytes))
    analysis = analyze_document_text(extraction.text, language=language, source_kind="pdf")
    _repository().put(message.sender, analysis.pending_action())
    request = build_pdf_request(extraction, language=language, note=message.text)
    request += "\n\n" + prompt_facts(analysis, language=language)
    return core.IncomingMessage(message.message_id, message.sender, request, "text")


async def _normalize_office_document(
    message: core.IncomingMessage,
    kind: str,
    language: str,
) -> core.IncomingMessage:
    media_url = await core.get_media_url(str(message.media_id))
    content = await core.download_media_bytes(media_url)
    if kind == "docx":
        extraction = await core.anyio.to_thread.run_sync(lambda: extract_docx_text(content))
    else:
        extraction = await core.anyio.to_thread.run_sync(lambda: extract_plain_text(content))
    analysis = analyze_document_text(extraction.text, language=language, source_kind=kind)
    _repository().put(message.sender, analysis.pending_action())
    request = build_document_request(extraction, language=language, note=message.text)
    request += "\n\n" + prompt_facts(analysis, language=language)
    return core.IncomingMessage(message.message_id, message.sender, request, "text")


async def process_incoming(message: core.IncomingMessage) -> None:
    if message.message_type == "text":
        profile = core.store.get_user(message.sender)
        pending = _repository().get(message.sender)
        stage = str(profile.get("onboarding_stage") or "")
        if pending and stage == "complete" and (_confirmation(message.text) or _decline(message.text)):
            language = _preferred_language(profile)
            if _decline(message.text):
                _repository().delete(message.sender)
                await core._finish(message.message_id, _declined_message(language), message.sender)
                return
            if profile.get("memory_consent") != "granted":
                await core._finish(message.message_id, core.memory_required_message(language), message.sender)
                return
            mission = core._hero_memory().create_mission(
                message.sender,
                title=str(pending.get("title") or "Document follow-up"),
                topic=str(pending.get("topic") or "document"),
                next_step=str(pending.get("next_step") or ""),
                due_at=str(pending.get("due_at") or "") or None,
                metadata={
                    "source": str(pending.get("source_kind") or "document"),
                    "language": language,
                    "category": str(pending.get("topic") or "document"),
                },
            )
            _repository().delete(message.sender)
            await core._finish(message.message_id, _saved_message(language, mission), message.sender)
            return
    await _ORIGINAL_PROCESS_INCOMING(message)


def _privacy_delete_with_pending(store: Any, phone: str) -> bool:
    pending_deleted = _repository(store).delete(phone)
    return bool(_ORIGINAL_PRIVACY_DELETE(store, phone) or pending_deleted)


def _privacy_cleanup_with_pending(store: Any, **kwargs: Any) -> dict[str, int]:
    result = _ORIGINAL_PRIVACY_CLEANUP(store, **kwargs)
    result["pending_documents"] = _repository(store).cleanup_expired(now=kwargs.get("now"))
    return result


# Patch extraction points after all lower document layers are loaded.
application_layer._extract_pdf_message = _extract_pdf_message
office_layer._normalize_document = _normalize_office_document
composed.delete_all_user_data = _privacy_delete_with_pending
privacy_module.cleanup_retention = _privacy_cleanup_with_pending
core.process_incoming = process_incoming

app = composed.app
store = composed.store
