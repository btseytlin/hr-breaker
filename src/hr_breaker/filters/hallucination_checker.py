from hr_breaker.agents.hallucination_detector import detect_hallucinations
from hr_breaker.config import get_settings
from hr_breaker.filters.base import BaseFilter
from hr_breaker.filters.registry import FilterRegistry
from hr_breaker.models import FilterResult, JobPosting, OptimizedResume, ResumeSource


@FilterRegistry.register
class HallucinationChecker(BaseFilter):
    """LLM-based hallucination detection filter. Runs last after all others pass."""

    name = "HallucinationChecker"
    priority = 3

    @property
    def threshold(self) -> float:
        return get_settings().filter_hallucination_threshold

    async def evaluate(
        self,
        optimized: OptimizedResume,
        job: JobPosting,
        source: ResumeSource,
    ) -> FilterResult:
        result = await detect_hallucinations(optimized, source, no_shame=self.no_shame)
        if not self.no_shame:
            result.threshold = self.threshold
        return result
