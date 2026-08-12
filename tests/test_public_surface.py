"""Production bot discovery and indexing boundary tests."""
from __future__ import annotations

from unittest.mock import patch

from fastapi import FastAPI
from starlette.testclient import TestClient

import webhook_security


def _inner_app() -> FastAPI:
    application = FastAPI()

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return application


def test_public_surface_blocks_framework_discovery_paths() -> None:
    client = TestClient(
        webhook_security.PublicSurfaceMiddleware(_inner_app())
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
        webhook_security.PublicSurfaceMiddleware(_inner_app())
    )

    response = client.get("/robots.txt")

    assert response.status_code == 200
    assert response.text == "User-agent: *\nDisallow: /\n"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-robots-tag"] == "noindex, nofollow, noarchive"


def test_public_responses_receive_noindex_header() -> None:
    client = TestClient(
        webhook_security.PublicSurfaceMiddleware(_inner_app())
    )

    response = client.get("/health")

    assert response.status_code == 200
    assert response.headers["x-robots-tag"] == "noindex, nofollow, noarchive"


def test_signature_rejections_also_receive_noindex_header() -> None:
    protected = webhook_security.PublicSurfaceMiddleware(
        webhook_security.MetaWebhookSignatureMiddleware(_inner_app())
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
