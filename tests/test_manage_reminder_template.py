from __future__ import annotations

import httpx
import pytest

from scripts.manage_reminder_template import (
    TEMPLATE_NAME,
    TemplateManagementError,
    VARIANTS,
    manage_templates,
)


ENVIRONMENT = {
    "WABA_ID": "waba-test-id",
    "WHATSAPP_TOKEN": "token-that-is-never-returned",
    "WHATSAPP_API_VERSION": "v22.0",
}


def test_check_is_read_only_and_reports_missing_languages() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"data": []})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = manage_templates(environment=ENVIRONMENT, client=client)

    assert [request.method for request in requests] == ["GET"]
    assert set(result["variants"]) == {variant.language for variant in VARIANTS}
    assert all(item["status"] == "MISSING" for item in result["variants"].values())
    assert "token-that-is-never-returned" not in str(result)


def test_apply_submits_only_missing_variants_with_utility_payload() -> None:
    payloads: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{
                "id": "existing-ar", "name": TEMPLATE_NAME, "language": "ar", "status": "APPROVED",
            }]})
        payloads.append(__import__("json").loads(request.content))
        return httpx.Response(200, json={"id": f"created-{len(payloads)}", "status": "PENDING"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = manage_templates(apply=True, environment=ENVIRONMENT, client=client)

    assert len(payloads) == len(VARIANTS) - 1
    assert all(payload["name"] == TEMPLATE_NAME and payload["category"] == "UTILITY" for payload in payloads)
    assert result["variants"]["ar"]["action"] == "existing"
    assert result["variants"]["de"]["action"] == "submitted"


def test_provider_errors_are_bounded() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "secret provider detail"}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(TemplateManagementError, match="meta_template_request_failed") as caught:
            manage_templates(environment=ENVIRONMENT, client=client)

    assert "secret provider detail" not in str(caught.value)


def test_configuration_is_fail_closed() -> None:
    with pytest.raises(TemplateManagementError, match="missing_meta_configuration"):
        manage_templates(environment={})


def test_template_variables_are_sequential_and_have_examples() -> None:
    for variant in VARIANTS:
        positions = [variant.body.index(f"{{{{{number}}}}}") for number in (1, 2, 3)]
        assert positions == sorted(positions)
        assert len(variant.example) == 3
