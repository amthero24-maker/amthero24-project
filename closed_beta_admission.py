"""Privacy-safe, deterministic admission policy for Closed Beta waves.

This module is deliberately side-effect free. It does not send messages, persist
participants, or enable Beta admission by itself.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class AdmissionDecision(StrEnum):
    DISABLED = "disabled"
    NEEDS_OPT_IN = "needs_opt_in"
    ADMITTED = "admitted"
    ALREADY_ADMITTED = "already_admitted"
    FULL = "full"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class AdmissionPolicy:
    enabled: bool = False
    capacity: int = 5

    def validate(self) -> None:
        if not 1 <= self.capacity <= 100:
            raise ValueError("beta_capacity_invalid")


@dataclass(frozen=True)
class AdmissionSnapshot:
    admitted_count: int
    already_admitted: bool = False
    beta_opt_in: bool = False
    state_verified: bool = True
    database_available: bool = True
    safety_hold: bool = False

    def validate(self, policy: AdmissionPolicy) -> None:
        policy.validate()
        if self.admitted_count < 0:
            raise ValueError("beta_admitted_count_invalid")
        if self.admitted_count > policy.capacity:
            raise ValueError("beta_admitted_count_exceeds_capacity")


@dataclass(frozen=True)
class AdmissionStatus:
    enabled: bool
    capacity: int
    admitted_count: int
    remaining_slots: int
    full: bool
    decision: AdmissionDecision


def decide_admission(policy: AdmissionPolicy, snapshot: AdmissionSnapshot) -> AdmissionDecision:
    """Return a fail-closed admission decision without consuming a slot."""
    snapshot.validate(policy)
    if not policy.enabled:
        return AdmissionDecision.DISABLED
    if not snapshot.database_available or not snapshot.state_verified or snapshot.safety_hold:
        return AdmissionDecision.BLOCKED
    if snapshot.already_admitted:
        return AdmissionDecision.ALREADY_ADMITTED
    if not snapshot.beta_opt_in:
        return AdmissionDecision.NEEDS_OPT_IN
    if snapshot.admitted_count >= policy.capacity:
        return AdmissionDecision.FULL
    return AdmissionDecision.ADMITTED


def aggregate_status(policy: AdmissionPolicy, snapshot: AdmissionSnapshot) -> AdmissionStatus:
    """Expose aggregate-only diagnostics; no participant identifiers are returned."""
    decision = decide_admission(policy, snapshot)
    remaining = max(policy.capacity - snapshot.admitted_count, 0)
    return AdmissionStatus(
        enabled=policy.enabled,
        capacity=policy.capacity,
        admitted_count=snapshot.admitted_count,
        remaining_slots=remaining,
        full=remaining == 0,
        decision=decision,
    )


def released_count(current_count: int, *, was_admitted: bool) -> int:
    """Idempotently model leaving/revocation without touching unrelated user data."""
    if current_count < 0:
        raise ValueError("beta_admitted_count_invalid")
    if not was_admitted:
        return current_count
    return max(current_count - 1, 0)


def privacy_safe_event_names() -> tuple[str, ...]:
    return (
        "beta_admission_checked",
        "beta_opt_in_required",
        "beta_admission_granted",
        "beta_admission_full",
        "beta_admission_blocked",
        "beta_admission_revoked",
    )
