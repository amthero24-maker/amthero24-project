"""Deterministic grounding for high-confidence pasted German invoice text.

This boundary handles only a narrow, structured invoice shape. It performs no
persistence, provider call, logging, payment action, or external mutation.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_SUPPORTED_LANGUAGES = {"de", "ar", "en", "uk", "el"}
_FIELD_LABEL = re.compile(
    r"^(?:datum|betreff|kundennummer|kunden-nr\.?|betrag|zahlungsfrist|"
    r"fällig(?:keit)?|faellig(?:keit)?|verwendungszweck|zahlungsreferenz|iban)\s*:",
    re.IGNORECASE,
)
_ORGANIZATION_SUFFIX = re.compile(
    r"\b(?:gmbh|ag|ug(?:\s*\(haftungsbeschränkt\))?|kg|ohg|gbr|se|e\.?\s*v\.?)\b",
    re.IGNORECASE,
)
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:[\s-]?[A-Z0-9]){11,30}\b", re.IGNORECASE)


@dataclass(frozen=True)
class PastedInvoiceFacts:
    sender: str
    document_date: str
    subject: str
    customer_number: str
    amount: str
    deadline: str
    payment_purpose: str
    payment_requested: bool
    proof_requested_if_paid: bool
    bank_details_present: bool
    customer_number_explicitly_assigned_as_purpose: bool


def _normalized_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return "".join(
        character
        for character in text
        if character == "\n" or unicodedata.category(character) not in {"Cf", "Cs"}
    )


def _clean_value(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip(" \t*_-–—")


def _field(text: str, labels: str) -> str:
    match = re.search(
        rf"(?im)^\s*(?:{labels})\s*:\s*([^\n\r]+?)\s*$",
        text,
    )
    return _clean_value(match.group(1)) if match else ""


def _sender(text: str) -> str:
    lines = [_clean_value(line) for line in text.splitlines()]
    field_index = next(
        (index for index, line in enumerate(lines) if _FIELD_LABEL.match(line)),
        -1,
    )
    if field_index <= 0:
        return ""
    for index in range(field_index - 1, max(-1, field_index - 6), -1):
        candidate = lines[index]
        if not candidate or ":" in candidate or len(candidate) > 120:
            continue
        if candidate.endswith(("?", "؟", ".", "!", "؛")):
            continue
        if _ORGANIZATION_SUFFIX.search(candidate):
            return candidate
    return ""


def extract_pasted_invoice_facts(text: str) -> PastedInvoiceFacts | None:
    """Return facts only for a strict, high-confidence pasted invoice shape."""
    normalized = _normalized_text(text)
    lowered = normalized.casefold()
    sender = _sender(normalized)
    document_date = _field(normalized, r"datum")
    subject = _field(normalized, r"betreff")
    customer_number = _field(normalized, r"kundennummer|kunden-nr\.?")
    amount = _field(normalized, r"betrag")
    deadline = _field(
        normalized,
        r"zahlungsfrist|fällig(?:keit)?|faellig(?:keit)?",
    )
    payment_purpose = _field(
        normalized,
        r"verwendungszweck|zahlungsreferenz",
    )
    payment_requested = bool(
        re.search(r"\b(?:überweisen|ueberweisen|zahlen|begleichen)\b", lowered)
    )
    proof_requested_if_paid = bool(
        "zahlungsnachweis" in lowered
        and re.search(r"\b(?:bereits\s+bezahlt|schon\s+bezahlt)\b", lowered)
    )
    bank_details_present = bool(
        _IBAN.search(normalized)
        or re.search(r"\bbankverbindung\b", lowered)
    )
    customer_number_explicitly_assigned_as_purpose = bool(
        re.search(
            r"(?:im|als)\s+verwendungszweck[^\n.]{0,100}\bkundennummer\b"
            r"|\bkundennummer\b[^\n.]{0,100}(?:im|als)\s+verwendungszweck"
            r"|\bunter\s+angabe\s+der\s+kundennummer\b",
            lowered,
        )
    )

    field_count = sum(
        bool(value)
        for value in (
            document_date,
            subject,
            customer_number,
            amount,
            deadline,
        )
    )
    invoice_subject = bool(
        subject
        and re.search(
            r"\b(?:rechnung|mahnung|zahlungsaufforderung|offener?\s+betrag)\b",
            subject.casefold(),
        )
    ) or bool(
        re.search(
            r"\b(?:offene\s+rechnung|offener\s+betrag|zahlungsaufforderung)\b",
            lowered,
        )
    )
    if not (
        sender
        and amount
        and deadline
        and payment_requested
        and invoice_subject
        and field_count >= 4
    ):
        return None

    return PastedInvoiceFacts(
        sender=sender,
        document_date=document_date,
        subject=subject,
        customer_number=customer_number,
        amount=amount,
        deadline=deadline,
        payment_purpose=payment_purpose,
        payment_requested=payment_requested,
        proof_requested_if_paid=proof_requested_if_paid,
        bank_details_present=bank_details_present,
        customer_number_explicitly_assigned_as_purpose=(
            customer_number_explicitly_assigned_as_purpose
        ),
    )


def _arabic_reply(facts: PastedInvoiceFacts) -> str:
    lines = [
        f"هاي رسالة من {facts.sender} بخصوص فاتورة مفتوحة.",
        "",
        "المعلومات المهمة:",
        f"• المبلغ: {facts.amount}",
        f"• آخر موعد للدفع: {facts.deadline}",
    ]
    if facts.document_date:
        lines.append(f"• تاريخ الرسالة: {facts.document_date}")
    if facts.subject:
        lines.append(f"• الموضوع: {facts.subject}")
    if facts.customer_number:
        lines.append(f"• رقم العميل: {facts.customer_number}")

    lines.extend(["", "المطلوب منك:"])
    lines.append(
        f"• إذا ما دفعت بعد: ادفع المبلغ قبل {facts.deadline} باستخدام بيانات الدفع الصحيحة الموجودة في الفاتورة الكاملة."
    )
    if facts.proof_requested_if_paid:
        lines.append(
            f"• إذا كنت دافع: أرسل إثبات الدفع إلى {facts.sender}."
        )

    if facts.payment_purpose:
        lines.extend([
            "",
            f"غرض التحويل المذكور بالنص هو: {facts.payment_purpose}.",
        ])
        if not facts.bank_details_present:
            lines.append(
                "بيانات الحساب غير ظاهرة بالنص المرسل؛ خذها من الفاتورة الكاملة أو ابعت الصفحة اللي فيها تفاصيل الدفع."
            )
    elif (
        facts.customer_number
        and facts.customer_number_explicitly_assigned_as_purpose
    ):
        lines.extend([
            "",
            f"النص يطلب استخدام رقم العميل {facts.customer_number} كغرض للتحويل.",
        ])
        if not facts.bank_details_present:
            lines.append(
                "بيانات الحساب غير ظاهرة بالنص المرسل؛ خذها من الفاتورة الكاملة أو ابعت الصفحة اللي فيها تفاصيل الدفع."
            )
    else:
        lines.append("")
        if facts.customer_number:
            lines.append(
                f"مهم: {facts.customer_number} ظاهر كرقم عميل فقط، والنص ما بيطلب استخدامه كمرجع أو غرض للتحويل."
            )
        missing = []
        if not facts.bank_details_present:
            missing.append("بيانات الحساب")
        missing.append("غرض التحويل")
        lines.append(
            f"{' و'.join(missing)} غير ظاهرين بالنص المرسل؛ خذهم من الفاتورة الكاملة أو ابعت الصفحة اللي فيها تفاصيل الدفع."
        )
    return "\n".join(lines)


def _german_reply(facts: PastedInvoiceFacts) -> str:
    lines = [
        f"Das Schreiben stammt von {facts.sender} und betrifft eine offene Rechnung.",
        "",
        "Wichtige Angaben:",
        f"• Betrag: {facts.amount}",
        f"• Zahlungsfrist: {facts.deadline}",
    ]
    if facts.document_date:
        lines.append(f"• Schreiben vom: {facts.document_date}")
    if facts.subject:
        lines.append(f"• Betreff: {facts.subject}")
    if facts.customer_number:
        lines.append(f"• Kundennummer: {facts.customer_number}")

    lines.extend([
        "",
        "Was zu tun ist:",
        f"• Falls noch nicht bezahlt: den Betrag bis {facts.deadline} mit den korrekten Zahlungsdaten aus der vollständigen Rechnung überweisen.",
    ])
    if facts.proof_requested_if_paid:
        lines.append(
            f"• Falls bereits bezahlt: den Zahlungsnachweis an {facts.sender} senden."
        )

    if facts.payment_purpose:
        lines.extend(["", f"Angegebener Verwendungszweck: {facts.payment_purpose}."])
        if not facts.bank_details_present:
            lines.append(
                "Die Bankverbindung fehlt im übermittelten Ausschnitt. Nutze die vollständige Rechnung oder sende die Seite mit den Zahlungsdetails."
            )
    elif facts.customer_number and facts.customer_number_explicitly_assigned_as_purpose:
        lines.extend([
            "",
            f"Der Text weist ausdrücklich an, die Kundennummer {facts.customer_number} als Verwendungszweck zu verwenden.",
        ])
        if not facts.bank_details_present:
            lines.append(
                "Die Bankverbindung fehlt im übermittelten Ausschnitt. Nutze die vollständige Rechnung oder sende die Seite mit den Zahlungsdetails."
            )
    else:
        lines.append("")
        if facts.customer_number:
            lines.append(
                f"Wichtig: {facts.customer_number} ist im Text nur als Kundennummer bezeichnet; sie wird nicht als Verwendungszweck angewiesen."
            )
        missing = []
        if not facts.bank_details_present:
            missing.append("Bankverbindung")
        missing.append("Verwendungszweck")
        lines.append(
            f"{' und '.join(missing)} fehlen im übermittelten Ausschnitt. Nutze die vollständige Rechnung oder sende die Seite mit den Zahlungsdetails."
        )
    return "\n".join(lines)


def _english_reply(facts: PastedInvoiceFacts) -> str:
    lines = [
        f"This letter is from {facts.sender} and concerns an unpaid invoice.",
        "",
        "Key details:",
        f"• Amount: {facts.amount}",
        f"• Payment deadline: {facts.deadline}",
    ]
    if facts.document_date:
        lines.append(f"• Letter date: {facts.document_date}")
    if facts.subject:
        lines.append(f"• Subject: {facts.subject}")
    if facts.customer_number:
        lines.append(f"• Customer number: {facts.customer_number}")

    lines.extend([
        "",
        "What you need to do:",
        f"• If you have not paid: pay by {facts.deadline} using the correct payment details from the complete invoice.",
    ])
    if facts.proof_requested_if_paid:
        lines.append(
            f"• If you already paid: send proof of payment to {facts.sender}."
        )

    if facts.payment_purpose:
        lines.extend(["", f"The stated payment reference is: {facts.payment_purpose}."])
        if not facts.bank_details_present:
            lines.append(
                "The bank details are not visible in the supplied text. Use the complete invoice or send the page containing the payment details."
            )
    elif facts.customer_number and facts.customer_number_explicitly_assigned_as_purpose:
        lines.extend([
            "",
            f"The text explicitly instructs you to use customer number {facts.customer_number} as the payment reference.",
        ])
        if not facts.bank_details_present:
            lines.append(
                "The bank details are not visible in the supplied text. Use the complete invoice or send the page containing the payment details."
            )
    else:
        lines.append("")
        if facts.customer_number:
            lines.append(
                f"Important: {facts.customer_number} is shown only as a customer number; the text does not instruct you to use it as the payment reference."
            )
        missing = []
        if not facts.bank_details_present:
            missing.append("bank details")
        missing.append("payment reference")
        lines.append(
            f"The {' and '.join(missing)} are not visible in the supplied text. Use the complete invoice or send the page containing the payment details."
        )
    return "\n".join(lines)


def _ukrainian_reply(facts: PastedInvoiceFacts) -> str:
    lines = [
        f"Цей лист надійшов від {facts.sender} і стосується неоплаченого рахунку.",
        "",
        "Основні дані:",
        f"• Сума: {facts.amount}",
        f"• Строк оплати: {facts.deadline}",
    ]
    if facts.document_date:
        lines.append(f"• Дата листа: {facts.document_date}")
    if facts.subject:
        lines.append(f"• Тема: {facts.subject}")
    if facts.customer_number:
        lines.append(f"• Номер клієнта: {facts.customer_number}")
    lines.extend([
        "",
        "Що потрібно зробити:",
        f"• Якщо ще не сплачено: сплатити до {facts.deadline}, використовуючи правильні реквізити з повного рахунку.",
    ])
    if facts.proof_requested_if_paid:
        lines.append(
            f"• Якщо вже сплачено: надіслати підтвердження оплати до {facts.sender}."
        )
    if facts.payment_purpose:
        lines.extend(["", f"Вказане призначення платежу: {facts.payment_purpose}."])
        if not facts.bank_details_present:
            lines.append(
                "У надісланому уривку немає банківських реквізитів. Скористайся повним рахунком або надішли сторінку з платіжними даними."
            )
    elif facts.customer_number and facts.customer_number_explicitly_assigned_as_purpose:
        lines.extend([
            "",
            f"Текст прямо вимагає використати номер клієнта {facts.customer_number} як призначення платежу.",
        ])
        if not facts.bank_details_present:
            lines.append(
                "У надісланому уривку немає банківських реквізитів. Скористайся повним рахунком або надішли сторінку з платіжними даними."
            )
    else:
        lines.append("")
        if facts.customer_number:
            lines.append(
                f"Важливо: {facts.customer_number} вказано лише як номер клієнта; текст не вимагає використовувати його як призначення платежу."
            )
        missing = []
        if not facts.bank_details_present:
            missing.append("банківські реквізити")
        missing.append("призначення платежу")
        lines.append(
            f"У надісланому уривку відсутні {' та '.join(missing)}. Скористайся повним рахунком або надішли сторінку з платіжними даними."
        )
    return "\n".join(lines)


def _greek_reply(facts: PastedInvoiceFacts) -> str:
    lines = [
        f"Η επιστολή προέρχεται από την {facts.sender} και αφορά ανεξόφλητο τιμολόγιο.",
        "",
        "Βασικά στοιχεία:",
        f"• Ποσό: {facts.amount}",
        f"• Προθεσμία πληρωμής: {facts.deadline}",
    ]
    if facts.document_date:
        lines.append(f"• Ημερομηνία επιστολής: {facts.document_date}")
    if facts.subject:
        lines.append(f"• Θέμα: {facts.subject}")
    if facts.customer_number:
        lines.append(f"• Αριθμός πελάτη: {facts.customer_number}")
    lines.extend([
        "",
        "Τι χρειάζεται να κάνεις:",
        f"• Αν δεν έχεις πληρώσει: πλήρωσε έως {facts.deadline} με τα σωστά στοιχεία από το πλήρες τιμολόγιο.",
    ])
    if facts.proof_requested_if_paid:
        lines.append(
            f"• Αν έχεις ήδη πληρώσει: στείλε την απόδειξη πληρωμής στην {facts.sender}."
        )
    if facts.payment_purpose:
        lines.extend(["", f"Η αναγραφόμενη αιτιολογία πληρωμής είναι: {facts.payment_purpose}."])
        if not facts.bank_details_present:
            lines.append(
                "Τα τραπεζικά στοιχεία δεν εμφανίζονται στο απόσπασμα. Χρησιμοποίησε το πλήρες τιμολόγιο ή στείλε τη σελίδα με τα στοιχεία πληρωμής."
            )
    elif facts.customer_number and facts.customer_number_explicitly_assigned_as_purpose:
        lines.extend([
            "",
            f"Το κείμενο ζητά ρητά να χρησιμοποιηθεί ο αριθμός πελάτη {facts.customer_number} ως αιτιολογία πληρωμής.",
        ])
        if not facts.bank_details_present:
            lines.append(
                "Τα τραπεζικά στοιχεία δεν εμφανίζονται στο απόσπασμα. Χρησιμοποίησε το πλήρες τιμολόγιο ή στείλε τη σελίδα με τα στοιχεία πληρωμής."
            )
    else:
        lines.append("")
        if facts.customer_number:
            lines.append(
                f"Σημαντικό: το {facts.customer_number} εμφανίζεται μόνο ως αριθμός πελάτη· το κείμενο δεν ζητά να χρησιμοποιηθεί ως αιτιολογία πληρωμής."
            )
        missing = []
        if not facts.bank_details_present:
            missing.append("τραπεζικά στοιχεία")
        missing.append("αιτιολογία πληρωμής")
        lines.append(
            f"Στο απόσπασμα λείπουν {' και '.join(missing)}. Χρησιμοποίησε το πλήρες τιμολόγιο ή στείλε τη σελίδα με τα στοιχεία πληρωμής."
        )
    return "\n".join(lines)


def grounded_pasted_invoice_reply(text: str, *, language: str) -> str | None:
    """Render a bounded answer or return None when confidence is insufficient."""
    facts = extract_pasted_invoice_facts(text)
    if facts is None:
        return None
    lang = language if language in _SUPPORTED_LANGUAGES else "de"
    return {
        "ar": _arabic_reply,
        "de": _german_reply,
        "en": _english_reply,
        "uk": _ukrainian_reply,
        "el": _greek_reply,
    }[lang](facts)
