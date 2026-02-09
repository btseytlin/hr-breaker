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

    # API keys
    gemini_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    )
    openai_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("OPENAI_API_KEY"),
    )
    openai_base_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("OPENAI_BASE_URL"),
    )
    anthropic_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("ANTHROPIC_API_KEY"),
    )

    # Model provider (e.g. "anthropic", "openai", "" for default/google)
    model_provider: str = Field(
        default="",
        validation_alias=AliasChoices("MODEL_PROVIDER"),
    )

    pro_model: str = Field(
        default="gemini/gemini-3-pro-preview",
        validation_alias=AliasChoices("PRO_MODEL", "GEMINI_PRO_MODEL"),
    )
    flash_model: str = Field(
        default="gemini/gemini-3-flash-preview",
        validation_alias=AliasChoices("FLASH_MODEL", "GEMINI_FLASH_MODEL"),
    )
    reasoning_effort: str = "medium"
    cache_dir: Path = Path(".cache/resumes")
    output_dir: Path = Path("output")
    max_iterations: int = 5
    max_result_retries: int = 3
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

    # Resume length limits
    resume_max_chars: int = 4500
    resume_max_words: int = 520
    resume_page2_overflow_chars: int = 1000

    # Keyword matcher params
    keyword_tfidf_max_features: int = 200
    keyword_tfidf_cutoff: float = 0.1
    keyword_max_missing_display: int = 10

    # Embedding settings
    embedding_model: str = "gemini/text-embedding-004"
    embedding_output_dimensionality: int = 768

    # Agent limits
    agent_name_extractor_chars: int = 2000

    # Translation settings
    default_language: str = "en"
    translation_max_iterations: int = 2

    # Retry settings
    retry_max_attempts: int = 5
    retry_max_wait: float = 60.0

    def model_post_init(self, __context: Any) -> None:
        if self.gemini_api_key and "GEMINI_API_KEY" not in os.environ:
            os.environ["GEMINI_API_KEY"] = self.gemini_api_key
        if self.openai_api_key and "OPENAI_API_KEY" not in os.environ:
            os.environ["OPENAI_API_KEY"] = self.openai_api_key
        if self.openai_base_url and "OPENAI_BASE_URL" not in os.environ:
            os.environ["OPENAI_BASE_URL"] = self.openai_base_url
        if self.anthropic_api_key and "ANTHROPIC_API_KEY" not in os.environ:
            os.environ["ANTHROPIC_API_KEY"] = self.anthropic_api_key

    def _get_full_model_name(self, model: str) -> str:
        """Construct full litellm model name with provider prefix if needed."""
        provider = self.model_provider.strip().lower()
        if not provider:
            # No provider set — use model string as-is (assumes litellm format like "gemini/...")
            return model
        # If the model already has a provider prefix (contains "/"), use as-is
        if "/" in model:
            return model
        # Otherwise prepend provider: e.g. "anthropic/claude-sonnet-4-20250514"
        return f"{provider}/{model}"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def get_pro_model() -> LiteLLMModel:
    settings = get_settings()
    return LiteLLMModel(model_name=settings._get_full_model_name(settings.pro_model))


def get_flash_model() -> LiteLLMModel:
    settings = get_settings()
    return LiteLLMModel(model_name=settings._get_full_model_name(settings.flash_model))


def get_model_settings() -> dict[str, Any] | None:
    """Get model settings with reasoning effort config.
    
    Only applies Google thinking config when using Google provider.
    Anthropic/OpenAI don't support reasoning_effort.
    """
    settings = get_settings()
    provider = settings.model_provider.strip().lower()
    # Only apply reasoning_effort for Google/Gemini (default) provider
    if provider in ("", "google-gla", "gemini"):
        if settings.reasoning_effort and settings.reasoning_effort != "none":
            return {"reasoning_effort": settings.reasoning_effort}
    return None