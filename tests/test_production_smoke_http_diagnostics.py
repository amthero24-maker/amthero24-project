"""Regression tests for sanitized Production Smoke HTTP diagnostics."""
from __future__ import annotations

import io
from email.message import Message
from unittest.mock import patch
from urllib.error import HTTPError

import pytest

import production_smoke


class _Response:
    def __init__(self, *, status: int, content_type: str, body: bytes) -> None:
        self.status = status
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self, _limit: int) -> bytes:
        return self._body


def test_invalid_json_reports_only_status_and_bounded_content_type() -> None:
    response = _Response(
        status=500,
        content_type="text/html; charset=utf-8",
        body=b"<html>private production failure details</html>",
    )
    with patch("production_smoke.urlopen", return_value=response):
        with pytest.raises(production_smoke.SmokeError) as exc_info:
            production_smoke.fetch_json("https://example.test", "/admin/launch-readiness", token="secret")

    detail = str(exc_info.value)
    assert detail == "endpoint returned invalid JSON (HTTP 500; content-type=text/html)"
    assert "private production failure details" not in detail
    assert "secret" not in detail
    assert "example.test" not in detail


def test_http_error_invalid_json_preserves_safe_status_without_body() -> None:
    headers = Message()
    headers["Content-Type"] = "text/plain; charset=utf-8"
    error = HTTPError(
        "https://example.test/admin/launch-readiness",
        401,
        "Unauthorized",
        headers,
        io.BytesIO(b"token mismatch internal detail"),
    )
    with patch("production_smoke.urlopen", side_effect=error):
        with pytest.raises(production_smoke.SmokeError) as exc_info:
            production_smoke.fetch_json("https://example.test", "/admin/launch-readiness", token="secret")

    detail = str(exc_info.value)
    assert detail == "endpoint returned invalid JSON (HTTP 401; content-type=text/plain)"
    assert "token mismatch" not in detail
    assert "secret" not in detail


def test_malformed_content_type_is_not_reflected() -> None:
    response = _Response(
        status=502,
        content_type="text/html\r\nX-Injected: value",
        body=b"gateway error",
    )
    with patch("production_smoke.urlopen", return_value=response):
        with pytest.raises(production_smoke.SmokeError) as exc_info:
            production_smoke.fetch_json("https://example.test", "/admin/launch-readiness")

    assert str(exc_info.value) == "endpoint returned invalid JSON (HTTP 502; content-type=unknown)"
