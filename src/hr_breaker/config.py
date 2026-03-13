import contextlib
import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from pydantic import AliasChoices, Field
from pydantic_ai_litellm import LiteLLMModel
from pydantic_settings import BaseSettings

import litellm

from hr_breaker import litellm_patch

load_dotenv()

litellm.suppress_debug_info = True
litellm_patch.apply()


def setup_logging() -> logging.Logger:
    general_level = os.getenv("LOG_LEVEL_GENERAL", "WARNING").upper()
    project_level = os.getenv("LOG_LEVEL", "WARNING").upper()

    logging.basicConfig(
        level=getattr(logging, general_level, logging.WARNING),
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%H:%M:%S",
    )

    project_logger = logging.getLogger("hr_breaker")
    project_logger.setLevel(getattr(logging, project_level, logging.WARNING))
    return project_logger


logger = setup_logging()


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

    pro_model: str = "gemini/gemini-3-pro-preview"
    flash_model: str = "gemini/gemini-3-flash-preview"
    reasoning_effort: str = "medium"
    cache_dir: Path = Path(".cache/resumes")
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


@lru_cache
def get_settings() -> Settings:
    return Settings()


def get_pro_model() -> LiteLLMModel:
    return LiteLLMModel(model_name=get_settings().pro_model)


def get_flash_model() -> LiteLLMModel:
    return LiteLLMModel(model_name=get_settings().flash_model)


_FIELD_ENV_MAP = {
    "pro_model": "PRO_MODEL",
    "flash_model": "FLASH_MODEL",
    "embedding_model": "EMBEDDING_MODEL",
    "reasoning_effort": "REASONING_EFFORT",
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
    """Temporarily override settings via env vars, restoring originals on exit."""
    if not overrides:
        yield
        return

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
            os.environ[env_var] = str(key_value)

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
    """Get model settings with reasoning effort config."""
    settings = get_settings()
    if settings.reasoning_effort and settings.reasoning_effort != "none":
        return {"reasoning_effort": settings.reasoning_effort}
    return None
