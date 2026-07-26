"""ASGI protection for Meta WhatsApp webhook authenticity and deployment drain."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from collections.abc import Awaitable, Callable
from typing import Any

from log_safety import install_logging_safety

# Railway starts `webhook_security:app`. Install logging safety before any storage,
# provider, webhook, or application module can create a record containing request data.
install_logging_safety()

from encryption_policy import install_encryption_policy  # noqa: E402
from storage_factory import install_production_storage_policy  # noqa: E402

# Install durable-storage and reversible-encryption policies before application
# composition imports modules and binds them.
install_production_storage_policy()
install_encryption_policy()

import runtime_health  # noqa: E402
from deployment_lifecycle import lifecycle  # noqa: E402

_MAX_WEBHOOK_BODY_BYTES = 2 * 1024 * 1024


def signature_required() -> bool:
    return os.getenv("WEBHOOK_SIGNATURE_REQUIRED", "false").strip().casefold() in {"1", "true", "yes", "on"}


def verify_meta_signature(body: bytes, signature: str, app_secret: str) -> bool:
    """Validate Meta's sha256 HMAC signature using constant-time comparison."""
    if not body or not app_secret or not signature.startswith("sha256="):
        return False
    supplied = signature.split("=", 1)[1].strip().casefold()
    if len(supplied) != 64:
        return False
    expected = hmac.new(app_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, supplied)


async def _json_response(
    send: Callable[[dict[str, Any]], Awaitable[None]],
    status: int,
    payload: dict[str, object],
    *,
    retry_after: str = "",
) -> None:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode("ascii")),
        (b"cache-control", b"no-store"),
    ]
    if retry_after:
        headers.append((b"retry-after", retry_after.encode("ascii")))
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


class DeploymentDrainMiddleware:
    """Stop new webhook work before Railway terminates an old deployment."""

    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self.app = app

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[..., Awaitable[dict[str, Any]]],
        send: Callable[..., Awaitable[None]],
    ) -> None:
        if scope.get("type") == "http" and scope.get("method") == "POST" and scope.get("path") == "/webhook":
            if not lifecycle.snapshot().accepting_work:
                await _json_response(send, 503, {"status": "draining"}, retry_after="10")
                return
        await self.app(scope, receive, send)


class MetaWebhookSignatureMiddleware:
    """Verify POST /webhook payloads when a Meta App Secret is configured."""

    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Callable[..., Awaitable[dict[str, Any]]], send: Callable[..., Awaitable[None]]) -> None:
        if scope.get("type") != "http" or scope.get("method") != "POST" or scope.get("path") != "/webhook":
            await self.app(scope, receive, send)
            return

        app_secret = os.getenv("META_APP_SECRET", "").strip()
        required = signature_required()
        if not app_secret:
            if required:
                await _json_response(send, 503, {"status": "unavailable"})
                return
            await self.app(scope, receive, send)
            return

        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message.get("type") != "http.request":
                continue
            chunk = bytes(message.get("body") or b"")
            size += len(chunk)
            if size > _MAX_WEBHOOK_BODY_BYTES:
                await _json_response(send, 413, {"status": "rejected"})
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        body = b"".join(chunks)

        headers = {
            key.decode("latin-1").casefold(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        signature = headers.get("x-hub-signature-256", "")
        if not verify_meta_signature(body, signature, app_secret):
            await _json_response(send, 403, {"status": "rejected"})
            return

        replayed = False

        async def replay_receive() -> dict[str, Any]:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return {"type": "http.disconnect"}

        await self.app(scope, replay_receive, send)


app = DeploymentDrainMiddleware(MetaWebhookSignatureMiddleware(runtime_health.app))
