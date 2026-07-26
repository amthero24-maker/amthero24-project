"""Final composition layer for privacy-safe backup freshness operations."""
from __future__ import annotations

from typing import Any

import admin_extensions as admin_module
import launch_extensions as launch_module
import shared_drain_extensions as composed
from backup_freshness import aggregate_backup_freshness
from backup_freshness_policy import augment_launch_report

core = composed.core
_ORIGINAL_ADMIN_BUILD_OVERVIEW = admin_module.build_overview
_ORIGINAL_BUILD_LAUNCH_REPORT = launch_module.build_launch_report


def _build_overview(store: Any, **kwargs: Any) -> dict[str, Any]:
    payload = _ORIGINAL_ADMIN_BUILD_OVERVIEW(store, **kwargs)
    payload["backups"] = aggregate_backup_freshness(store, now=kwargs.get("now"))
    return payload


def _build_launch_report(overview: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    report = _ORIGINAL_BUILD_LAUNCH_REPORT(overview, **kwargs)
    return augment_launch_report(report, overview, environment=kwargs.get("env"))


admin_module.build_overview = _build_overview
launch_module.build_launch_report = _build_launch_report

app = composed.app
store = composed.store
