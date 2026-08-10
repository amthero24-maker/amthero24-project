"""Side-effect-bounded Closed Beta admission composition service.

The service does not send WhatsApp messages. It returns a deterministic outcome to
its caller. When admission is disabled it is an exact pass-through boundary.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping

from closed_beta_admission import AdmissionDecision
from closed_beta_admission_repository import ClosedBetaAdmissionRepository
from closed_beta_onboarding import (
    beta_admitted_message,
    beta_declined_message,
    beta_full_message,
    beta_notice,
    beta_opt_in_decision,
    onboarding_config,
)


class BetaAdmissionAction(StrEnum):
    CONTINUE = "continue"
    REPLY_AND_STOP = "reply_and_stop"


@dataclass(frozen=True)
class BetaAdmissionOutcome:
    action: BetaAdmissionAction
    reply: str = ""
    decision: str = ""
    changed: bool = False

    @property
    def should_continue(self) -> bool:
        return self.action is BetaAdmissionAction.CONTINUE


def _unavailable_message(language: str) -> str:
    messages = {
        "ar": "النسخة التجريبية غير متاحة مؤقتًا. ما تم حجز مكان أو حفظ موافقة جديدة.",
        "de": "Die Closed Beta ist vorübergehend nicht verfügbar. Es wurde kein Platz belegt und keine neue Zustimmung gespeichert.",
        "en": "The Closed Beta is temporarily unavailable. No slot was consumed and no new consent was stored.",
        "uk": "Закрита Beta тимчасово недоступна. Місце не було зайнято й нову згоду не збережено.",
        "el": "Η κλειστή Beta δεν είναι προσωρινά διαθέσιμη. Δεν δεσμεύτηκε θέση και δεν αποθηκεύτηκε νέα συγκατάθεση.",
    }
    return messages.get(language, messages["de"])


def evaluate_beta_admission(
    *,
    store: Any,
    phone: str,
    text: str,
    language: str,
    env: Mapping[str, str],
) -> BetaAdmissionOutcome:
    """Evaluate whether normal onboarding may continue for this incoming turn.

    Disabled configuration is a no-op. Enabled configuration is fail-closed:
    invalid config, unavailable schema/DB, full capacity, or missing opt-in never
    fall through to normal onboarding.
    """
    try:
        config = onboarding_config(env)
    except (TypeError, ValueError):
        return BetaAdmissionOutcome(
            BetaAdmissionAction.REPLY_AND_STOP,
            _unavailable_message(language),
            AdmissionDecision.BLOCKED.value,
        )

    if not config.enabled:
        return BetaAdmissionOutcome(
            BetaAdmissionAction.CONTINUE,
            decision=AdmissionDecision.DISABLED.value,
        )

    try:
        repository = ClosedBetaAdmissionRepository(
            store,
            tenant_key=config.tenant_key,
            wave=config.wave,
        )
    except (TypeError, ValueError):
        return BetaAdmissionOutcome(
            BetaAdmissionAction.REPLY_AND_STOP,
            _unavailable_message(language),
            AdmissionDecision.BLOCKED.value,
        )

    if not repository.schema_ready:
        return BetaAdmissionOutcome(
            BetaAdmissionAction.REPLY_AND_STOP,
            _unavailable_message(language),
            AdmissionDecision.BLOCKED.value,
        )

    if repository.is_admitted(phone):
        return BetaAdmissionOutcome(
            BetaAdmissionAction.CONTINUE,
            decision=AdmissionDecision.ALREADY_ADMITTED.value,
        )

    opt_in = beta_opt_in_decision(text)
    if opt_in is False:
        return BetaAdmissionOutcome(
            BetaAdmissionAction.REPLY_AND_STOP,
            beta_declined_message(language),
            AdmissionDecision.NEEDS_OPT_IN.value,
        )
    if opt_in is None:
        return BetaAdmissionOutcome(
            BetaAdmissionAction.REPLY_AND_STOP,
            beta_notice(language),
            AdmissionDecision.NEEDS_OPT_IN.value,
        )

    status = repository.claim(
        phone,
        policy=config.policy,
        beta_opt_in=True,
        consent_version=config.notice_version,
    )
    if status.decision is AdmissionDecision.ADMITTED:
        return BetaAdmissionOutcome(
            BetaAdmissionAction.REPLY_AND_STOP,
            beta_admitted_message(language),
            status.decision.value,
            changed=status.changed,
        )
    if status.decision is AdmissionDecision.ALREADY_ADMITTED:
        return BetaAdmissionOutcome(
            BetaAdmissionAction.CONTINUE,
            decision=status.decision.value,
        )
    if status.decision is AdmissionDecision.FULL:
        return BetaAdmissionOutcome(
            BetaAdmissionAction.REPLY_AND_STOP,
            beta_full_message(language),
            status.decision.value,
        )
    return BetaAdmissionOutcome(
        BetaAdmissionAction.REPLY_AND_STOP,
        _unavailable_message(language),
        status.decision.value,
    )
