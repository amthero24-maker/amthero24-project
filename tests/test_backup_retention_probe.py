"""Fail-closed contract for the synthetic production-volume retention proof."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from scripts.backup_retention_probe import (
    CONFIRMATION,
    main as probe_main,
    run_retention_probe,
)


def test_all_backup_profiles_pin_the_dockerfile_builder() -> None:
    for path in (
        "railway.backup.json",
        "railway.backup.certification.json",
        "railway.backup.retention-probe.json",
        "railway.restore.certification.json",
    ):
        config = json.loads(Path(path).read_text(encoding="utf-8"))
        assert config["build"] == {
            "builder": "DOCKERFILE",
            "dockerfilePath": "Dockerfile.backup",
        }


def test_retention_probe_profile_is_explicit_one_shot() -> None:
    config = json.loads(
        Path("railway.backup.retention-probe.json").read_text(encoding="utf-8")
    )
    assert config["build"]["builder"] == "DOCKERFILE"
    assert config["build"]["dockerfilePath"] == "Dockerfile.backup"
    deploy = config["deploy"]
    assert deploy["startCommand"] == (
        "python -m scripts.backup_retention_probe "
        "--confirm SYNTHETIC_RETENTION_PROBE"
    )
    assert deploy["restartPolicyType"] == "NEVER"
    assert "cronSchedule" not in deploy
    assert "healthcheckPath" not in deploy


def test_probe_rejects_missing_confirmation_before_volume_access(
    tmp_path: Path,
    capsys,
) -> None:
    with patch(
        "scripts.backup_retention_probe.verify_backup_volume_from_system"
    ) as verify:
        exit_code = probe_main(["--mount-path", str(tmp_path)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert json.loads(captured.err) == {
        "reason": "confirmation_required",
        "status": "failed",
    }
    verify.assert_not_called()


def test_probe_rotates_pairs_only_in_disposable_subdirectory(tmp_path: Path) -> None:
    sentinel = tmp_path / "real-backup-sentinel"
    sentinel.write_text("must remain untouched", encoding="utf-8")

    with patch(
        "scripts.backup_retention_probe.verify_backup_volume_from_system"
    ) as verify:
        report = run_retention_probe(str(tmp_path))

    verify.assert_called_once()
    assert report == {
        "cleanup": "complete",
        "created_pairs": 5,
        "kept_pairs": 2,
        "paired_deletion": True,
        "removed_pairs": 3,
        "scope": "synthetic_subdirectory_only",
        "status": "verified",
    }
    assert sentinel.read_text(encoding="utf-8") == "must remain untouched"
    assert not list(tmp_path.glob(".amthero24-retention-proof-*"))
    assert sorted(path.name for path in tmp_path.iterdir()) == [sentinel.name]


def test_probe_cli_success_output_is_bounded_and_path_free(
    tmp_path: Path,
    capsys,
) -> None:
    with patch(
        "scripts.backup_retention_probe.verify_backup_volume_from_system"
    ):
        exit_code = probe_main(
            ["--confirm", CONFIRMATION, "--mount-path", str(tmp_path)]
        )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 0
    assert payload["status"] == "verified"
    assert payload["removed_pairs"] == 3
    assert payload["kept_pairs"] == 2
    assert payload["paired_deletion"] is True
    assert payload["cleanup"] == "complete"
    assert str(tmp_path) not in captured.out
    assert "amthero24-2000" not in captured.out


def test_probe_failure_cleans_synthetic_directory_and_hides_error_detail(
    tmp_path: Path,
    capsys,
) -> None:
    private_detail = "private/path/value-must-not-leak"
    with patch(
        "scripts.backup_retention_probe.verify_backup_volume_from_system"
    ), patch(
        "scripts.backup_retention_probe._rotate",
        side_effect=OSError(private_detail),
    ):
        exit_code = probe_main(
            ["--confirm", CONFIRMATION, "--mount-path", str(tmp_path)]
        )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert json.loads(captured.err) == {
        "reason": "filesystem_operation_failed",
        "status": "failed",
    }
    assert private_detail not in captured.err
    assert str(tmp_path) not in captured.err
    assert not list(tmp_path.glob(".amthero24-retention-proof-*"))


def test_retention_probe_module_entrypoint_loads_dependencies() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "scripts.backup_retention_probe", "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "disposable synthetic volume subdirectory" in completed.stdout
