"""Tests for translation functionality: language model, orchestration, PDF naming."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from pathlib import Path

from hr_breaker.models import (
    JobPosting,
    OptimizedResume,
    ResumeSource,
)
from hr_breaker.models.language import (
    Language,
    SUPPORTED_LANGUAGES,
    DEFAULT_LANGUAGE,
    get_language,
)
from hr_breaker.services.pdf_storage import PDFStorage, sanitize_filename


# ── Language model tests ──────────────────────────────────────────────────────


class TestLanguageModel:
    def test_language_dataclass(self):
        lang = Language(code="de", english_name="German", native_name="Deutsch")
        assert lang.code == "de"
        assert lang.english_name == "German"
        assert lang.native_name == "Deutsch"

    def test_language_is_frozen(self):
        lang = Language(code="en", english_name="English", native_name="English")
        with pytest.raises(AttributeError):
            lang.code = "fr"

    def test_supported_languages_not_empty(self):
        assert len(SUPPORTED_LANGUAGES) >= 2

    def test_english_is_first(self):
        assert SUPPORTED_LANGUAGES[0].code == "en"

    def test_russian_is_supported(self):
        codes = [lang.code for lang in SUPPORTED_LANGUAGES]
        assert "ru" in codes

    def test_default_language_is_english(self):
        assert DEFAULT_LANGUAGE.code == "en"
        assert DEFAULT_LANGUAGE.english_name == "English"

    def test_get_language_english(self):
        lang = get_language("en")
        assert lang.code == "en"
        assert lang.english_name == "English"

    def test_get_language_russian(self):
        lang = get_language("ru")
        assert lang.code == "ru"
        assert lang.english_name == "Russian"
        assert lang.native_name == "Русский"

    def test_get_language_unsupported_raises(self):
        with pytest.raises(ValueError, match="Unsupported language: zz"):
            get_language("zz")

    def test_all_languages_have_required_fields(self):
        for lang in SUPPORTED_LANGUAGES:
            assert lang.code, f"Missing code for {lang}"
            assert lang.english_name, f"Missing english_name for {lang}"
            assert lang.native_name, f"Missing native_name for {lang}"

    def test_language_codes_unique(self):
        codes = [lang.code for lang in SUPPORTED_LANGUAGES]
        assert len(codes) == len(set(codes)), "Duplicate language codes found"


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def job_posting():
    return JobPosting(
        title="Backend Engineer",
        company="Acme Corp",
        requirements=["Python", "Django"],
        keywords=["python", "django", "rest", "api"],
    )


@pytest.fixture
def source_resume():
    return ResumeSource(content="John Doe\nPython developer with 5 years experience")


# ── Orchestration optimize_for_job translation integration ────────────────────


class TestOptimizeForJobTranslation:
    @pytest.mark.asyncio
    async def test_language_passed_to_optimizer_for_russian(self, source_resume, job_posting):
        """optimize_for_job should pass Russian language to optimize_resume."""
        russian = get_language("ru")
        mock_optimized = OptimizedResume(
            html="<div>Русский</div>",
            source_checksum=source_resume.checksum,
            pdf_text="Русский", pdf_bytes=b"pdf",
        )

        with patch("hr_breaker.orchestration.optimize_resume", new_callable=AsyncMock) as mock_opt, \
             patch("hr_breaker.orchestration._render_and_extract") as mock_render, \
             patch("hr_breaker.orchestration.run_filters", new_callable=AsyncMock) as mock_filters:

            from hr_breaker.models import ValidationResult, FilterResult
            mock_opt.return_value = mock_optimized
            mock_render.return_value = mock_optimized
            mock_filters.return_value = ValidationResult(results=[
                FilterResult(filter_name="test", passed=True, score=1.0),
            ])

            from hr_breaker.orchestration import optimize_for_job
            await optimize_for_job(
                source_resume, job=job_posting, language=russian, max_iterations=1,
            )

            assert mock_opt.call_args.kwargs.get("language") == russian

    @pytest.mark.asyncio
    async def test_language_none_passed_to_optimizer(self, source_resume, job_posting):
        """optimize_for_job with language=None passes None to optimizer."""
        mock_optimized = OptimizedResume(
            html="<div>English</div>",
            source_checksum=source_resume.checksum,
            pdf_text="English", pdf_bytes=b"pdf",
        )

        with patch("hr_breaker.orchestration.optimize_resume", new_callable=AsyncMock) as mock_opt, \
             patch("hr_breaker.orchestration._render_and_extract") as mock_render, \
             patch("hr_breaker.orchestration.run_filters", new_callable=AsyncMock) as mock_filters:

            from hr_breaker.models import ValidationResult, FilterResult
            mock_opt.return_value = mock_optimized
            mock_render.return_value = mock_optimized
            mock_filters.return_value = ValidationResult(results=[
                FilterResult(filter_name="test", passed=True, score=1.0),
            ])

            from hr_breaker.orchestration import optimize_for_job
            await optimize_for_job(
                source_resume, job=job_posting, language=None, max_iterations=1,
            )

            assert mock_opt.call_args.kwargs.get("language") is None

    @pytest.mark.asyncio
    async def test_english_language_passed_as_is(self, source_resume, job_posting):
        """optimize_for_job with language=English passes it to optimizer (optimizer ignores it)."""
        english = get_language("en")
        mock_optimized = OptimizedResume(
            html="<div>English</div>",
            source_checksum=source_resume.checksum,
            pdf_text="English", pdf_bytes=b"pdf",
        )

        with patch("hr_breaker.orchestration.optimize_resume", new_callable=AsyncMock) as mock_opt, \
             patch("hr_breaker.orchestration._render_and_extract") as mock_render, \
             patch("hr_breaker.orchestration.run_filters", new_callable=AsyncMock) as mock_filters:

            from hr_breaker.models import ValidationResult, FilterResult
            mock_opt.return_value = mock_optimized
            mock_render.return_value = mock_optimized
            mock_filters.return_value = ValidationResult(results=[
                FilterResult(filter_name="test", passed=True, score=1.0),
            ])

            from hr_breaker.orchestration import optimize_for_job
            await optimize_for_job(
                source_resume, job=job_posting, language=english, max_iterations=1,
            )

            assert mock_opt.call_args.kwargs.get("language") == english


# ── PDF filename language postfix tests ───────────────────────────────────────


class TestPDFStorageLanguagePostfix:
    def test_generate_path_default_english(self, tmp_path):
        """Without lang_code, filename should end with _en.pdf."""
        with patch("hr_breaker.services.pdf_storage.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(output_dir=tmp_path)
            storage = PDFStorage()
            path = storage.generate_path("John", "Doe", "Acme", "Engineer")
            assert path.name == "john_doe_acme_engineer_en.pdf"

    def test_generate_path_russian(self, tmp_path):
        """With lang_code='ru', filename should end with _ru.pdf."""
        with patch("hr_breaker.services.pdf_storage.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(output_dir=tmp_path)
            storage = PDFStorage()
            path = storage.generate_path("John", "Doe", "Acme", "Engineer", lang_code="ru")
            assert path.name == "john_doe_acme_engineer_ru.pdf"

    def test_generate_path_explicit_english(self, tmp_path):
        """With lang_code='en', filename should end with _en.pdf."""
        with patch("hr_breaker.services.pdf_storage.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(output_dir=tmp_path)
            storage = PDFStorage()
            path = storage.generate_path("John", "Doe", "Acme", "Engineer", lang_code="en")
            assert path.name == "john_doe_acme_engineer_en.pdf"

    def test_generate_path_no_role(self, tmp_path):
        """Without role, lang code should still be appended."""
        with patch("hr_breaker.services.pdf_storage.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(output_dir=tmp_path)
            storage = PDFStorage()
            path = storage.generate_path("John", "Doe", "Acme", lang_code="ru")
            assert path.name == "john_doe_acme_ru.pdf"

    def test_different_lang_codes_produce_different_filenames(self, tmp_path):
        """Same resume in different languages should have different filenames."""
        with patch("hr_breaker.services.pdf_storage.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(output_dir=tmp_path)
            storage = PDFStorage()
            path_en = storage.generate_path("John", "Doe", "Acme", "Dev", lang_code="en")
            path_ru = storage.generate_path("John", "Doe", "Acme", "Dev", lang_code="ru")
            assert path_en != path_ru
            assert path_en.stem.endswith("_en")
            assert path_ru.stem.endswith("_ru")


# ── Config tests ──────────────────────────────────────────────────────────────


class TestTranslationConfig:
    def test_default_language_setting(self):
        from hr_breaker.config import Settings
        s = Settings()
        assert s.default_language == "from_job"
