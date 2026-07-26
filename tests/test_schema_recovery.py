"""Unit tests for privacy-safe backup and restore schema identity."""
from __future__ import annotations

from dataclasses import asdict
from unittest.mock import patch

import pytest

import schema_recovery


def test_expected_identity_round_trips_through_manifest() -> None:
    identity = schema_recovery.expected_schema_identity()
    restored = schema_recovery.schema_identity_from_manifest(identity.manifest_fields())

    assert restored == identity
    assert restored.version >= 1
    assert restored.ledger_entries == restored.version
    assert len(restored.checksum) == 64
    assert restored.contract == "valid"


def test_manifest_identity_rejects_missing_and_invalid_metadata() -> None:
    with pytest.raises(schema_recovery.SchemaRecoveryError) as missing:
        schema_recovery.schema_identity_from_manifest({})
    assert missing.value.code == "backup_schema_identity_missing"

    identity = schema_recovery.expected_schema_identity().manifest_fields()
    identity["schema_checksum"] = "not-a-checksum"
    with pytest.raises(schema_recovery.SchemaRecoveryError) as checksum:
        schema_recovery.schema_identity_from_manifest(identity)
    assert checksum.value.code == "backup_schema_checksum_invalid"


def test_manifest_identity_rejects_incomplete_ledger() -> None:
    identity = schema_recovery.expected_schema_identity().manifest_fields()
    identity["schema_ledger_entries"] = int(identity["schema_version"]) + 1

    with pytest.raises(schema_recovery.SchemaRecoveryError) as raised:
        schema_recovery.schema_identity_from_manifest(identity)
    assert raised.value.code == "backup_schema_ledger_invalid"


def test_current_manifest_rejects_other_version_and_checksum() -> None:
    fields = schema_recovery.expected_schema_identity().manifest_fields()
    fields["schema_version"] = int(fields["schema_version"]) + 1
    fields["schema_ledger_entries"] = int(fields["schema_version"])
    with pytest.raises(schema_recovery.SchemaRecoveryError) as version:
        schema_recovery.require_current_manifest_schema(fields)
    assert version.value.code == "backup_schema_version_incompatible"

    fields = schema_recovery.expected_schema_identity().manifest_fields()
    fields["schema_checksum"] = "f" * 64 if fields["schema_checksum"] != "f" * 64 else "e" * 64
    with pytest.raises(schema_recovery.SchemaRecoveryError) as checksum:
        schema_recovery.require_current_manifest_schema(fields)
    assert checksum.value.code == "backup_schema_checksum_incompatible"


def test_restored_schema_must_exactly_match_manifest_identity() -> None:
    expected = schema_recovery.expected_schema_identity()
    different = schema_recovery.SchemaIdentity(
        version=expected.version,
        checksum=expected.checksum,
        ledger_entries=expected.ledger_entries,
        contract="invalid",
    )
    with patch("schema_recovery.require_current_manifest_schema", return_value=expected), patch(
        "schema_recovery.inspect_database_schema", return_value=different
    ):
        with pytest.raises(schema_recovery.SchemaRecoveryError) as raised:
            schema_recovery.verify_restored_schema("postgresql://example.invalid/db", expected.manifest_fields())
    assert raised.value.code == "restored_schema_identity_mismatch"


def test_schema_identity_contains_only_non_personal_schema_metadata() -> None:
    fields = set(schema_recovery.SchemaIdentity.__dataclass_fields__)
    assert fields == {"version", "checksum", "ledger_entries", "contract"}
    encoded = " ".join(fields).casefold()
    for forbidden in ("phone", "message", "document", "sender", "ciphertext", "token", "database_url"):
        assert forbidden not in encoded

    payload = asdict(schema_recovery.expected_schema_identity())
    assert set(payload) == fields
