import pytest

from hr_breaker.agents.extractor import extract_document
import hr_breaker.config as config_module


@pytest.mark.asyncio
async def test_extract_document_fails_fast_without_openai_key(monkeypatch):
    monkeypatch.setenv("FLASH_MODEL", "openai/gpt-5.3-codex")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)
    config_module.clear_settings_cache()
    try:
        with pytest.raises(RuntimeError, match="Missing API key for extraction model 'openai/gpt-5.3-codex'.*OPENAI_API_KEY"):
            await extract_document("Candidate resume text")
    finally:
        config_module.clear_settings_cache()
