"""Top-level durable queue observability and privacy-retention composition."""
from __future__ import annotations

from typing import Any

import admin_extensions as admin_module
import durable_queue_extensions as composed
import privacy_engine as privacy_module
from durable_queue import DurableQueueRepository
from queue_observability import build_queue_overview

core = composed.core
_ORIGINAL_ADMIN_BUILD_OVERVIEW = admin_module.build_overview
_ORIGINAL_PRIVACY_CLEANUP = privacy_module.cleanup_retention
_QUEUE_REPOSITORY: DurableQueueRepository | None = None


def _repository(store: Any | None = None) -> DurableQueueRepository:
    global _QUEUE_REPOSITORY
    target = store or core.store
    if _QUEUE_REPOSITORY is None or _QUEUE_REPOSITORY.store is not target:
        _QUEUE_REPOSITORY = DurableQueueRepository(target)
    return _QUEUE_REPOSITORY


def _build_overview(store: Any, **kwargs: Any) -> dict[str, Any]:
    payload = _ORIGINAL_ADMIN_BUILD_OVERVIEW(store, **kwargs)
    payload["durable_queue"] = build_queue_overview(store, now=kwargs.get("now"))
    return payload


def _cleanup_retention(store: Any, **kwargs: Any) -> dict[str, int]:
    result = _ORIGINAL_PRIVACY_CLEANUP(store, **kwargs)
    if str(getattr(store, "backend_name", "json")) == "postgresql":
        result["durable_queue"] = _repository(store).cleanup(now=kwargs.get("now"))
    else:
        result["durable_queue"] = 0
    return result


admin_module.build_overview = _build_overview
privacy_module.cleanup_retention = _cleanup_retention

app = composed.app
store = composed.store
