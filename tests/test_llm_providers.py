"""Tests for provider model discovery."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from hr_breaker.services.llm_providers import (
    _fetch_gemini_models,
    fetch_provider_catalog,
    _fetch_anthropic_models,
    _litellm_prefix,
)


@pytest.mark.asyncio
async def test_fetch_provider_catalog_uses_google_api_key_fallback_for_gemini(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "google-key")

    with patch(
        "hr_breaker.services.llm_providers._fetch_gemini_models",
        new=AsyncMock(return_value=([{"value": "gemini/gemini-2.5-pro", "label": "Gemini 2.5 Pro"}], [])),
    ) as mock_fetch:
        result = await fetch_provider_catalog("gemini")

    mock_fetch.assert_awaited_once_with("google-key")
    assert result["status"]["state"] == "connected"
    assert result["chat_models"] == [{"value": "gemini/gemini-2.5-pro", "label": "Gemini 2.5 Pro"}]
    assert result["embedding_models"] == []


@pytest.mark.asyncio
async def test_fetch_gemini_models_uses_query_param_auth_only():
    recorded = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"models": []}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, params=None, headers=None):
            recorded["url"] = url
            recorded["params"] = params
            recorded["headers"] = headers
            return FakeResponse()

    with patch("hr_breaker.services.llm_providers.httpx.AsyncClient", return_value=FakeClient()):
        await _fetch_gemini_models("gem-test")

    assert recorded == {
        "url": "https://generativelanguage.googleapis.com/v1beta/models",
        "params": {"key": "gem-test"},
        "headers": None,
    }


@pytest.mark.asyncio
async def test_fetch_provider_catalog_uses_custom_base_url_for_openai_compatible_provider():
    with patch(
        "hr_breaker.services.llm_providers._fetch_openai_models",
        new=AsyncMock(return_value=([{"value": "openai/gpt-4.1-mini", "label": "gpt-4.1-mini"}], [])),
    ) as mock_fetch:
        result = await fetch_provider_catalog(
            provider="custom",
            api_key="sk-test",
            base_url="https://example.test/v1/",
        )

    mock_fetch.assert_awaited_once_with("sk-test", "https://example.test/v1", litellm_prefix="openai/")
    assert result["status"]["state"] == "connected"
    assert result["chat_models"] == [{"value": "openai/gpt-4.1-mini", "label": "gpt-4.1-mini"}]


@pytest.mark.asyncio
async def test_fetch_provider_catalog_surfaces_http_error_detail():
    response = httpx.Response(
        401,
        request=httpx.Request("GET", "https://example.test/v1/models"),
        json={"error": {"message": "Invalid API key"}},
    )
    error = httpx.HTTPStatusError("Unauthorized", request=response.request, response=response)

    with patch(
        "hr_breaker.services.llm_providers._fetch_openai_models",
        new=AsyncMock(side_effect=error),
    ):
        result = await fetch_provider_catalog(provider="openai", api_key="sk-test")

    assert result["status"] == {
        "state": "warning",
        "message": "Connection failed (401)",
        "detail": "Invalid API key",
    }
    assert result["chat_models"] == []
    assert result["embedding_models"] == []


@pytest.mark.asyncio
async def test_fetch_provider_catalog_surfaces_request_error_detail():
    error = httpx.RequestError(
        "Timed out while connecting",
        request=httpx.Request("GET", "https://example.test/v1/models"),
    )

    with patch(
        "hr_breaker.services.llm_providers._fetch_openai_models",
        new=AsyncMock(side_effect=error),
    ):
        result = await fetch_provider_catalog(provider="openai", api_key="sk-test")

    assert result["status"] == {
        "state": "warning",
        "message": "Connection failed",
        "detail": "Timed out while connecting",
    }
    assert result["chat_models"] == []
    assert result["embedding_models"] == []


@pytest.mark.asyncio
async def test_fetch_provider_catalog_uses_anthropic_models_endpoint():
    with patch(
        "hr_breaker.services.llm_providers._fetch_anthropic_models",
        new=AsyncMock(return_value=([{"value": "anthropic/claude-sonnet-4", "label": "Claude Sonnet 4"}], [])),
    ) as mock_fetch:
        result = await fetch_provider_catalog(provider="anthropic", api_key="ant-test")

    mock_fetch.assert_awaited_once_with("ant-test", "https://api.anthropic.com")
    assert result["status"]["state"] == "connected"
    assert result["chat_models"] == [{"value": "anthropic/claude-sonnet-4", "label": "Claude Sonnet 4"}]
    assert result["embedding_models"] == []


@pytest.mark.asyncio
async def test_fetch_anthropic_models_uses_required_headers_only():
    recorded = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": [
                    {"id": "claude-sonnet-4", "display_name": "Claude Sonnet 4"},
                ]
            }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers=None):
            recorded["url"] = url
            recorded["headers"] = headers
            return FakeResponse()

    with patch("hr_breaker.services.llm_providers.httpx.AsyncClient", return_value=FakeClient()):
        chat, embed = await _fetch_anthropic_models("ant-test", "https://api.anthropic.com")

    assert chat == [{"value": "anthropic/claude-sonnet-4", "label": "Claude Sonnet 4"}]
    assert embed == []
    assert recorded == {
        "url": "https://api.anthropic.com/v1/models",
        "headers": {
            "x-api-key": "ant-test",
            "anthropic-version": "2023-06-01",
        },
    }


@pytest.mark.asyncio
async def test_fetch_provider_catalog_uses_moonshot_openai_compatible_endpoint():
    with patch(
        "hr_breaker.services.llm_providers._fetch_openai_models",
        new=AsyncMock(return_value=([{"value": "moonshot/kimi-k2", "label": "kimi-k2"}], [])),
    ) as mock_fetch:
        result = await fetch_provider_catalog(provider="moonshot", api_key="moon-test")

    mock_fetch.assert_awaited_once_with("moon-test", "https://api.moonshot.ai/v1", litellm_prefix="moonshot/")
    assert result["status"]["state"] == "connected"
    assert result["chat_models"] == [{"value": "moonshot/kimi-k2", "label": "kimi-k2"}]


def test_litellm_prefix_supports_moonshot():
    assert _litellm_prefix("moonshot") == "moonshot/"

@pytest.mark.asyncio
async def test_fetch_anthropic_models_avoids_double_v1_suffix():
    recorded = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": []}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers=None):
            recorded["url"] = url
            recorded["headers"] = headers
            return FakeResponse()

    with patch("hr_breaker.services.llm_providers.httpx.AsyncClient", return_value=FakeClient()):
        await _fetch_anthropic_models("ant-test", "https://api.anthropic.com/v1/")

    assert recorded["url"] == "https://api.anthropic.com/v1/models"