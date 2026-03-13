"""Tests for language parameter in filter chain."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from hr_breaker.models import JobPosting, OptimizedResume, ResumeSource, FilterResult, ValidationResult
from hr_breaker.models.language import get_language


class TestBaseFilterLanguageParam:
    """All filters accept language parameter."""

    @pytest.fixture
    def job(self):
        return JobPosting(
            title="Backend Engineer", company="Acme",
            requirements=["Python"], keywords=["python"],
        )

    @pytest.fixture
    def source(self):
        return ResumeSource(content="John Doe\nPython dev")

    @pytest.fixture
    def optimized(self, source):
        return OptimizedResume(
            html="<div>Test</div>", source_checksum=source.checksum,
            pdf_text="Test", pdf_bytes=b"pdf",
        )

    def test_content_length_checker_accepts_language(self, optimized, job, source):
        """ContentLengthChecker.evaluate accepts language param."""
        from hr_breaker.filters import ContentLengthChecker
        f = ContentLengthChecker()
        import asyncio
        asyncio.run(f.evaluate(optimized, job, source, language=get_language("ru")))

    def test_data_validator_accepts_language(self, optimized, job, source):
        """DataValidator.evaluate accepts language param."""
        from hr_breaker.filters import DataValidator
        f = DataValidator()
        import asyncio
        asyncio.run(f.evaluate(optimized, job, source, language=get_language("ru")))


class TestRunFiltersLanguage:
    """run_filters passes language to each filter."""

    @pytest.mark.asyncio
    async def test_language_passed_to_filters(self):
        """run_filters should pass language to filter.evaluate."""
        russian = get_language("ru")
        source = ResumeSource(content="John Doe\nPython dev")
        optimized = OptimizedResume(
            html="<div>Test</div>", source_checksum=source.checksum,
            pdf_text="Test", pdf_bytes=b"pdf",
        )
        job = JobPosting(
            title="Dev", company="Co",
            requirements=["Python"], keywords=["python"],
        )

        captured_language = []

        class MockFilter:
            name = "MockFilter"
            priority = 1
            threshold = 0.5
            def __init__(self, no_shame=False):
                pass
            async def evaluate(self, optimized, job, source, language=None, source_language=None):
                captured_language.append(language)
                return FilterResult(
                    filter_name="MockFilter", passed=True, score=1.0,
                )

        with patch("hr_breaker.orchestration.FilterRegistry") as mock_registry:
            mock_registry.all.return_value = [MockFilter]

            from hr_breaker.orchestration import run_filters
            await run_filters(optimized, job, source, language=russian)

            assert captured_language == [russian]


class TestCombinedReviewerLanguage:
    """combined_review should include language context in prompt."""

    @pytest.mark.asyncio
    async def test_russian_language_in_review_prompt(self):
        """When language is Russian, combined_review prompt mentions it."""
        russian = get_language("ru")
        optimized = OptimizedResume(
            html="<div>Тест</div>", source_checksum="abc",
            pdf_text="Разработчик Python", pdf_bytes=b"%PDF-fake",
        )
        job = JobPosting(
            title="Dev", company="Co",
            requirements=["Python"], keywords=["python"],
        )

        with patch("hr_breaker.agents.combined_reviewer.get_combined_reviewer_agent") as mock_get, \
             patch("hr_breaker.agents.combined_reviewer.get_renderer") as mock_renderer, \
             patch("hr_breaker.agents.combined_reviewer.pdf_to_image") as mock_img:

            mock_render_result = MagicMock()
            mock_render_result.pdf_bytes = b"pdf"
            mock_render_result.warnings = []
            mock_render_result.page_count = 1
            mock_renderer.return_value.render.return_value = mock_render_result
            mock_img.return_value = (b"png", 1)

            mock_agent = AsyncMock()
            mock_result = MagicMock()
            mock_result.output = MagicMock(
                looks_professional=True, visual_issues=[], visual_feedback="",
                keyword_score=0.8, experience_score=0.8, education_score=0.8,
                overall_fit_score=0.8, disqualified=False, ats_issues=[],
            )
            mock_agent.run.return_value = mock_result
            mock_get.return_value = mock_agent

            from hr_breaker.agents.combined_reviewer import combined_review
            await combined_review(optimized, job, language=russian)

            # run() receives a list [prompt_str, BinaryContent]; extract the string
            call_arg = mock_agent.run.call_args[0][0]
            prompt = call_arg[0] if isinstance(call_arg, list) else call_arg
            assert "Russian" in prompt

    @pytest.mark.asyncio
    async def test_no_language_note_when_english(self):
        """No LANGUAGE NOTE when language is None."""
        optimized = OptimizedResume(
            html="<div>Test</div>", source_checksum="abc",
            pdf_text="Test", pdf_bytes=b"%PDF-fake",
        )
        job = JobPosting(
            title="Dev", company="Co",
            requirements=["Python"], keywords=["python"],
        )

        with patch("hr_breaker.agents.combined_reviewer.get_combined_reviewer_agent") as mock_get, \
             patch("hr_breaker.agents.combined_reviewer.get_renderer") as mock_renderer, \
             patch("hr_breaker.agents.combined_reviewer.pdf_to_image") as mock_img:

            mock_render_result = MagicMock()
            mock_render_result.pdf_bytes = b"pdf"
            mock_render_result.warnings = []
            mock_render_result.page_count = 1
            mock_renderer.return_value.render.return_value = mock_render_result
            mock_img.return_value = (b"png", 1)

            mock_agent = AsyncMock()
            mock_result = MagicMock()
            mock_result.output = MagicMock(
                looks_professional=True, visual_issues=[], visual_feedback="",
                keyword_score=0.8, experience_score=0.8, education_score=0.8,
                overall_fit_score=0.8, disqualified=False, ats_issues=[],
            )
            mock_agent.run.return_value = mock_result
            mock_get.return_value = mock_agent

            from hr_breaker.agents.combined_reviewer import combined_review
            await combined_review(optimized, job, language=None)

            call_arg = mock_agent.run.call_args[0][0]
            prompt = call_arg[0] if isinstance(call_arg, list) else call_arg
            assert "LANGUAGE NOTE" not in prompt


class TestHallucinationDetectorLanguage:

    @pytest.mark.asyncio
    async def test_russian_context_in_prompt(self):
        """detect_hallucinations should mention language when non-English."""
        russian = get_language("ru")
        source = ResumeSource(content="John Doe\nPython dev")
        optimized = OptimizedResume(
            html="<div>Разработчик</div>", source_checksum=source.checksum,
        )

        with patch("hr_breaker.agents.hallucination_detector.get_hallucination_agent") as mock_get:
            mock_agent = AsyncMock()
            mock_result = MagicMock()
            mock_result.output = MagicMock(
                no_hallucination_score=0.95, concerns=[], reasoning="Good",
            )
            mock_agent.run.return_value = mock_result
            mock_get.return_value = mock_agent

            from hr_breaker.agents.hallucination_detector import detect_hallucinations
            await detect_hallucinations(optimized, source, language=russian)

            prompt = mock_agent.run.call_args[0][0]
            assert "Russian" in prompt
            assert "LANGUAGE NOTE" in prompt

    @pytest.mark.asyncio
    async def test_no_language_note_when_none(self):
        """No LANGUAGE NOTE when language is None."""
        source = ResumeSource(content="John Doe\nPython dev")
        optimized = OptimizedResume(
            html="<div>Test</div>", source_checksum=source.checksum,
        )

        with patch("hr_breaker.agents.hallucination_detector.get_hallucination_agent") as mock_get:
            mock_agent = AsyncMock()
            mock_result = MagicMock()
            mock_result.output = MagicMock(
                no_hallucination_score=0.95, concerns=[], reasoning="Good",
            )
            mock_agent.run.return_value = mock_result
            mock_get.return_value = mock_agent

            from hr_breaker.agents.hallucination_detector import detect_hallucinations
            await detect_hallucinations(optimized, source, language=None)

            prompt = mock_agent.run.call_args[0][0]
            assert "LANGUAGE NOTE" not in prompt


class TestAIGeneratedDetectorLanguage:

    @pytest.mark.asyncio
    async def test_russian_context_in_prompt(self):
        """detect_ai_generated should mention language when non-English."""
        russian = get_language("ru")
        optimized = OptimizedResume(
            html="<div>Разработчик</div>",
            source_checksum="abc",
            pdf_text="Разработчик Python",
        )

        with patch("hr_breaker.agents.ai_generated_detector.get_ai_generated_agent") as mock_get:
            mock_agent = AsyncMock()
            mock_result = MagicMock()
            mock_result.output = MagicMock(
                is_ai_generated=False, ai_probability=0.1, indicators=[],
            )
            mock_agent.run.return_value = mock_result
            mock_get.return_value = mock_agent

            from hr_breaker.agents.ai_generated_detector import detect_ai_generated
            await detect_ai_generated(optimized, language=russian)

            prompt = mock_agent.run.call_args[0][0]
            assert "Russian" in prompt
            assert "LANGUAGE NOTE" in prompt

    @pytest.mark.asyncio
    async def test_no_language_note_when_none(self):
        """No LANGUAGE NOTE when language is None."""
        optimized = OptimizedResume(
            html="<div>Test</div>",
            source_checksum="abc",
            pdf_text="Test",
        )

        with patch("hr_breaker.agents.ai_generated_detector.get_ai_generated_agent") as mock_get:
            mock_agent = AsyncMock()
            mock_result = MagicMock()
            mock_result.output = MagicMock(
                is_ai_generated=False, ai_probability=0.1, indicators=[],
            )
            mock_agent.run.return_value = mock_result
            mock_get.return_value = mock_agent

            from hr_breaker.agents.ai_generated_detector import detect_ai_generated
            await detect_ai_generated(optimized, language=None)

            prompt = mock_agent.run.call_args[0][0]
            assert "LANGUAGE NOTE" not in prompt
