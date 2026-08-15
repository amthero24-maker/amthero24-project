"""Fail-closed tests for time-bound recovery pipeline certification."""
from __future__ import annotations

from pathlib import Path

import recovery_pipeline_certification as certification


def test_current_checkout_matches_the_restore_certified_pipeline() -> None:
    assessment = certification.assess_recovery_pipeline()

    assert assessment.ready is True
    assert assessment.status == "ready"
    assert assessment.code == "pipeline_certified"
    assert assessment.checked_files == 16


def test_missing_certified_pipeline_file_fails_closed(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        certification,
        "_CERTIFIED_BLOBS",
        {"missing.txt": "0" * 40},
    )

    assessment = certification.assess_recovery_pipeline(tmp_path)

    assert assessment.ready is False
    assert assessment.code == "pipeline_file_missing"
    assert assessment.checked_files == 0
    assert "missing.txt" not in str(assessment)


def test_pipeline_content_drift_fails_closed_without_exposing_identity(
    tmp_path: Path, monkeypatch
) -> None:
    candidate = tmp_path / "pipeline.txt"
    candidate.write_text("certified\n", encoding="utf-8")
    expected = certification._git_blob_sha(candidate)
    monkeypatch.setattr(
        certification,
        "_CERTIFIED_BLOBS",
        {"pipeline.txt": expected},
    )

    assert certification.assess_recovery_pipeline(tmp_path).ready is True

    candidate.write_text("drifted\n", encoding="utf-8")
    assessment = certification.assess_recovery_pipeline(tmp_path)

    assert assessment.ready is False
    assert assessment.code == "pipeline_drift"
    assert expected not in str(assessment)
    assert "pipeline.txt" not in str(assessment)


def test_certification_window_defaults_to_seven_days() -> None:
    assert certification.restore_certification_max_age_hours({}) == 168


def test_certification_window_accepts_only_bounded_explicit_hours() -> None:
    assert certification.restore_certification_max_age_hours(
        {"PRODUCTION_BACKUP_RESTORE_CERTIFICATION_MAX_AGE_HOURS": "24"}
    ) == 24
    assert certification.restore_certification_max_age_hours(
        {"PRODUCTION_BACKUP_RESTORE_CERTIFICATION_MAX_AGE_HOURS": "720"}
    ) == 720

    for value in ("", "not-a-number", "23", "721"):
        result = certification.restore_certification_max_age_hours(
            {"PRODUCTION_BACKUP_RESTORE_CERTIFICATION_MAX_AGE_HOURS": value}
        )
        if value == "":
            assert result is None
        else:
            assert result is None
