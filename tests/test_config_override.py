import os

import pytest

import hr_breaker.config as config_module


class TestSettingsOverride:
    def setup_method(self):
        config_module.get_settings.cache_clear()

    def teardown_method(self):
        config_module.get_settings.cache_clear()

    def test_override_restores_original(self):
        original = config_module.get_settings().pro_model
        with config_module.settings_override({"pro_model": "test/model-123"}):
            assert config_module.get_settings().pro_model == "test/model-123"
        assert config_module.get_settings().pro_model == original

    def test_override_api_key(self):
        original = os.environ.get("OPENAI_API_KEY")
        with config_module.settings_override({"api_keys": {"openai": "sk-test-123"}}):
            assert os.environ.get("OPENAI_API_KEY") == "sk-test-123"
        assert os.environ.get("OPENAI_API_KEY") == original

    def test_override_openai_api_base(self):
        original = os.environ.get("OPENAI_API_BASE")
        with config_module.settings_override({"openai_api_base": "https://example.test/v1"}):
            assert os.environ.get("OPENAI_API_BASE") == "https://example.test/v1"
        assert os.environ.get("OPENAI_API_BASE") == original

    def test_override_scoped_openai_api_bases(self):
        original_flash = os.environ.get("FLASH_OPENAI_API_BASE")
        original_embedding = os.environ.get("EMBEDDING_OPENAI_API_BASE")
        with config_module.settings_override({
            "flash_openai_api_base": "https://flash.example.test/v1",
            "embedding_openai_api_base": "https://embed.example.test/v1",
        }):
            assert os.environ.get("FLASH_OPENAI_API_BASE") == "https://flash.example.test/v1"
            assert os.environ.get("EMBEDDING_OPENAI_API_BASE") == "https://embed.example.test/v1"
        assert os.environ.get("FLASH_OPENAI_API_BASE") == original_flash
        assert os.environ.get("EMBEDDING_OPENAI_API_BASE") == original_embedding


    def test_empty_override_is_noop(self):
        original = config_module.get_settings().pro_model
        with config_module.settings_override({}):
            assert config_module.get_settings().pro_model == original

    def test_none_values_ignored(self):
        original = config_module.get_settings().pro_model
        with config_module.settings_override({"pro_model": None}):
            assert config_module.get_settings().pro_model == original
