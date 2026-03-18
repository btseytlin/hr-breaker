from hr_breaker.agents.ai_generated_detector import detect_ai_generated
from hr_breaker.config import get_settings
from hr_breaker.filters.base import BaseFilter
from hr_breaker.filters.registry import FilterRegistry
from hr_breaker.models import FilterResult, JobPosting, OptimizedResume, ResumeSource
from hr_breaker.models.language import Language


@FilterRegistry.register
class AIGeneratedChecker(BaseFilter):
    """Detect AI-generated text in resumes. Runs last after all others."""

    name = "AIGeneratedChecker"
    priority = 7

    @property
    def threshold(self) -> float:
        return get_settings().filter_ai_generated_threshold

    async def evaluate(
        self,
        optimized: OptimizedResume,
        job: JobPosting,
        source: ResumeSource,
        language: Language | None = None,
        source_language: Language | None = None,
    ) -> FilterResult:
        result = await detect_ai_generated(optimized, language=language)
        # Halve threshold in no-shame mode
        result.threshold = self.threshold / 2 if self.no_shame else self.threshold
        result.passed = result.score >= result.threshold
        return result
