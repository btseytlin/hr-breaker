"""Tests for translation functionality: language model, agents, orchestration, PDF naming."""

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
from hr_breaker.agents.translator import TranslationResult, SYSTEM_PROMPT as TRANSLATOR_PROMPT
from hr_breaker.agents.translation_reviewer import TranslationReview, SYSTEM_PROMPT as REVIEWER_PROMPT
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


# ── Translation result model tests ────────────────────────────────────────────


class TestTranslationModels:
    def test_translation_result(self):
        result = TranslationResult(
            html="<div>Тест</div>",
            changes=["Kept 'Python' in English"],
        )
        assert result.html == "<div>Тест</div>"
        assert len(result.changes) == 1

    def test_translation_result_defaults(self):
        result = TranslationResult(html="<div>Test</div>")
        assert result.changes == []

    def test_translation_review_passed(self):
        review = TranslationReview(
            passed=True,
            score=0.95,
            issues=[],
            suggestions=[],
            reasoning="Excellent translation",
        )
        assert review.passed
        assert review.score == 0.95

    def test_translation_review_failed(self):
        review = TranslationReview(
            passed=False,
            score=0.6,
            issues=["Wrong term for 'deployment'"],
            suggestions=["Use 'развёртывание' instead"],
            reasoning="Several terminology issues",
        )
        assert not review.passed
        assert len(review.issues) == 1
        assert len(review.suggestions) == 1

    def test_translation_review_score_bounds(self):
        with pytest.raises(Exception):
            TranslationReview(passed=True, score=1.5, reasoning="bad")
        with pytest.raises(Exception):
            TranslationReview(passed=True, score=-0.1, reasoning="bad")


# ── Translator prompt tests ───────────────────────────────────────────────────


class TestTranslatorPrompt:
    def test_prompt_has_language_placeholders(self):
        assert "{language_english}" in TRANSLATOR_PROMPT
        assert "{language_native}" in TRANSLATOR_PROMPT

    def test_prompt_formatting(self):
        formatted = TRANSLATOR_PROMPT.format(
            language_english="Russian",
            language_native="Русский",
        )
        assert "Russian" in formatted
        assert "Русский" in formatted
        assert "{language_english}" not in formatted

    def test_prompt_mentions_html_preservation(self):
        assert "HTML" in TRANSLATOR_PROMPT
        assert "CSS" in TRANSLATOR_PROMPT

    def test_prompt_mentions_technical_terms(self):
        assert "technical" in TRANSLATOR_PROMPT.lower() or "TECHNICAL" in TRANSLATOR_PROMPT


class TestReviewerPrompt:
    def test_prompt_has_language_placeholders(self):
        assert "{language_english}" in REVIEWER_PROMPT
        assert "{language_native}" in REVIEWER_PROMPT

    def test_prompt_formatting(self):
        formatted = REVIEWER_PROMPT.format(
            language_english="Russian",
            language_native="Русский",
        )
        assert "Russian" in formatted
        assert "{language_english}" not in formatted

    def test_prompt_mentions_scoring(self):
        assert "0.8" in REVIEWER_PROMPT  # pass threshold
        assert "TERMINOLOGY" in REVIEWER_PROMPT
        assert "NATURALNESS" in REVIEWER_PROMPT
        assert "COMPLETENESS" in REVIEWER_PROMPT


# ── Translator agent tests (mocked) ──────────────────────────────────────────


@pytest.fixture
def russian():
    return get_language("ru")


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


@pytest.fixture
def optimized_resume(source_resume):
    return OptimizedResume(
        html='<div class="header"><h1>John Doe</h1></div><div class="section">Python developer</div>',
        source_checksum=source_resume.checksum,
        pdf_text="John Doe\nPython developer",
        pdf_bytes=b"%PDF-fake",
    )


class TestTranslateResume:
    @pytest.mark.asyncio
    async def test_translate_resume_calls_agent(self, russian, job_posting):
        """translate_resume should call the agent and return TranslationResult."""
        mock_output = TranslationResult(
            html='<div class="header"><h1>John Doe</h1></div><div class="section">Разработчик Python</div>',
            changes=["Kept 'Python' in English", "Translated 'developer' to 'Разработчик'"],
        )
        mock_result = MagicMock()
        mock_result.output = mock_output

        with patch("hr_breaker.agents.translator.get_translator_agent") as mock_get:
            mock_agent = AsyncMock()
            mock_agent.run.return_value = mock_result
            mock_get.return_value = mock_agent

            from hr_breaker.agents.translator import translate_resume

            result = await translate_resume(
                html="<div>Python developer</div>",
                language=russian,
                job=job_posting,
            )

            assert isinstance(result, TranslationResult)
            assert "Разработчик" in result.html
            mock_agent.run.assert_called_once()

    @pytest.mark.asyncio
    async def test_translate_resume_with_feedback(self, russian, job_posting):
        """translate_resume should include feedback in prompt when provided."""
        mock_output = TranslationResult(html="<div>Fixed</div>", changes=[])
        mock_result = MagicMock()
        mock_result.output = mock_output

        with patch("hr_breaker.agents.translator.get_translator_agent") as mock_get:
            mock_agent = AsyncMock()
            mock_agent.run.return_value = mock_result
            mock_get.return_value = mock_agent

            from hr_breaker.agents.translator import translate_resume

            await translate_resume(
                html="<div>Test</div>",
                language=russian,
                job=job_posting,
                feedback="Wrong term for 'deployment'",
            )

            # Check that feedback was included in the prompt
            call_args = mock_agent.run.call_args[0][0]
            assert "Wrong term for 'deployment'" in call_args
            assert "Reviewer Feedback" in call_args


class TestReviewTranslation:
    @pytest.mark.asyncio
    async def test_review_translation_passes(self, russian, job_posting):
        """review_translation should return a TranslationReview."""
        mock_output = TranslationReview(
            passed=True,
            score=0.92,
            issues=[],
            suggestions=[],
            reasoning="Good translation",
        )
        mock_result = MagicMock()
        mock_result.output = mock_output

        with patch("hr_breaker.agents.translation_reviewer.get_translation_reviewer_agent") as mock_get:
            mock_agent = AsyncMock()
            mock_agent.run.return_value = mock_result
            mock_get.return_value = mock_agent

            from hr_breaker.agents.translation_reviewer import review_translation

            result = await review_translation(
                original_html="<div>Python developer</div>",
                translated_html="<div>Разработчик Python</div>",
                language=russian,
                job=job_posting,
            )

            assert result.passed
            assert result.score == 0.92
            mock_agent.run.assert_called_once()

    @pytest.mark.asyncio
    async def test_review_translation_fails(self, russian, job_posting):
        """review_translation should correctly report failures."""
        mock_output = TranslationReview(
            passed=False,
            score=0.55,
            issues=["Awkward phrasing in summary"],
            suggestions=["Rephrase summary section"],
            reasoning="Multiple issues",
        )
        mock_result = MagicMock()
        mock_result.output = mock_output

        with patch("hr_breaker.agents.translation_reviewer.get_translation_reviewer_agent") as mock_get:
            mock_agent = AsyncMock()
            mock_agent.run.return_value = mock_result
            mock_get.return_value = mock_agent

            from hr_breaker.agents.translation_reviewer import review_translation

            result = await review_translation(
                original_html="<div>Test</div>",
                translated_html="<div>Тест</div>",
                language=russian,
                job=job_posting,
            )

            assert not result.passed
            assert len(result.issues) == 1


# ── Orchestration translation flow tests ──────────────────────────────────────


class TestTranslateAndRerender:
    @pytest.mark.asyncio
    async def test_translate_and_rerender_happy_path(
        self, russian, job_posting, optimized_resume
    ):
        """Should translate, review (pass), and re-render."""
        mock_translation = TranslationResult(
            html="<div>Translated HTML</div>",
            changes=["Translated content"],
        )
        mock_review = TranslationReview(
            passed=True, score=0.9, issues=[], suggestions=[], reasoning="Good",
        )

        with patch("hr_breaker.orchestration.translate_resume", new_callable=AsyncMock) as mock_tr, \
             patch("hr_breaker.orchestration.review_translation", new_callable=AsyncMock) as mock_rv, \
             patch("hr_breaker.orchestration._render_and_extract") as mock_render:

            mock_tr.return_value = mock_translation
            mock_rv.return_value = mock_review
            mock_render.return_value = optimized_resume.model_copy(
                update={"html": "Translated HTML", "pdf_bytes": b"pdf", "pdf_text": "text"}
            )

            from hr_breaker.orchestration import translate_and_rerender

            result = await translate_and_rerender(
                optimized_resume, russian, job_posting, max_translation_iterations=2,
            )

            # Should have called translate once and review once (passed first try)
            mock_tr.assert_called_once()
            mock_rv.assert_called_once()
            mock_render.assert_called_once()

    @pytest.mark.asyncio
    async def test_translate_and_rerender_retry_on_failure(
        self, russian, job_posting, optimized_resume
    ):
        """Should retry translation when review fails."""
        mock_translation_1 = TranslationResult(html="<div>Bad</div>", changes=[])
        mock_translation_2 = TranslationResult(html="<div>Better</div>", changes=[])
        mock_review_fail = TranslationReview(
            passed=False, score=0.5, issues=["Wrong terms"],
            suggestions=["Fix terms"], reasoning="Poor",
        )
        mock_review_pass = TranslationReview(
            passed=True, score=0.9, issues=[], suggestions=[], reasoning="Good",
        )

        with patch("hr_breaker.orchestration.translate_resume", new_callable=AsyncMock) as mock_tr, \
             patch("hr_breaker.orchestration.review_translation", new_callable=AsyncMock) as mock_rv, \
             patch("hr_breaker.orchestration._render_and_extract") as mock_render:

            mock_tr.side_effect = [mock_translation_1, mock_translation_2]
            mock_rv.side_effect = [mock_review_fail, mock_review_pass]
            mock_render.return_value = optimized_resume.model_copy(
                update={"html": "Better", "pdf_bytes": b"pdf", "pdf_text": "text"}
            )

            from hr_breaker.orchestration import translate_and_rerender

            result = await translate_and_rerender(
                optimized_resume, russian, job_posting, max_translation_iterations=2,
            )

            assert mock_tr.call_count == 2
            assert mock_rv.call_count == 2
            # Second translate call should include feedback
            second_call_kwargs = mock_tr.call_args_list[1]
            assert second_call_kwargs.kwargs.get("feedback") is not None

    @pytest.mark.asyncio
    async def test_translate_and_rerender_status_callback(
        self, russian, job_posting, optimized_resume
    ):
        """Should call on_status callback during translation."""
        mock_translation = TranslationResult(html="<div>OK</div>", changes=[])
        mock_review = TranslationReview(
            passed=True, score=0.9, issues=[], suggestions=[], reasoning="OK",
        )
        status_messages = []

        with patch("hr_breaker.orchestration.translate_resume", new_callable=AsyncMock) as mock_tr, \
             patch("hr_breaker.orchestration.review_translation", new_callable=AsyncMock) as mock_rv, \
             patch("hr_breaker.orchestration._render_and_extract") as mock_render:

            mock_tr.return_value = mock_translation
            mock_rv.return_value = mock_review
            mock_render.return_value = optimized_resume

            from hr_breaker.orchestration import translate_and_rerender

            await translate_and_rerender(
                optimized_resume, russian, job_posting,
                max_translation_iterations=2,
                on_status=lambda msg: status_messages.append(msg),
            )

            assert len(status_messages) >= 2  # At least "Translating..." and "Translation complete"
            assert any("Russian" in msg for msg in status_messages)
            assert "Translation complete" in status_messages

    @pytest.mark.asyncio
    async def test_translate_and_rerender_uses_last_translation_on_exhaustion(
        self, russian, job_posting, optimized_resume
    ):
        """If max iterations reached without passing, should use last translation anyway."""
        mock_translation = TranslationResult(html="<div>NotPerfect</div>", changes=[])
        mock_review_fail = TranslationReview(
            passed=False, score=0.6, issues=["Still bad"],
            suggestions=["Try again"], reasoning="Not great",
        )

        with patch("hr_breaker.orchestration.translate_resume", new_callable=AsyncMock) as mock_tr, \
             patch("hr_breaker.orchestration.review_translation", new_callable=AsyncMock) as mock_rv, \
             patch("hr_breaker.orchestration._render_and_extract") as mock_render:

            mock_tr.return_value = mock_translation
            mock_rv.return_value = mock_review_fail
            mock_render.return_value = optimized_resume.model_copy(
                update={"html": "NotPerfect"}
            )

            from hr_breaker.orchestration import translate_and_rerender

            # Should not raise, should use last attempt
            result = await translate_and_rerender(
                optimized_resume, russian, job_posting, max_translation_iterations=2,
            )

            assert mock_tr.call_count == 2
            mock_render.assert_called_once()  # Still renders the last translation


# ── Orchestration optimize_for_job translation integration ────────────────────


class TestOptimizeForJobTranslation:
    @pytest.mark.asyncio
    async def test_no_translation_for_english(self, source_resume, job_posting):
        """optimize_for_job should skip translation when language is English."""
        english = get_language("en")
        mock_optimized = OptimizedResume(
            html="<div>English</div>",
            source_checksum=source_resume.checksum,
            pdf_text="English",
            pdf_bytes=b"pdf",
        )

        with patch("hr_breaker.orchestration.optimize_resume", new_callable=AsyncMock) as mock_opt, \
             patch("hr_breaker.orchestration._render_and_extract") as mock_render, \
             patch("hr_breaker.orchestration.run_filters", new_callable=AsyncMock) as mock_filters, \
             patch("hr_breaker.orchestration.translate_and_rerender", new_callable=AsyncMock) as mock_translate:

            from hr_breaker.models import ValidationResult, FilterResult
            mock_opt.return_value = mock_optimized
            mock_render.return_value = mock_optimized
            mock_filters.return_value = ValidationResult(results=[
                FilterResult(filter_name="test", passed=True, score=1.0),
            ])

            from hr_breaker.orchestration import optimize_for_job

            result = await optimize_for_job(
                source_resume, job=job_posting, language=english, max_iterations=1,
            )

            mock_translate.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_translation_when_language_is_none(self, source_resume, job_posting):
        """optimize_for_job should skip translation when language is None."""
        mock_optimized = OptimizedResume(
            html="<div>English</div>",
            source_checksum=source_resume.checksum,
            pdf_text="English",
            pdf_bytes=b"pdf",
        )

        with patch("hr_breaker.orchestration.optimize_resume", new_callable=AsyncMock) as mock_opt, \
             patch("hr_breaker.orchestration._render_and_extract") as mock_render, \
             patch("hr_breaker.orchestration.run_filters", new_callable=AsyncMock) as mock_filters, \
             patch("hr_breaker.orchestration.translate_and_rerender", new_callable=AsyncMock) as mock_translate:

            from hr_breaker.models import ValidationResult, FilterResult
            mock_opt.return_value = mock_optimized
            mock_render.return_value = mock_optimized
            mock_filters.return_value = ValidationResult(results=[
                FilterResult(filter_name="test", passed=True, score=1.0),
            ])

            from hr_breaker.orchestration import optimize_for_job

            result = await optimize_for_job(
                source_resume, job=job_posting, language=None, max_iterations=1,
            )

            mock_translate.assert_not_called()

    @pytest.mark.asyncio
    async def test_translation_called_for_russian(self, source_resume, job_posting):
        """optimize_for_job should call translation for non-English language."""
        russian = get_language("ru")
        mock_optimized = OptimizedResume(
            html="<div>English</div>",
            source_checksum=source_resume.checksum,
            pdf_text="English",
            pdf_bytes=b"pdf",
        )
        mock_translated = mock_optimized.model_copy(
            update={"html": "<div>Русский</div>"}
        )

        with patch("hr_breaker.orchestration.optimize_resume", new_callable=AsyncMock) as mock_opt, \
             patch("hr_breaker.orchestration._render_and_extract") as mock_render, \
             patch("hr_breaker.orchestration.run_filters", new_callable=AsyncMock) as mock_filters, \
             patch("hr_breaker.orchestration.translate_and_rerender", new_callable=AsyncMock) as mock_translate:

            from hr_breaker.models import ValidationResult, FilterResult
            mock_opt.return_value = mock_optimized
            mock_render.return_value = mock_optimized
            mock_filters.return_value = ValidationResult(results=[
                FilterResult(filter_name="test", passed=True, score=1.0),
            ])
            mock_translate.return_value = mock_translated

            from hr_breaker.orchestration import optimize_for_job

            optimized, validation, job = await optimize_for_job(
                source_resume, job=job_posting, language=russian, max_iterations=1,
            )

            mock_translate.assert_called_once()
            assert optimized.html == "<div>Русский</div>"


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
        assert s.default_language == "en"

    def test_translation_max_iterations_setting(self):
        from hr_breaker.config import Settings
        s = Settings()
        assert s.translation_max_iterations == 2
