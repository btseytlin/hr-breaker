import pytest
from httpx import AsyncClient, ASGITransport
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
