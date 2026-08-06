"""Idempotently manage the approved WhatsApp reminder utility template.

The command is read-only unless ``--apply`` is supplied.  It deliberately emits
only bounded template metadata and never prints credentials or API responses.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from typing import Mapping

import httpx

TEMPLATE_NAME = "amthero24_reminder_v1"
_GRAPH_VERSION = re.compile(r"^v\d+\.\d+$")
_SAFE_STATUS = {"APPROVED", "PENDING", "REJECTED", "PAUSED", "DISABLED", "IN_APPEAL"}


@dataclass(frozen=True)
class TemplateVariant:
    language: str
    body: str
    example: tuple[str, str, str]


VARIANTS = (
    TemplateVariant(
        "ar",
        "مرحبًا {{1}}، تذكير من AmtHero24: لديك متابعة بخصوص «{{2}}» بتاريخ {{3}}. افتح المحادثة لمتابعة الخطوة التالية.",
        ("سام", "موعد الدائرة", "10.08.2026"),
    ),
    TemplateVariant(
        "de",
        "Hallo {{1}}, Erinnerung von AmtHero24: Du hast eine Aufgabe zu „{{2}}“ am {{3}}. Öffne den Chat, um mit dem nächsten Schritt weiterzumachen.",
        ("Sam", "Behördentermin", "10.08.2026"),
    ),
    TemplateVariant(
        "en_US",
        "Hello {{1}}, a reminder from AmtHero24: you have a follow-up for “{{2}}” on {{3}}. Open the chat to continue with the next step.",
        ("Sam", "office appointment", "10.08.2026"),
    ),
    TemplateVariant(
        "uk",
        "Вітаю, {{1}}! Нагадування від AmtHero24: у вас запланована справа «{{2}}» на {{3}}. Відкрийте чат, щоб перейти до наступного кроку.",
        ("Сам", "зустріч в установі", "10.08.2026"),
    ),
    TemplateVariant(
        "el",
        "Γεια σου {{1}}, υπενθύμιση από το AmtHero24: έχεις συνέχεια για «{{2}}» στις {{3}}. Άνοιξε τη συνομιλία για το επόμενο βήμα.",
        ("Sam", "ραντεβού στην υπηρεσία", "10.08.2026"),
    ),
)


class TemplateManagementError(RuntimeError):
    """Stable template-management failure without provider response detail."""


def _configuration(environment: Mapping[str, str]) -> tuple[str, str, str]:
    waba_id = str(environment.get("WABA_ID", "")).strip()
    token = str(environment.get("WHATSAPP_TOKEN", "")).strip()
    version = str(environment.get("WHATSAPP_API_VERSION", "v22.0")).strip()
    if not waba_id or not token:
        raise TemplateManagementError("missing_meta_configuration")
    if not _GRAPH_VERSION.fullmatch(version):
        raise TemplateManagementError("invalid_graph_version")
    return waba_id, token, version


def _payload(variant: TemplateVariant) -> dict[str, object]:
    return {
        "name": TEMPLATE_NAME,
        "language": variant.language,
        "category": "UTILITY",
        "components": [{
            "type": "BODY",
            "text": variant.body,
            "example": {"body_text": [list(variant.example)]},
        }],
    }


def _bounded_status(value: object) -> str:
    status = str(value or "UNKNOWN").strip().upper()
    return status if status in _SAFE_STATUS else "UNKNOWN"


def manage_templates(
    *,
    apply: bool = False,
    environment: Mapping[str, str] | None = None,
    client: httpx.Client | None = None,
) -> dict[str, object]:
    """Inspect variants and optionally submit only missing languages."""
    waba_id, token, version = _configuration(environment or os.environ)
    owns_client = client is None
    active_client = client or httpx.Client(timeout=20.0)
    base_url = f"https://graph.facebook.com/{version}/{waba_id}/message_templates"
    try:
        response = active_client.get(
            base_url,
            headers={"Authorization": f"Bearer {token}"},
            params={"name": TEMPLATE_NAME, "fields": "id,name,status,language,category", "limit": 100},
        )
        response.raise_for_status()
        data = response.json()
        existing = {
            str(item.get("language") or ""): {
                "status": _bounded_status(item.get("status")),
                "template_id": str(item.get("id") or "")[:40],
            }
            for item in data.get("data", [])
            if isinstance(item, dict) and item.get("name") == TEMPLATE_NAME
        }
        results: dict[str, dict[str, str]] = {}
        for variant in VARIANTS:
            if variant.language in existing:
                results[variant.language] = {"action": "existing", **existing[variant.language]}
                continue
            if not apply:
                results[variant.language] = {"action": "missing", "status": "MISSING", "template_id": ""}
                continue
            created = active_client.post(
                base_url,
                headers={"Authorization": f"Bearer {token}"},
                json=_payload(variant),
            )
            created.raise_for_status()
            item = created.json()
            results[variant.language] = {
                "action": "submitted",
                "status": _bounded_status(item.get("status") or "PENDING"),
                "template_id": str(item.get("id") or "")[:40],
            }
        statuses = [item["status"] for item in results.values()]
        overall = "approved" if statuses and all(value == "APPROVED" for value in statuses) else "attention"
        return {"template": TEMPLATE_NAME, "mode": "apply" if apply else "check", "status": overall, "variants": results}
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise TemplateManagementError("meta_template_request_failed") from exc
    finally:
        if owns_client:
            active_client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage the AmtHero24 reminder utility template.")
    parser.add_argument("--apply", action="store_true", help="Submit missing variants; default is read-only.")
    parser.add_argument("--require-approved", action="store_true", help="Exit non-zero until all variants are approved.")
    args = parser.parse_args()
    try:
        result = manage_templates(apply=args.apply)
    except TemplateManagementError as exc:
        print(json.dumps({"status": "error", "code": str(exc)}, separators=(",", ":")))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 1 if args.require_approved and result["status"] != "approved" else 0


if __name__ == "__main__":
    raise SystemExit(main())
