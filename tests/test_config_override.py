import os

import pytest

from hr_breaker.config import get_settings, settings_override


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
