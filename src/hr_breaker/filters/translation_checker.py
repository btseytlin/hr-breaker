from hr_breaker.agents.translation_checker import check_translation_quality
from hr_breaker.config import get_settings
from hr_breaker.filters.base import BaseFilter
from hr_breaker.filters.registry import FilterRegistry
from hr_breaker.models import FilterResult, JobPosting, OptimizedResume, ResumeSource
from hr_breaker.models.language import Language


@FilterRegistry.register
class TranslationQualityChecker(BaseFilter):
    """Evaluate translation quality for non-English resumes. Skipped for English."""

    name = "TranslationQualityChecker"
    priority = 8  # Run last — no point checking translation if content fails

    @property
    def threshold(self) -> float:
        return get_settings().filter_translation_threshold

    async def evaluate(
        self,
        optimized: OptimizedResume,
        job: JobPosting,
        source: ResumeSource,
        language: Language | None = None,
        source_language: Language | None = None,
    ) -> FilterResult:
        # Skip when target language matches source language (no translation needed)
        effective_source = source_language.code if source_language else "en"
        effective_target = language.code if language else "en"
        if effective_source == effective_target:
            return FilterResult(
                filter_name=self.name,
                passed=True,
                score=1.0,
                threshold=self.threshold,
                skipped=True,
            )

        result = await check_translation_quality(optimized, source, job, language)
        result.threshold = self.threshold
        result.passed = result.score >= self.threshold
        return result
