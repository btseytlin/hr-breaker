from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from hr_breaker.server import app


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_settings_returns_all_configurable_fields(client):
    resp = await client.get("/api/settings")
    assert resp.status_code == 200
    data = resp.json()
    # Existing
    assert "language_modes" in data
    assert data["llm_backend"] in {"litellm", "codex_cli"}
    assert "codex_model" in data
    assert "codex_flash_model" in data
    assert "codex_reasoning_effort" in data
    assert "pro_model" in data
    assert "flash_model" in data
    assert "max_iterations" in data
    # New
    assert "embedding_model" in data
    assert "reasoning_effort" in data
    assert "filter_thresholds" in data
    assert "api_keys_set" in data
    # Thresholds structure
    thresholds = data["filter_thresholds"]
    for key in ["hallucination", "keyword", "llm", "vector", "ai_generated", "translation"]:
        assert key in thresholds
    # API keys are booleans
    keys = data["api_keys_set"]
    for key in ["gemini", "openrouter", "openai", "anthropic", "moonshot"]:
        assert isinstance(keys[key], bool)


@pytest.mark.asyncio
async def test_codex_models_returns_current_cli_catalog(client):
    catalog = {
        "models": [
            {
                "value": "gpt-current",
                "label": "GPT Current",
                "description": "Current model.",
                "is_default": True,
                "default_reasoning_effort": "medium",
                "supported_reasoning_efforts": ["low", "medium", "high"],
            }
        ],
        "default_model": "gpt-current",
    }
    with patch(
        "hr_breaker.services.codex_cli.get_codex_model_catalog",
        new=AsyncMock(return_value=catalog),
    ):
        response = await client.get("/api/codex/models")

    assert response.status_code == 200
    assert response.json() == catalog
