"""Meta webhook authenticity tests."""
from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import patch

from starlette.testclient import TestClient

import webhook_security


async def _echo_app(scope, receive, send) -> None:
    body = b""
    if scope["method"] == "POST":
        message = await receive()
        body = message.get("body", b"")
    response = json.dumps({"received": len(body)}).encode("utf-8")
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"application/json")]})
    await send({"type": "http.response.body", "body": response})


def _signature(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_signature_helper_accepts_exact_body_and_rejects_invalid_values() -> None:
    body = b'{"entry":[]}'
    signature = _signature(body, "app-secret")
    assert webhook_security.verify_meta_signature(body, signature, "app-secret") is True
    assert webhook_security.verify_meta_signature(body + b" ", signature, "app-secret") is False
    assert webhook_security.verify_meta_signature(body, "sha256=bad", "app-secret") is False
    assert webhook_security.verify_meta_signature(body, signature, "wrong-secret") is False


def test_valid_signature_replays_exact_body_to_webhook() -> None:
    body = b'{"entry":[{"id":"1"}]}'
    client = TestClient(webhook_security.MetaWebhookSignatureMiddleware(_echo_app))
    with patch.dict("os.environ", {"META_APP_SECRET": "app-secret"}, clear=True):
        response = client.post(
            "/webhook",
            content=body,
            headers={"content-type": "application/json", "x-hub-signature-256": _signature(body, "app-secret")},
        )
    assert response.status_code == 200
    assert response.json() == {"received": len(body)}


def test_missing_or_invalid_signature_is_rejected() -> None:
    body = b'{"entry":[]}'
    client = TestClient(webhook_security.MetaWebhookSignatureMiddleware(_echo_app))
    with patch.dict("os.environ", {"META_APP_SECRET": "app-secret"}, clear=True):
        missing = client.post("/webhook", content=body)
        invalid = client.post("/webhook", content=body, headers={"x-hub-signature-256": "sha256=" + "0" * 64})
    assert missing.status_code == 403
    assert invalid.status_code == 403
    assert missing.json()["status"] == "rejected"


def test_signature_is_optional_until_secret_is_configured() -> None:
    body = b'{"entry":[]}'
    client = TestClient(webhook_security.MetaWebhookSignatureMiddleware(_echo_app))
    with patch.dict("os.environ", {}, clear=True):
        response = client.post("/webhook", content=body)
    assert response.status_code == 200


def test_required_signature_without_secret_fails_closed() -> None:
    client = TestClient(webhook_security.MetaWebhookSignatureMiddleware(_echo_app))
    with patch.dict("os.environ", {"WEBHOOK_SIGNATURE_REQUIRED": "true"}, clear=True):
        response = client.post("/webhook", content=b"{}")
    assert response.status_code == 503
    assert response.json()["status"] == "unavailable"


def test_get_webhook_verification_is_not_signature_checked() -> None:
    client = TestClient(webhook_security.MetaWebhookSignatureMiddleware(_echo_app))
    with patch.dict("os.environ", {"META_APP_SECRET": "app-secret"}, clear=True):
        response = client.get("/webhook")
    assert response.status_code == 200
