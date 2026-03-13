import re
from dataclasses import dataclass

from sklearn.feature_extraction.text import TfidfVectorizer

from hr_breaker.config import get_settings
from hr_breaker.filters.base import BaseFilter
from hr_breaker.filters.registry import FilterRegistry
from hr_breaker.models import FilterResult, JobPosting, OptimizedResume, ResumeSource
from hr_breaker.models.language import Language


@dataclass
class KeywordCheckResult:
    """Result of keyword matching check."""

    score: float
    passed: bool
    missing_keywords: list[str]


def check_keywords(
    resume_text: str, job: JobPosting, threshold: float | None = None
) -> KeywordCheckResult:
    """Check keyword coverage of resume text vs job posting.

    Args:
        resume_text: Plain text from resume (already lowercased)
        job: Job posting with requirements and keywords
        threshold: Minimum score to pass (default from settings)

    Returns:
        KeywordCheckResult with score, passed status, and missing keywords ranked by TF-IDF importance
    """
    settings = get_settings()
    if threshold is None:
        threshold = settings.filter_keyword_threshold

    resume_lower = resume_text.lower()
    job_text = f"{job.title} {job.description or ''} {' '.join(job.requirements)}"

    vectorizer = TfidfVectorizer(
        stop_words="english",
        ngram_range=(1, 2),
        max_features=settings.keyword_tfidf_max_features,
        token_pattern=r"(?u)\b[a-zA-Z][a-zA-Z0-9+#.-]*\b",
    )

    try:
        vectorizer.fit([job_text.lower()])
    except ValueError:
        return KeywordCheckResult(score=1.0, passed=True, missing_keywords=[])

    feature_names = vectorizer.get_feature_names_out()
    tfidf_matrix = vectorizer.transform([job_text.lower()])
    tfidf_scores = dict(zip(feature_names, tfidf_matrix.toarray()[0]))

    significant_keywords = {
        term for term, score in tfidf_scores.items()
        if score > settings.keyword_tfidf_cutoff
    }
    for kw in job.keywords:
        significant_keywords.add(kw.lower())

    if not significant_keywords:
        return KeywordCheckResult(score=1.0, passed=True, missing_keywords=[])

    matched, missing = [], []
    for keyword in significant_keywords:
        pattern = rf"\b{re.escape(keyword)}\b"
        if re.search(pattern, resume_lower):
            matched.append(keyword)
        else:
            missing.append(keyword)

    matched_weight = sum(tfidf_scores.get(kw, 0.1) for kw in matched)
    total_weight = sum(tfidf_scores.get(kw, 0.1) for kw in significant_keywords)
    score = matched_weight / total_weight if total_weight > 0 else 1.0

    missing_sorted = sorted(
        missing, key=lambda kw: tfidf_scores.get(kw, 0), reverse=True
    )

    return KeywordCheckResult(
        score=float(score),
        passed=bool(score >= threshold),
        missing_keywords=missing_sorted[:settings.keyword_max_missing_display],
    )


@FilterRegistry.register
class KeywordMatcher(BaseFilter):
    """Keyword matching filter using TF-IDF weighted scoring."""

    name = "KeywordMatcher"
    priority = 4

    @property
    def threshold(self) -> float:
        return get_settings().filter_keyword_threshold

    async def evaluate(
        self,
        optimized: OptimizedResume,
        job: JobPosting,
        source: ResumeSource,
        language: Language | None = None,
        source_language: Language | None = None,
    ) -> FilterResult:
        if optimized.pdf_text is None:
            return FilterResult(
                filter_name=self.name,
                passed=False,
                score=0.0,
                threshold=self.threshold,
                issues=["No PDF text available"],
                suggestions=["Ensure PDF compilation succeeds"],
            )

        result = check_keywords(optimized.pdf_text, job, self.threshold)

        issues = []
        suggestions = []
        if result.missing_keywords and not result.passed:
            issues.append(
                f"Missing important keywords: {', '.join(result.missing_keywords)}"
            )
            suggestions.append(
                "Add missing keywords if they match your actual experience"
            )

        return FilterResult(
            filter_name=self.name,
            passed=result.passed,
            score=result.score,
            threshold=self.threshold,
            issues=issues,
            suggestions=suggestions,
        )
