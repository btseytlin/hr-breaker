import os

import pytest

import hr_breaker.config as config_module
from hr_breaker.utils.api_key import sanitize_api_key


def test_plain_ascii_passthrough():
    assert sanitize_api_key("sk-abc123", "openrouter") == "sk-abc123"


def test_strips_leading_trailing_whitespace():
    assert sanitize_api_key("  sk-abc  ", "openrouter") == "sk-abc"


def test_strips_nbsp_prefix():
    assert sanitize_api_key("\u00a0\u00a0sk-abc", "openrouter") == "sk-abc"


def test_strips_bom():
    assert sanitize_api_key("\ufeffsk-abc", "openrouter") == "sk-abc"


def test_strips_zero_width():
    assert sanitize_api_key("\u200bsk-abc\u200b", "openrouter") == "sk-abc"


def test_strips_mixed_invisible_and_whitespace():
    assert sanitize_api_key("  \ufeff\u00a0sk-abc\u200b  ", "gemini") == "sk-abc"


def test_rejects_interior_non_ascii():
    with pytest.raises(ValueError) as exc:
        sanitize_api_key("sk-абc", "openrouter")
    msg = str(exc.value)
    assert "openrouter" in msg
    assert "non-ASCII" in msg
    assert hex(0x430) in msg  # 'а' — Cyrillic small a


def test_rejects_empty_after_strip():
    with pytest.raises(ValueError) as exc:
        sanitize_api_key("\u00a0\u00a0", "openrouter")
    assert "empty" in str(exc.value)
    assert "openrouter" in str(exc.value)


def test_rejects_empty_input():
    with pytest.raises(ValueError) as exc:
        sanitize_api_key("", "gemini")
    assert "empty" in str(exc.value)


def test_rejects_smart_quote():
    with pytest.raises(ValueError) as exc:
        sanitize_api_key("sk\u201cabc", "openrouter")
    msg = str(exc.value)
    assert "non-ASCII" in msg
    assert hex(0x201C) in msg


class TestSettingsOverrideSanitization:
    def setup_method(self):
        config_module.get_settings.cache_clear()
        self._saved = os.environ.get("OPENROUTER_API_KEY")

    def teardown_method(self):
        config_module.get_settings.cache_clear()
        if self._saved is None:
            os.environ.pop("OPENROUTER_API_KEY", None)
        else:
            os.environ["OPENROUTER_API_KEY"] = self._saved

    def test_settings_override_sanitizes_nbsp_key(self):
        with config_module.settings_override({"api_keys": {"openrouter": "\u00a0\u00a0sk-abc"}}):
            assert os.environ["OPENROUTER_API_KEY"] == "sk-abc"

    def test_settings_override_rejects_non_ascii_key(self):
        pre_value = os.environ.get("OPENROUTER_API_KEY")
        with pytest.raises(ValueError) as exc:
            with config_module.settings_override({"api_keys": {"openrouter": "sk-абc"}}):
                pass
        msg = str(exc.value)
        assert "openrouter" in msg
        assert "non-ASCII" in msg
        assert os.environ.get("OPENROUTER_API_KEY") == pre_value


class TestSanitizeEnvApiKeys:
    def setup_method(self):
        self._saved = os.environ.get("OPENROUTER_API_KEY")

    def teardown_method(self):
        if self._saved is None:
            os.environ.pop("OPENROUTER_API_KEY", None)
        else:
            os.environ["OPENROUTER_API_KEY"] = self._saved

    def test_rejects_corrupt_env(self):
        os.environ["OPENROUTER_API_KEY"] = "sk-абc"
        with pytest.raises(ValueError) as exc:
            config_module.sanitize_env_api_keys()
        assert "openrouter" in str(exc.value)
        assert "non-ASCII" in str(exc.value)

    def test_normalizes_nbsp_in_env(self):
        os.environ["OPENROUTER_API_KEY"] = "\u00a0sk-clean"
        config_module.sanitize_env_api_keys()
        assert os.environ["OPENROUTER_API_KEY"] == "sk-clean"

    def test_leaves_clean_key_unchanged(self):
        os.environ["OPENROUTER_API_KEY"] = "sk-already-clean"
        config_module.sanitize_env_api_keys()
        assert os.environ["OPENROUTER_API_KEY"] == "sk-already-clean"
