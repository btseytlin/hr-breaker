"""Tests for optimizer language integration."""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from hr_breaker.models import JobPosting, ResumeSource, IterationContext, OptimizedResume, ValidationResult, FilterResult
from hr_breaker.models.language import get_language, get_language_safe, resolve_target_language, LANGUAGE_MODES


class TestLanguageHelpers:

    def test_get_language_safe_known(self):
        assert get_language_safe("en").code == "en"
        assert get_language_safe("ru").code == "ru"

    def test_get_language_safe_unknown(self):
        assert get_language_safe("xx").code == "en"
        assert get_language_safe(None).code == "en"
        assert get_language_safe("").code == "en"

    def test_resolve_from_job(self):
        lang = resolve_target_language("from_job", "ru", "en")
        assert lang.code == "ru"

    def test_resolve_from_resume(self):
        lang = resolve_target_language("from_resume", "ru", "en")
        assert lang.code == "en"

    def test_resolve_fixed_code(self):
        lang = resolve_target_language("ru", "en", "en")
        assert lang.code == "ru"

    def test_resolve_from_job_unknown(self):
        lang = resolve_target_language("from_job", "xx", "en")
        assert lang.code == "en"

    def test_language_modes_structure(self):
        assert len(LANGUAGE_MODES) >= 4
        values = [m["value"] for m in LANGUAGE_MODES]
        assert "from_job" in values
        assert "from_resume" in values
        assert "en" in values
        assert "ru" in values


class TestOptimizerLanguagePrompt:

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
    def context(self, source):
        return IterationContext(iteration=0, original_resume=source.content)

    @pytest.mark.asyncio
    async def test_english_language_instructions_when_none(self, job, source, context):
        """English TARGET LANGUAGE block when language is None."""
        with patch("hr_breaker.agents.optimizer.get_optimizer_agent") as mock_get:
            mock_agent = AsyncMock()
            mock_result = MagicMock()
            mock_result.output = MagicMock(html="<div>Test</div>", changes=[])
            mock_agent.run.return_value = mock_result
            mock_get.return_value = mock_agent

            from hr_breaker.agents.optimizer import optimize_resume
            await optimize_resume(source, job, context, language=None)

            prompt = mock_agent.run.call_args[0][0]
            assert "TARGET LANGUAGE: English" in prompt

    @pytest.mark.asyncio
    async def test_english_language_instructions_when_english(self, job, source, context):
        """English TARGET LANGUAGE block when language is English."""
        english = get_language("en")
        with patch("hr_breaker.agents.optimizer.get_optimizer_agent") as mock_get:
            mock_agent = AsyncMock()
            mock_result = MagicMock()
            mock_result.output = MagicMock(html="<div>Test</div>", changes=[])
            mock_agent.run.return_value = mock_result
            mock_get.return_value = mock_agent

            from hr_breaker.agents.optimizer import optimize_resume
            await optimize_resume(source, job, context, language=english)

            prompt = mock_agent.run.call_args[0][0]
            assert "TARGET LANGUAGE: English" in prompt

    @pytest.mark.asyncio
    async def test_russian_adds_language_instructions(self, job, source, context):
        """TARGET LANGUAGE block present for Russian."""
        russian = get_language("ru")
        with patch("hr_breaker.agents.optimizer.get_optimizer_agent") as mock_get:
            mock_agent = AsyncMock()
            mock_result = MagicMock()
            mock_result.output = MagicMock(html="<div>Тест</div>", changes=[])
            mock_agent.run.return_value = mock_result
            mock_get.return_value = mock_agent

            from hr_breaker.agents.optimizer import optimize_resume
            await optimize_resume(source, job, context, language=russian)

            prompt = mock_agent.run.call_args[0][0]
            assert "TARGET LANGUAGE" in prompt
            assert "Russian" in prompt
            assert "Русский" in prompt


class TestOrchestrationPassesLanguage:

    @pytest.mark.asyncio
    async def test_language_passed_to_optimizer(self):
        russian = get_language("ru")
        source = ResumeSource(content="John Doe\nPython dev")
        job = JobPosting(
            title="Backend Engineer", company="Acme",
            requirements=["Python"], keywords=["python"],
        )
        mock_optimized = MagicMock()
        mock_optimized.html = "<div>Тест</div>"
        mock_optimized.data = None

        with patch("hr_breaker.orchestration.optimize_resume", new_callable=AsyncMock) as mock_opt, \
             patch("hr_breaker.orchestration._render_and_extract") as mock_render, \
             patch("hr_breaker.orchestration.run_filters", new_callable=AsyncMock) as mock_filters:

            mock_opt.return_value = mock_optimized
            mock_render.return_value = mock_optimized
            mock_filters.return_value = ValidationResult(results=[
                FilterResult(filter_name="test", passed=True, score=1.0),
            ])

            from hr_breaker.orchestration import optimize_for_job
            await optimize_for_job(source, job=job, language=russian, max_iterations=1)

            assert mock_opt.call_args.kwargs.get("language") == russian

    @pytest.mark.asyncio
    async def test_source_language_passed_to_filters(self):
        russian = get_language("ru")
        english = get_language("en")
        source = ResumeSource(content="John Doe\nPython dev")
        job = JobPosting(
            title="Backend Engineer", company="Acme",
            requirements=["Python"], keywords=["python"],
        )
        mock_optimized = MagicMock()
        mock_optimized.html = "<div>Тест</div>"
        mock_optimized.data = None

        with patch("hr_breaker.orchestration.optimize_resume", new_callable=AsyncMock) as mock_opt, \
             patch("hr_breaker.orchestration._render_and_extract") as mock_render, \
             patch("hr_breaker.orchestration.run_filters", new_callable=AsyncMock) as mock_filters:

            mock_opt.return_value = mock_optimized
            mock_render.return_value = mock_optimized
            mock_filters.return_value = ValidationResult(results=[
                FilterResult(filter_name="test", passed=True, score=1.0),
            ])

            from hr_breaker.orchestration import optimize_for_job
            await optimize_for_job(source, job=job, language=russian, source_language=english, max_iterations=1)

            assert mock_filters.call_args.kwargs.get("source_language") == english
