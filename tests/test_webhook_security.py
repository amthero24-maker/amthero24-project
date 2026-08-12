"""Meta webhook signature and deployment-drain boundary tests."""
from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import patch

from starlette.testclient import TestClient

import webhook_security
from deployment_lifecycle import lifecycle


async def _echo_app(scope, receive, send) -> None:
    body = b""
    while True:
        message = await receive()
        if message.get("type") != "http.request":
            continue
        body += bytes(message.get("body") or b"")
        if not message.get("more_body", False):
            break
    response = json.dumps({"size": len(body)}).encode("utf-8")
    await send({
        "type": "http.response.start",
        "status": 200,
        "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(response)).encode())],
    })
    await send({"type": "http.response.body", "body": response})


def _signature(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_signature_helper_accepts_valid_and_rejects_invalid() -> None:
    body = b'{"entry":[]}'
    secret = "app-secret"
    assert webhook_security.verify_meta_signature(body, _signature(body, secret), secret)
    assert not webhook_security.verify_meta_signature(body, "sha256=bad", secret)
    assert not webhook_security.verify_meta_signature(body, "", secret)


def test_signature_middleware_rejects_bad_signature() -> None:
    client = TestClient(webhook_security.MetaWebhookSignatureMiddleware(_echo_app))
    with patch.dict(
        "os.environ",
        {"META_APP_SECRET": "app-secret", "WEBHOOK_SIGNATURE_REQUIRED": "true"},
        clear=True,
    ):
        response = client.post("/webhook", content=b'{"entry":[]}', headers={"X-Hub-Signature-256": "sha256=bad"})
    assert response.status_code == 403
    assert response.json() == {"status": "rejected"}
    assert response.headers["cache-control"] == "no-store"


def test_signature_middleware_replays_valid_body() -> None:
    body = b'{"entry":[{"id":"safe"}]}'
    secret = "app-secret"
    client = TestClient(webhook_security.MetaWebhookSignatureMiddleware(_echo_app))
    with patch.dict(
        "os.environ",
        {"META_APP_SECRET": secret, "WEBHOOK_SIGNATURE_REQUIRED": "true"},
        clear=True,
    ):
        response = client.post(
            "/webhook",
            content=body,
            headers={"X-Hub-Signature-256": _signature(body, secret)},
        )
    assert response.status_code == 200
    assert response.json() == {"size": len(body)}


def test_missing_secret_can_fail_closed() -> None:
    client = TestClient(webhook_security.MetaWebhookSignatureMiddleware(_echo_app))
    with patch.dict("os.environ", {"WEBHOOK_SIGNATURE_REQUIRED": "true"}, clear=True):
        response = client.post("/webhook", content=b'{"entry":[]}')
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}


def test_oversized_body_is_rejected_before_application() -> None:
    body = b"x" * (webhook_security._MAX_WEBHOOK_BODY_BYTES + 1)
    client = TestClient(webhook_security.MetaWebhookSignatureMiddleware(_echo_app))
    with patch.dict("os.environ", {}, clear=True):
        response = client.post("/webhook", content=body)
    assert response.status_code == 413
    assert response.json() == {"status": "rejected"}


def test_drain_middleware_rejects_new_webhook_with_retry_after() -> None:
    middleware = webhook_security.DeploymentDrainMiddleware(_echo_app)
    client = TestClient(middleware)
    lifecycle.start_accepting()
    lifecycle.begin_drain()
    response = client.post("/webhook", content=b"{}")
    assert response.status_code == 503
    assert response.headers["retry-after"] == "10"
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"status": "draining"}
    lifecycle.start_accepting()


def test_drain_middleware_tracks_and_releases_webhook_work() -> None:
    middleware = webhook_security.DeploymentDrainMiddleware(_echo_app)
    client = TestClient(middleware)
    lifecycle.start_accepting()
    before = lifecycle.snapshot().active_work
    response = client.post("/webhook", content=b"{}")
    after = lifecycle.snapshot().active_work
    assert response.status_code == 200
    assert before == after == 0


def test_public_surface_blocks_framework_discovery_paths() -> None:
    client = TestClient(
        webhook_security.PublicSurfaceMiddleware(_echo_app)
    )

    for path in (
        "/docs",
        "/docs/",
        "/docs/oauth2-redirect",
        "/redoc",
        "/redoc/",
        "/openapi.json",
        "/openapi.json/",
    ):
        response = client.get(path)
        assert response.status_code == 404
        assert response.headers["x-robots-tag"] == "noindex, nofollow, noarchive"
        assert response.headers["cache-control"] == "no-store"


def test_robots_disallows_all_crawling_without_reaching_application() -> None:
    client = TestClient(
        webhook_security.PublicSurfaceMiddleware(_echo_app)
    )

    response = client.get("/robots.txt")

    assert response.status_code == 200
    assert response.text == "User-agent: *\nDisallow: /\n"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-robots-tag"] == "noindex, nofollow, noarchive"


def test_public_responses_receive_noindex_header() -> None:
    client = TestClient(
        webhook_security.PublicSurfaceMiddleware(_echo_app)
    )

    response = client.get("/health")

    assert response.status_code == 200
    assert response.headers["x-robots-tag"] == "noindex, nofollow, noarchive"


def test_signature_rejections_also_receive_noindex_header() -> None:
    protected = webhook_security.PublicSurfaceMiddleware(
        webhook_security.MetaWebhookSignatureMiddleware(_echo_app)
    )
    client = TestClient(protected)

    with patch.dict(
        "os.environ",
        {"META_APP_SECRET": "synthetic-app-secret"},
        clear=True,
    ):
        response = client.post("/webhook", content=b'{"entry":[]}')

    assert response.status_code == 403
    assert response.headers["x-robots-tag"] == "noindex, nofollow, noarchive"


def test_production_entrypoint_installs_public_surface_outermost() -> None:
    assert isinstance(
        webhook_security.app,
        webhook_security.PublicSurfaceMiddleware,
    )
    assert isinstance(
        webhook_security.app.app,
        webhook_security.DeploymentDrainMiddleware,
    )
