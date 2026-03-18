"""Tests for TranslationQualityChecker filter and agent."""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from hr_breaker.models import JobPosting, OptimizedResume, ResumeSource, FilterResult
from hr_breaker.models.language import get_language


@pytest.fixture
def job():
    return JobPosting(
        title="Backend Engineer", company="Acme",
        requirements=["Python"], keywords=["python", "django"],
    )


@pytest.fixture
def source():
    return ResumeSource(content="John Doe\nPython dev with 5 years experience")


@pytest.fixture
def optimized(source):
    return OptimizedResume(
        html="<div>Тест</div>",
        source_checksum=source.checksum,
        pdf_text="Джон Доу\nPython разработчик с 5-летним опытом",
    )


class TestTranslationQualityCheckerFilter:

    @pytest.mark.asyncio
    async def test_skipped_when_no_language(self, job, source, optimized):
        """Filter is skipped when language is None."""
        from hr_breaker.filters.translation_checker import TranslationQualityChecker
        checker = TranslationQualityChecker()
        result = await checker.evaluate(optimized, job, source, language=None)
        assert result.skipped is True
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_skipped_when_english(self, job, source, optimized):
        """Filter is skipped when language is English."""
        from hr_breaker.filters.translation_checker import TranslationQualityChecker
        english = get_language("en")
        checker = TranslationQualityChecker()
        result = await checker.evaluate(optimized, job, source, language=english)
        assert result.skipped is True
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_calls_agent_for_russian(self, job, source, optimized):
        """Filter calls check_translation_quality for non-English."""
        from hr_breaker.filters.translation_checker import TranslationQualityChecker
        russian = get_language("ru")
        mock_result = FilterResult(
            filter_name="TranslationQualityChecker",
            passed=False, score=1.0, issues=[], suggestions=[],
        )
        with patch("hr_breaker.filters.translation_checker.check_translation_quality",
                    new_callable=AsyncMock, return_value=mock_result):
            checker = TranslationQualityChecker()
            result = await checker.evaluate(optimized, job, source, language=russian)
            assert result.score == 1.0
            assert result.passed is True  # 1.0 >= 1.0 threshold

    @pytest.mark.asyncio
    async def test_fails_below_threshold(self, job, source, optimized):
        """Filter fails when score is below threshold."""
        from hr_breaker.filters.translation_checker import TranslationQualityChecker
        russian = get_language("ru")
        mock_result = FilterResult(
            filter_name="TranslationQualityChecker",
            passed=False, score=0.5, issues=["Bad translation"], suggestions=["Fix it"],
        )
        with patch("hr_breaker.filters.translation_checker.check_translation_quality",
                    new_callable=AsyncMock, return_value=mock_result):
            checker = TranslationQualityChecker()
            result = await checker.evaluate(optimized, job, source, language=russian)
            assert result.passed is False
            assert result.score == 0.5
            assert "Bad translation" in result.issues

    def test_priority_is_8(self):
        from hr_breaker.filters.translation_checker import TranslationQualityChecker
        assert TranslationQualityChecker.priority == 8

    def test_registered_in_registry(self):
        from hr_breaker.filters.registry import FilterRegistry
        names = [f.name for f in FilterRegistry.all()]
        assert "TranslationQualityChecker" in names


class TestTranslationCheckerAgent:

    @pytest.mark.asyncio
    async def test_agent_prompt_includes_language(self, job, source, optimized):
        """Agent prompt mentions the target language."""
        russian = get_language("ru")
        with patch("hr_breaker.agents.translation_checker.get_translation_checker_agent") as mock_get:
            mock_agent = AsyncMock()
            mock_result = MagicMock()
            mock_result.output = MagicMock(score=0.9, issues=[], suggestions=[])
            mock_agent.run.return_value = mock_result
            mock_get.return_value = mock_agent

            from hr_breaker.agents.translation_checker import check_translation_quality
            await check_translation_quality(optimized, source, job, russian)

            prompt = mock_agent.run.call_args[0][0]
            assert "Russian" in prompt

    @pytest.mark.asyncio
    async def test_agent_includes_original_resume(self, job, source, optimized):
        """Agent prompt includes the original English resume for reference."""
        russian = get_language("ru")
        with patch("hr_breaker.agents.translation_checker.get_translation_checker_agent") as mock_get:
            mock_agent = AsyncMock()
            mock_result = MagicMock()
            mock_result.output = MagicMock(score=0.9, issues=[], suggestions=[])
            mock_agent.run.return_value = mock_result
            mock_get.return_value = mock_agent

            from hr_breaker.agents.translation_checker import check_translation_quality
            await check_translation_quality(optimized, source, job, russian)

            prompt = mock_agent.run.call_args[0][0]
            assert "Python dev with 5 years" in prompt

    def test_agent_construction(self):
        """Agent constructs without errors."""
        russian = get_language("ru")
        from hr_breaker.agents.translation_checker import get_translation_checker_agent
        agent = get_translation_checker_agent(russian)
        assert agent is not None


class TestTranslationThresholdConfig:

    def test_default_threshold(self):
        from hr_breaker.config import Settings
        s = Settings()
        assert s.filter_translation_threshold == 0.95
