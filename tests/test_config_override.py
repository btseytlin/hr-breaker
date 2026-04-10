import os
import threading
import time

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


    def test_overrides_are_serialized_across_threads(self):
        entered: list[str] = []
        first_ready = threading.Event()
        release_first = threading.Event()
        second_done = threading.Event()
        observed: dict[str, str] = {}

        def first_worker():
            with config_module.settings_override({"openai_api_base": "https://first.example.test/v1"}):
                entered.append("first")
                first_ready.set()
                assert os.environ.get("OPENAI_API_BASE") == "https://first.example.test/v1"
                assert release_first.wait(timeout=2)

        def second_worker():
            assert first_ready.wait(timeout=2)
            with config_module.settings_override({"openai_api_base": "https://second.example.test/v1"}):
                entered.append("second")
                observed["value"] = os.environ.get("OPENAI_API_BASE") or ""
            second_done.set()

        first_thread = threading.Thread(target=first_worker)
        second_thread = threading.Thread(target=second_worker)
        first_thread.start()
        second_thread.start()

        assert first_ready.wait(timeout=2)
        time.sleep(0.05)
        assert entered == ["first"]

        release_first.set()
        first_thread.join(timeout=2)
        second_thread.join(timeout=2)

        assert second_done.is_set()
        assert entered == ["first", "second"]
        assert observed["value"] == "https://second.example.test/v1"


def test_max_tokens_from_env(monkeypatch):
    monkeypatch.delenv("MAX_OUTPUT_TOKENS", raising=False)
    monkeypatch.setenv("MAX_TOKENS", "8192")
    config_module.get_settings.cache_clear()

    assert config_module.get_settings().max_tokens == 8192
    assert config_module.get_model_settings() == {
        "reasoning_effort": config_module.get_settings().reasoning_effort,
        "max_tokens": 8192,
    }

    config_module.get_settings.cache_clear()


def test_max_output_tokens_alias_from_env(monkeypatch):
    monkeypatch.delenv("MAX_TOKENS", raising=False)
    monkeypatch.setenv("MAX_OUTPUT_TOKENS", "4096")
    config_module.get_settings.cache_clear()

    assert config_module.get_settings().max_tokens == 4096

    config_module.get_settings.cache_clear()
