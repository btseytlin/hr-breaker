import contextlib
import logging
import os
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from pydantic import AliasChoices, Field
from pydantic_ai_litellm import LiteLLMModel
from pydantic_settings import BaseSettings

import litellm

from hr_breaker import litellm_patch
from hr_breaker.utils.api_key import sanitize_api_key

load_dotenv()


_ENV_API_KEY_PROVIDERS: tuple[tuple[str, str], ...] = (
    ("GEMINI_API_KEY", "gemini"),
    ("GOOGLE_API_KEY", "gemini"),
    ("OPENROUTER_API_KEY", "openrouter"),
    ("OPENAI_API_KEY", "openai"),
    ("ANTHROPIC_API_KEY", "anthropic"),
    ("MOONSHOT_API_KEY", "moonshot"),
)


def sanitize_env_api_keys() -> None:
    """Sanitize all API key env vars in-place. Raises ValueError on non-ASCII residue."""
    for env_var, provider in _ENV_API_KEY_PROVIDERS:
        raw = os.environ.get(env_var)
        if raw:
            os.environ[env_var] = sanitize_api_key(raw, provider)


sanitize_env_api_keys()

litellm.suppress_debug_info = True
litellm_patch.apply()


def setup_logging() -> logging.Logger:
    general_level = os.getenv("LOG_LEVEL_GENERAL", "WARNING").upper()
    project_level = os.getenv("LOG_LEVEL", "WARNING").upper()

    logging.basicConfig(
        level=getattr(logging, general_level, logging.WARNING),
        format="%(asctime)s.%(msecs)03d [%(levelname)s] %(name)s - %(message)s",
        datefmt="%H:%M:%S",
    )

    project_logger = logging.getLogger("hr_breaker")
    project_logger.setLevel(getattr(logging, project_level, logging.WARNING))
    return project_logger


logger = setup_logging()


_SETTINGS_OVERRIDE_LOCK = threading.RLock()


class Settings(BaseSettings):
    """Application settings. Reads from env vars (uppercased field names)."""

    # API keys (accepts GOOGLE_API_KEY as fallback for backward compat with Gemini)
    gemini_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    )
    moonshot_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("MOONSHOT_API_KEY"),
    )
    openai_api_base: str | None = Field(
        default=None,
        validation_alias=AliasChoices("OPENAI_API_BASE", "OPENAI_BASE_URL"),
    )
    pro_openai_api_base: str | None = Field(
        default=None,
        validation_alias=AliasChoices("PRO_OPENAI_API_BASE"),
    )
    flash_openai_api_base: str | None = Field(
        default=None,
        validation_alias=AliasChoices("FLASH_OPENAI_API_BASE"),
    )
    embedding_openai_api_base: str | None = Field(
        default=None,
        validation_alias=AliasChoices("EMBEDDING_OPENAI_API_BASE"),
    )

    pro_model: str = "gemini/gemini-3-pro-preview"
    flash_model: str = "gemini/gemini-3-flash-preview"
    reasoning_effort: str = "medium"
    max_tokens: int | None = Field(
        default=None,
        validation_alias=AliasChoices("MAX_TOKENS", "MAX_OUTPUT_TOKENS"),
        gt=0,
    )
    cache_dir: Path = Path(".cache/resumes")
    profile_dir: Path = Path(".cache/profiles")
    output_dir: Path = Path("output")
    max_iterations: int = 5
    pass_threshold: float = 0.7
    fast_mode: bool = Field(
        default=True,
        validation_alias=AliasChoices("fast_mode", "HR_BREAKER_FAST_MODE"),
    )

    # Scraper settings
    scraper_httpx_timeout: float = 15.0
    scraper_wayback_timeout: float = 10.0
    scraper_playwright_timeout: int = 30000
    scraper_httpx_max_retries: int = 3
    scraper_wayback_max_age_days: int = 30
    scraper_min_text_length: int = 200

    # Filter thresholds
    filter_hallucination_threshold: float = 0.9
    filter_keyword_threshold: float = 0.25
    filter_llm_threshold: float = 0.7
    filter_vector_threshold: float = 0.4
    filter_ai_generated_threshold: float = 0.4
    filter_translation_threshold: float = 0.95

    # Resume length limits
    resume_max_chars: int = 4500
    resume_max_words: int = 520
    resume_page2_overflow_chars: int = 1000

    # Keyword matcher params
    keyword_tfidf_max_features: int = 200
    keyword_tfidf_cutoff: float = 0.1
    keyword_max_missing_display: int = 10

    # Embedding settings
    embedding_model: str = "openrouter/google/gemini-embedding-001"
    embedding_output_dimensionality: int = 768

    # Profile archive settings
    profile_retrieval_top_k: int = 4
    profile_source_max_chars: int = 12000
    profile_snippet_max_chars: int = 700

    # Agent limits
    agent_name_extractor_chars: int = 2000

    # Language settings
    default_language: str = "from_job"

    # Retry settings
    retry_max_attempts: int = 5
    retry_max_wait: float = 60.0

    def model_post_init(self, __context: Any) -> None:
        if self.gemini_api_key and "GEMINI_API_KEY" not in os.environ:
            os.environ["GEMINI_API_KEY"] = self.gemini_api_key
        if self.moonshot_api_key and "MOONSHOT_API_KEY" not in os.environ:
            os.environ["MOONSHOT_API_KEY"] = self.moonshot_api_key
        if self.openai_api_base and "OPENAI_API_BASE" not in os.environ:
            os.environ["OPENAI_API_BASE"] = self.openai_api_base
        if self.pro_openai_api_base and "PRO_OPENAI_API_BASE" not in os.environ:
            os.environ["PRO_OPENAI_API_BASE"] = self.pro_openai_api_base
        if self.flash_openai_api_base and "FLASH_OPENAI_API_BASE" not in os.environ:
            os.environ["FLASH_OPENAI_API_BASE"] = self.flash_openai_api_base
        if self.embedding_openai_api_base and "EMBEDDING_OPENAI_API_BASE" not in os.environ:
            os.environ["EMBEDDING_OPENAI_API_BASE"] = self.embedding_openai_api_base


@lru_cache
def get_settings() -> Settings:
    return Settings()


def clear_settings_cache() -> None:
    """Clear cached settings (for tests)."""
    get_settings.cache_clear()


def required_api_key_env_for_model(model_name: str) -> str | None:
    if model_name.startswith("openrouter/"):
        return "OPENROUTER_API_KEY"
    if model_name.startswith("gemini/") or "gemini" in model_name:
        return "GEMINI_API_KEY"
    if model_name.startswith("openai/"):
        return "OPENAI_API_KEY"
    if model_name.startswith("anthropic/"):
        return "ANTHROPIC_API_KEY"
    if model_name.startswith("moonshot/"):
        return "MOONSHOT_API_KEY"
    return None


def has_api_key_for_model(model_name: str) -> bool:
    env_var = required_api_key_env_for_model(model_name)
    if env_var == "GEMINI_API_KEY":
        return bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
    if env_var is not None:
        return bool(os.environ.get(env_var))
    return bool(
        os.environ.get("GEMINI_API_KEY")
        or os.environ.get("GOOGLE_API_KEY")
        or os.environ.get("OPENROUTER_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("ANTHROPIC_API_KEY")
        or os.environ.get("MOONSHOT_API_KEY")
    )


def _litellm_api_base_for_model(scope: str, model_name: str) -> str | None:
    settings = get_settings()
    if not model_name.startswith("openai/"):
        return None
    scoped_field = {
        "pro": "pro_openai_api_base",
        "flash": "flash_openai_api_base",
        "embedding": "embedding_openai_api_base",
    }[scope]
    return getattr(settings, scoped_field) or settings.openai_api_base


def _litellm_model(scope: str, model_name: str) -> LiteLLMModel:
    return LiteLLMModel(model_name=model_name, api_base=_litellm_api_base_for_model(scope, model_name))


def get_pro_model() -> LiteLLMModel:
    return _litellm_model("pro", get_settings().pro_model)


def get_flash_model() -> LiteLLMModel:
    return _litellm_model("flash", get_settings().flash_model)


def get_embedding_api_base() -> str | None:
    settings = get_settings()
    return _litellm_api_base_for_model("embedding", settings.embedding_model)


_FIELD_ENV_MAP = {
    "pro_model": "PRO_MODEL",
    "flash_model": "FLASH_MODEL",
    "embedding_model": "EMBEDDING_MODEL",
    "reasoning_effort": "REASONING_EFFORT",
    "max_tokens": "MAX_TOKENS",
    "openai_api_base": "OPENAI_API_BASE",
    "pro_openai_api_base": "PRO_OPENAI_API_BASE",
    "flash_openai_api_base": "FLASH_OPENAI_API_BASE",
    "embedding_openai_api_base": "EMBEDDING_OPENAI_API_BASE",
    "filter_hallucination_threshold": "FILTER_HALLUCINATION_THRESHOLD",
    "filter_keyword_threshold": "FILTER_KEYWORD_THRESHOLD",
    "filter_llm_threshold": "FILTER_LLM_THRESHOLD",
    "filter_vector_threshold": "FILTER_VECTOR_THRESHOLD",
    "filter_ai_generated_threshold": "FILTER_AI_GENERATED_THRESHOLD",
    "filter_translation_threshold": "FILTER_TRANSLATION_THRESHOLD",
}

_API_KEY_ENV_MAP = {
    "gemini": "GEMINI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "moonshot": "MOONSHOT_API_KEY",
}


@contextlib.contextmanager
def settings_override(overrides: dict | None):
    """Temporarily override settings via env vars, restoring originals on exit.

    Overrides are serialized because the app still relies on process-global env vars
    for downstream model/provider clients. This avoids cross-request leakage.
    """
    if not overrides:
        yield
        return

    with _SETTINGS_OVERRIDE_LOCK:
        saved: dict[str, str | None] = {}

        # Apply field overrides
        for field, value in overrides.items():
            if field == "api_keys":
                continue
            if value is None:
                continue
            env_var = _FIELD_ENV_MAP.get(field)
            if env_var is None:
                continue
            saved[env_var] = os.environ.get(env_var)
            os.environ[env_var] = str(value)

        # Apply API key overrides
        api_keys = overrides.get("api_keys")
        if api_keys:
            for provider, key_value in api_keys.items():
                if key_value is None:
                    continue
                env_var = _API_KEY_ENV_MAP.get(provider)
                if env_var is None:
                    continue
                saved[env_var] = os.environ.get(env_var)
                try:
                    os.environ[env_var] = sanitize_api_key(str(key_value), provider)
                except ValueError:
                    # Restore any env vars already applied so the failed
                    # override does not partially leak.
                    for restore_var, original in saved.items():
                        if original is None:
                            os.environ.pop(restore_var, None)
                        else:
                            os.environ[restore_var] = original
                    raise

        get_settings.cache_clear()
        try:
            yield
        finally:
            for env_var, original in saved.items():
                if original is None:
                    os.environ.pop(env_var, None)
                else:
                    os.environ[env_var] = original
            get_settings.cache_clear()


def get_model_settings() -> dict[str, Any] | None:
    """Get model settings with reasoning effort and token cap config."""
    settings = get_settings()
    model_settings: dict[str, Any] = {}

    if settings.reasoning_effort and settings.reasoning_effort != "none":
        model_settings["reasoning_effort"] = settings.reasoning_effort
    if settings.max_tokens is not None:
        model_settings["max_tokens"] = settings.max_tokens

    return model_settings or None
