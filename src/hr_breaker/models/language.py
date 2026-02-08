"""Language definitions for resume translation."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Language:
    """A target language for resume output."""

    code: str  # ISO 639-1 code: "en", "ru"
    english_name: str  # "English", "Russian"
    native_name: str  # "English", "Русский"


SUPPORTED_LANGUAGES = [
    Language(code="en", english_name="English", native_name="English"),
    Language(code="ru", english_name="Russian", native_name="Русский"),
]

DEFAULT_LANGUAGE = SUPPORTED_LANGUAGES[0]  # English


def get_language(code: str) -> Language:
    """Get a Language by its ISO code. Raises ValueError if unsupported."""
    for lang in SUPPORTED_LANGUAGES:
        if lang.code == code:
            return lang
    raise ValueError(f"Unsupported language: {code}")
