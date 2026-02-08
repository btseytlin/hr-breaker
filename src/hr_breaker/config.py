import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()


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


class Settings(BaseModel):
    """Application settings."""

    google_api_key: str = ""
    gemini_pro_model: str = "gemini-3-pro-preview"
    gemini_flash_model: str = "gemini-3-flash-preview"
    gemini_thinking_budget: int | None = 8192
    cache_dir: Path = Path(".cache/resumes")
    output_dir: Path = Path("output")
    max_iterations: int = 5
    pass_threshold: float = 0.7
    fast_mode: bool = True

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
    filter_vector_threshold: float = 0.7
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
    embedding_model: str = "gemini-embedding-001"
    embedding_output_dimensionality: int = 768

    # Agent limits
    agent_name_extractor_chars: int = 2000

    # Translation settings
    default_language: str = "en"
    translation_max_iterations: int = 2


@lru_cache
def get_settings() -> Settings:
    thinking_env = os.getenv("GEMINI_THINKING_BUDGET")
    thinking_budget: int | None = 8192
    if thinking_env is not None:
        thinking_budget = int(thinking_env) if thinking_env else None
    return Settings(
        google_api_key=os.getenv("GOOGLE_API_KEY", ""),
        gemini_pro_model=os.getenv("GEMINI_PRO_MODEL") or "gemini-3-pro-preview",
        gemini_flash_model=os.getenv("GEMINI_FLASH_MODEL") or "gemini-3-flash-preview",
        gemini_thinking_budget=thinking_budget,
        fast_mode=os.getenv("HR_BREAKER_FAST_MODE", "true").lower()
        in ("true", "1", "yes"),
        # Scraper settings
        scraper_httpx_timeout=float(os.getenv("SCRAPER_HTTPX_TIMEOUT", "15")),
        scraper_wayback_timeout=float(os.getenv("SCRAPER_WAYBACK_TIMEOUT", "10")),
        scraper_playwright_timeout=int(
            os.getenv("SCRAPER_PLAYWRIGHT_TIMEOUT", "30000")
        ),
        scraper_httpx_max_retries=int(os.getenv("SCRAPER_HTTPX_MAX_RETRIES", "3")),
        scraper_wayback_max_age_days=int(
            os.getenv("SCRAPER_WAYBACK_MAX_AGE_DAYS", "30")
        ),
        scraper_min_text_length=int(os.getenv("SCRAPER_MIN_TEXT_LENGTH", "200")),
        # Filter thresholds
        filter_hallucination_threshold=float(
            os.getenv("FILTER_HALLUCINATION_THRESHOLD", "0.9")
        ),
        filter_keyword_threshold=float(os.getenv("FILTER_KEYWORD_THRESHOLD", "0.25")),
        filter_llm_threshold=float(os.getenv("FILTER_LLM_THRESHOLD", "0.7")),
        filter_vector_threshold=float(os.getenv("FILTER_VECTOR_THRESHOLD", "0.4")),
        filter_ai_generated_threshold=float(
            os.getenv("FILTER_AI_GENERATED_THRESHOLD", "0.4")
        ),
        # Resume length limits
        resume_max_chars=int(os.getenv("RESUME_MAX_CHARS", "4500")),
        resume_max_words=int(os.getenv("RESUME_MAX_WORDS", "520")),
        resume_page2_overflow_chars=int(
            os.getenv("RESUME_PAGE2_OVERFLOW_CHARS", "1000")
        ),
        # Keyword matcher params
        keyword_tfidf_max_features=int(os.getenv("KEYWORD_TFIDF_MAX_FEATURES", "200")),
        keyword_tfidf_cutoff=float(os.getenv("KEYWORD_TFIDF_CUTOFF", "0.1")),
        keyword_max_missing_display=int(os.getenv("KEYWORD_MAX_MISSING_DISPLAY", "10")),
        # Embedding settings
        embedding_model=os.getenv("EMBEDDING_MODEL", "gemini-embedding-001"),
        embedding_output_dimensionality=int(
            os.getenv("EMBEDDING_OUTPUT_DIMENSIONALITY", "768")
        ),
        # Agent limits
        agent_name_extractor_chars=int(os.getenv("AGENT_NAME_EXTRACTOR_CHARS", "2000")),
        # Translation settings
        default_language=os.getenv("DEFAULT_LANGUAGE", "en"),
        translation_max_iterations=int(os.getenv("TRANSLATION_MAX_ITERATIONS", "2")),
    )


def get_model_settings() -> dict[str, Any] | None:
    """Get GoogleModelSettings with thinking config if budget is set."""
    settings = get_settings()
    if settings.gemini_thinking_budget is not None:
        return {
            "google_thinking_config": {
                "thinking_budget": settings.gemini_thinking_budget
            }
        }
    return None
