import pytest

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
