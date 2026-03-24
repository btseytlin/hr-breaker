import os

import pytest

from hr_breaker.config import get_model_settings, get_settings, settings_override


class TestSettingsOverride:
    def setup_method(self):
        get_settings.cache_clear()

    def teardown_method(self):
        get_settings.cache_clear()

    def test_override_restores_original(self):
        original = get_settings().pro_model
        with settings_override({"pro_model": "test/model-123"}):
            assert get_settings().pro_model == "test/model-123"
        assert get_settings().pro_model == original

    def test_override_api_key(self):
        original = os.environ.get("OPENAI_API_KEY")
        with settings_override({"api_keys": {"openai": "sk-test-123"}}):
            assert os.environ.get("OPENAI_API_KEY") == "sk-test-123"
        assert os.environ.get("OPENAI_API_KEY") == original

    def test_empty_override_is_noop(self):
        original = get_settings().pro_model
        with settings_override({}):
            assert get_settings().pro_model == original

    def test_none_values_ignored(self):
        original = get_settings().pro_model
        with settings_override({"pro_model": None}):
            assert get_settings().pro_model == original


def test_max_tokens_from_env(monkeypatch):
    monkeypatch.setenv("MAX_TOKENS", "8192")
    get_settings.cache_clear()

    assert get_settings().max_tokens == 8192
    assert get_model_settings() == {
        "reasoning_effort": get_settings().reasoning_effort,
        "max_tokens": 8192,
    }

    get_settings.cache_clear()


def test_max_output_tokens_alias_from_env(monkeypatch):
    monkeypatch.delenv("MAX_TOKENS", raising=False)
    monkeypatch.setenv("MAX_OUTPUT_TOKENS", "4096")
    get_settings.cache_clear()

    assert get_settings().max_tokens == 4096

    get_settings.cache_clear()
