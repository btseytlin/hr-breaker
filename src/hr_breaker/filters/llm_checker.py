from hr_breaker.agents.combined_reviewer import combined_review, compute_ats_score
from hr_breaker.config import get_settings, logger
from hr_breaker.filters.base import BaseFilter
from hr_breaker.filters.registry import FilterRegistry
from hr_breaker.models import FilterResult, JobPosting, OptimizedResume, ResumeSource
from hr_breaker.models.language import Language


@FilterRegistry.register
class LLMChecker(BaseFilter):
    """Combined vision + ATS check in single LLM call."""

    name = "LLMChecker"
    priority = 5

    @property
    def threshold(self) -> float:
        return get_settings().filter_llm_threshold

    async def evaluate(
        self,
        optimized: OptimizedResume,
        job: JobPosting,
        source: ResumeSource,
        language: Language | None = None,
        source_language: Language | None = None,
    ) -> FilterResult:
        result, _, page_count, render_warnings = await combined_review(optimized, job, language=language)

        logger.debug(
            f"LLMChecker: professional={result.looks_professional}, "
            f"ats_scores=(kw={result.keyword_score:.2f}, exp={result.experience_score:.2f}, "
            f"edu={result.education_score:.2f}, fit={result.overall_fit_score:.2f}), "
            f"disqualified={result.disqualified}"
        )

        # Combine issues from both checks
        issues = list(result.visual_issues) + list(result.ats_issues)
        suggestions = []
        feedback = result.visual_feedback

        # Add render warnings
        if render_warnings:
            issues.extend([f"Render warning: {w}" for w in render_warnings])

        # Compute ATS score
        ats_score = compute_ats_score(result)

        # Pass requires: professional look + ATS score >= threshold + not disqualified
        passed = (
            result.looks_professional
            and ats_score >= self.threshold
            and not result.disqualified
        )

        if not result.looks_professional:
            suggestions.append("Fix visual/formatting issues")
        if ats_score < self.threshold:
            suggestions.append(
                f"Improve ATS match (score: {ats_score:.2f}, need: {self.threshold})"
            )
        if result.disqualified:
            suggestions.append("Address disqualifying issues")

        return FilterResult(
            filter_name=self.name,
            passed=passed,
            score=ats_score if result.looks_professional else 0.0,
            threshold=self.threshold,
            issues=issues,
            suggestions=suggestions,
            feedback=feedback,
        )
