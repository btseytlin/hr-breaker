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


def get_language_safe(code: str | None) -> Language:
    """Get a Language by its ISO code, defaulting to English on unknown codes."""
    if not code:
        return DEFAULT_LANGUAGE
    for lang in SUPPORTED_LANGUAGES:
        if lang.code == code:
            return lang
    return DEFAULT_LANGUAGE


LANGUAGE_MODES = [
    {"value": "from_job", "label": "From job posting"},
    {"value": "from_resume", "label": "From resume"},
] + [
    {"value": lang.code, "label": lang.native_name}
    for lang in SUPPORTED_LANGUAGES
]


def resolve_target_language(
    mode: str, job_lang_code: str | None, resume_lang_code: str | None
) -> Language:
    """Resolve a language mode string to a concrete Language."""
    if mode == "from_job":
        return get_language_safe(job_lang_code)
    if mode == "from_resume":
        return get_language_safe(resume_lang_code)
    return get_language_safe(mode)
