"""ASGI protection for Meta WhatsApp webhook authenticity and deployment drain."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from collections.abc import Awaitable, Callable
from typing import Any

from log_safety import install_logging_safety

# Controlled Canary is read-only by default. Reminder delivery and historical-token
# compatibility require explicit production activation after certification.
os.environ.setdefault("REMINDER_WORKER_ENABLED", "false")
os.environ.setdefault("REMINDER_LEGACY_TOKEN_DECRYPTION_ENABLED", "false")

install_logging_safety()

from encryption_policy import install_encryption_policy  # noqa: E402
from storage_factory import install_production_storage_policy  # noqa: E402

install_production_storage_policy()
install_encryption_policy()

import runtime_health  # noqa: E402
import reminder_language_extensions as reminder_language_layer  # noqa: E402
import official_draft_runtime_extension as official_draft_runtime_layer  # noqa: E402
import cancellation_grounding_extensions as cancellation_grounding_layer  # noqa: E402
import journey_grounding_extensions as journey_grounding_layer  # noqa: E402
import writing_grounding_extensions as writing_grounding_layer  # noqa: E402
import closed_beta_runtime_extension as closed_beta_runtime_layer  # noqa: E402
from deployment_lifecycle import lifecycle  # noqa: E402

official_draft_runtime_layer.install(reminder_language_layer.core)
cancellation_grounding_layer.install(
    reminder_language_layer.core,
    official_draft_runtime_layer,
)
journey_grounding_layer.install(
    reminder_language_layer.core,
    official_draft_runtime_layer,
)
writing_grounding_layer.install(reminder_language_layer.core)
closed_beta_runtime_layer.install(
    reminder_language_layer.core,
    runtime_health=runtime_health,
)

_MAX_WEBHOOK_BODY_BYTES = 2 * 1024 * 1024
_NOINDEX_HEADER = b"noindex, nofollow, noarchive"
_ROBOTS_BODY = b"User-agent: *\nDisallow: /\n"


def signature_required() -> bool:
    return os.getenv("WEBHOOK_SIGNATURE_REQUIRED", "false").strip().casefold() in {"1", "true", "yes", "on"}


def verify_meta_signature(body: bytes, signature: str, app_secret: str) -> bool:
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


async def _plain_response(
    send: Callable[[dict[str, Any]], Awaitable[None]],
    status: int,
    body: bytes,
    *,
    head_only: bool = False,
) -> None:
    headers = [
        (b"content-type", b"text/plain; charset=utf-8"),
        (b"content-length", str(len(body)).encode("ascii")),
        (b"cache-control", b"no-store"),
        (b"x-robots-tag", _NOINDEX_HEADER),
    ]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": b"" if head_only else body})


class PublicSurfaceMiddleware:
    """Keep the production bot API out of indexes and hide framework discovery."""

    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self.app = app

    @staticmethod
    def _is_discovery_path(path: str) -> bool:
        normalized = (path or "/").rstrip("/") or "/"
        return (
            normalized == "/openapi.json"
            or normalized == "/docs"
            or normalized.startswith("/docs/")
            or normalized == "/redoc"
            or normalized.startswith("/redoc/")
        )

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[..., Awaitable[dict[str, Any]]],
        send: Callable[..., Awaitable[None]],
    ) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        method = str(scope.get("method") or "GET").upper()
        path = str(scope.get("path") or "/")
        if method in {"GET", "HEAD"} and path == "/robots.txt":
            await _plain_response(
                send,
                200,
                _ROBOTS_BODY,
                head_only=method == "HEAD",
            )
            return
        if self._is_discovery_path(path):
            await _plain_response(
                send,
                404,
                b"Not Found\n",
                head_only=method == "HEAD",
            )
            return

        async def send_with_noindex(message: dict[str, Any]) -> None:
            if message.get("type") == "http.response.start":
                headers = list(message.get("headers", []))
                if not any(bytes(key).lower() == b"x-robots-tag" for key, _ in headers):
                    headers.append((b"x-robots-tag", _NOINDEX_HEADER))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_noindex)


class DeploymentDrainMiddleware:
    """Reject new webhooks during drain and track admitted request/background work."""

    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self.app = app

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[..., Awaitable[dict[str, Any]]],
        send: Callable[..., Awaitable[None]],
    ) -> None:
        is_webhook = (
            scope.get("type") == "http"
            and scope.get("method") == "POST"
            and scope.get("path") == "/webhook"
        )
        if not is_webhook:
            await self.app(scope, receive, send)
            return
        if not lifecycle.work_started():
            await _json_response(send, 503, {"status": "draining"}, retry_after="10")
            return
        try:
            await self.app(scope, receive, send)
        finally:
            lifecycle.work_finished()


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

        if not app_secret and required:
            await _json_response(send, 503, {"status": "unavailable"})
            return

        if app_secret:
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


app = PublicSurfaceMiddleware(
    DeploymentDrainMiddleware(
        MetaWebhookSignatureMiddleware(runtime_health.app)
    )
)
