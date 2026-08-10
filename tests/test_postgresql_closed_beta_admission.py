"""Real PostgreSQL concurrency tests for Closed Beta admission.

Runs only against the isolated PostgreSQL service in GitHub Actions. No Railway or
real-user database is contacted.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from closed_beta_admission import AdmissionDecision, AdmissionPolicy
from closed_beta_admission_repository import ClosedBetaAdmissionRepository
from data_store import PostgresDataStore


def _store() -> PostgresDataStore:
    return PostgresDataStore(os.environ["DATABASE_URL"])


def _scope(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def _phone(index: int) -> str:
    return f"+491799{index:07d}"


def _phone_hash(phone: str) -> str:
    normalized = "".join(
        character for character in phone if character.isdigit() or character == "+"
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def test_cross_replica_claims_never_exceed_capacity() -> None:
    first_store = _store()
    second_store = _store()
    tenant = _scope("tenant")
    wave = _scope("wave")
    policy = AdmissionPolicy(enabled=True, capacity=5)
    repositories = (
        ClosedBetaAdmissionRepository(first_store, tenant_key=tenant, wave=wave),
        ClosedBetaAdmissionRepository(second_store, tenant_key=tenant, wave=wave),
    )

    try:
        arguments = [
            (repositories[index % 2], _phone(index))
            for index in range(20)
        ]
        with ThreadPoolExecutor(max_workers=12) as executor:
            results = list(
                executor.map(
                    lambda item: item[0].claim(
                        item[1],
                        policy=policy,
                        beta_opt_in=True,
                        consent_version="closed-beta-v1",
                    ),
                    arguments,
                )
            )

        assert sum(result.decision == AdmissionDecision.ADMITTED for result in results) == 5
        assert sum(result.decision == AdmissionDecision.FULL for result in results) == 15
        assert repositories[0].status(policy).admitted_count == 5

        with first_store.pool.connection() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM closed_beta_admissions
                WHERE tenant_key = %s AND wave = %s AND status = 'active'
                """,
                (tenant, wave),
            ).fetchone()
        assert int(row["count"]) == 5
    finally:
        first_store.close()
        second_store.close()


def test_same_recipient_replay_consumes_one_slot_across_replicas() -> None:
    first_store = _store()
    second_store = _store()
    tenant = _scope("tenant")
    wave = _scope("wave")
    phone = _phone(100)
    policy = AdmissionPolicy(enabled=True, capacity=5)
    repositories = (
        ClosedBetaAdmissionRepository(first_store, tenant_key=tenant, wave=wave),
        ClosedBetaAdmissionRepository(second_store, tenant_key=tenant, wave=wave),
    )

    try:
        with ThreadPoolExecutor(max_workers=10) as executor:
            results = list(
                executor.map(
                    lambda index: repositories[index % 2].claim(
                        phone,
                        policy=policy,
                        beta_opt_in=True,
                        consent_version="closed-beta-v1",
                    ),
                    range(10),
                )
            )

        assert sum(result.decision == AdmissionDecision.ADMITTED for result in results) == 1
        assert sum(
            result.decision == AdmissionDecision.ALREADY_ADMITTED
            for result in results
        ) == 9
        assert repositories[0].status(policy).admitted_count == 1
    finally:
        first_store.close()
        second_store.close()


def test_revoke_releases_capacity_without_cross_tenant_effects() -> None:
    store = _store()
    tenant_a = _scope("tenant_a")
    tenant_b = _scope("tenant_b")
    wave = _scope("wave")
    policy = AdmissionPolicy(enabled=True, capacity=1)
    tenant_a_repository = ClosedBetaAdmissionRepository(
        store,
        tenant_key=tenant_a,
        wave=wave,
    )
    tenant_b_repository = ClosedBetaAdmissionRepository(
        store,
        tenant_key=tenant_b,
        wave=wave,
    )
    alpha = _phone(200)
    beta = _phone(201)

    try:
        assert tenant_a_repository.claim(
            alpha,
            policy=policy,
            beta_opt_in=True,
            consent_version="v1",
        ).decision == AdmissionDecision.ADMITTED
        assert tenant_a_repository.claim(
            beta,
            policy=policy,
            beta_opt_in=True,
            consent_version="v1",
        ).decision == AdmissionDecision.FULL
        assert tenant_b_repository.claim(
            beta,
            policy=policy,
            beta_opt_in=True,
            consent_version="v1",
        ).decision == AdmissionDecision.ADMITTED

        assert tenant_a_repository.revoke(alpha) is True
        assert tenant_a_repository.revoke(alpha) is False
        assert tenant_a_repository.claim(
            beta,
            policy=policy,
            beta_opt_in=True,
            consent_version="v1",
        ).decision == AdmissionDecision.ADMITTED
        assert tenant_a_repository.is_admitted(beta) is True
        assert tenant_b_repository.is_admitted(beta) is True
    finally:
        store.close()


def test_claim_revoke_and_delete_share_one_tenant_lock_across_waves() -> None:
    store = _store()
    tenant = _scope("tenant")
    wave_1 = _scope("wave_a")
    wave_2 = _scope("wave_b")
    policy = AdmissionPolicy(enabled=True, capacity=5)
    wave_1_repository = ClosedBetaAdmissionRepository(
        store,
        tenant_key=tenant,
        wave=wave_1,
    )
    wave_2_repository = ClosedBetaAdmissionRepository(
        store,
        tenant_key=tenant,
        wave=wave_2,
    )
    existing_phone = _phone(250)
    new_phone = _phone(251)

    try:
        assert wave_2_repository.claim(
            existing_phone,
            policy=policy,
            beta_opt_in=True,
            consent_version="v1",
        ).decision == AdmissionDecision.ADMITTED
        assert wave_1_repository._lock_material() == wave_2_repository._lock_material()

        with store.pool.connection() as holder:
            holder.execute(
                "SELECT pg_advisory_lock(hashtext(%s)::bigint)",
                (wave_1_repository._lock_material(),),
            )
            with ThreadPoolExecutor(max_workers=2) as executor:
                claim_future = executor.submit(
                    wave_1_repository.claim,
                    new_phone,
                    policy=policy,
                    beta_opt_in=True,
                    consent_version="v1",
                )
                delete_future = executor.submit(
                    wave_2_repository.delete_user,
                    existing_phone,
                )
                time.sleep(0.2)
                assert claim_future.done() is False
                assert delete_future.done() is False
                holder.execute(
                    "SELECT pg_advisory_unlock(hashtext(%s)::bigint)",
                    (wave_1_repository._lock_material(),),
                )
                claim_result = claim_future.result(timeout=5)
                delete_result = delete_future.result(timeout=5)

        assert claim_result.decision == AdmissionDecision.ADMITTED
        assert delete_result is True
        assert wave_1_repository.is_admitted(new_phone) is True
        assert wave_2_repository.is_admitted(existing_phone) is False
    finally:
        store.close()


def test_storage_export_and_privacy_delete_are_identifier_free_and_tenant_scoped() -> None:
    store = _store()
    tenant_a = _scope("tenant_a")
    tenant_b = _scope("tenant_b")
    wave_1 = _scope("wave_a")
    wave_2 = _scope("wave_b")
    phone = _phone(300)
    policy = AdmissionPolicy(enabled=True, capacity=5)
    repositories = (
        ClosedBetaAdmissionRepository(store, tenant_key=tenant_a, wave=wave_1),
        ClosedBetaAdmissionRepository(store, tenant_key=tenant_a, wave=wave_2),
        ClosedBetaAdmissionRepository(store, tenant_key=tenant_b, wave=wave_1),
    )

    try:
        for repository in repositories:
            assert repository.claim(
                phone,
                policy=policy,
                beta_opt_in=True,
                consent_version="closed-beta-v1",
            ).decision == AdmissionDecision.ADMITTED
        assert repositories[1].revoke(phone) is True

        with store.pool.connection() as connection:
            rows = connection.execute(
                """
                SELECT tenant_key, wave, phone_hash, consent_version
                FROM closed_beta_admissions
                WHERE phone_hash = %s
                """,
                (_phone_hash(phone),),
            ).fetchall()
        assert len(rows) == 3
        assert all(str(row["phone_hash"]) != phone for row in rows)
        assert all(len(str(row["phone_hash"])) == 64 for row in rows)
        assert phone not in repr(rows)

        exported = repositories[0].export_user_status(phone)
        assert exported["status"] == "available"
        assert exported["active"] is True
        assert [(record["wave"], record["status"]) for record in exported["records"]] == [
            (wave_1, "active"),
            (wave_2, "revoked"),
        ]
        serialized_export = json.dumps(exported, sort_keys=True)
        assert phone not in serialized_export
        assert tenant_a not in serialized_export
        assert re.search(r"\b[a-f0-9]{64}\b", serialized_export) is None

        assert repositories[0].delete_user(phone) is True
        assert repositories[0].is_admitted(phone) is False
        assert repositories[1].is_admitted(phone) is False
        assert repositories[2].is_admitted(phone) is True
    finally:
        store.close()
