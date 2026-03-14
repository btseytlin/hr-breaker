"""Profile retrieval — rank documents against a job posting and synthesize resume source.

Public API:
    rank_profile_documents(documents, job) -> list[RankedProfileDocument]
    synthesize_profile_resume_source(profile, selected_documents, ranked_documents) -> ResumeSource

Implementation is split across the retrieval/ subpackage:
    retrieval/lexical.py   — TF-IDF scoring
    retrieval/vector.py    — embedding scoring with in-memory cache
    retrieval/snippets.py  — best-line snippet extraction
    retrieval/merger.py    — extraction merging, formatting, and synthesis
"""

import logging
from typing import Iterable

from hr_breaker.config import get_settings
from hr_breaker.filters.keyword_matcher import check_keywords
from hr_breaker.models.job_posting import JobPosting
from hr_breaker.models.profile import Profile, ProfileDocument, RankedProfileDocument
from hr_breaker.models.resume import ResumeSource
from hr_breaker.services.retrieval.lexical import lexical_scores as _lexical_scores_fn
from hr_breaker.services.retrieval.merger import (
    format_extraction as _format_extraction,
    merge_extractions as _merge_extractions,
    synthesize_from_extractions,
    synthesize_from_whole_docs,
)
from hr_breaker.services.retrieval.snippets import build_snippet as _build_snippet_fn
from hr_breaker.services.retrieval.snippets import _job_text
from hr_breaker.services.retrieval.vector import vector_scores as _vector_scores_fn

logger = logging.getLogger(__name__)


# Keep these as module-level callables so tests can patch
# `hr_breaker.services.profile_retrieval._vector_scores` / `_lexical_scores`
def _lexical_scores(job_text: str, documents: list[ProfileDocument]) -> list[float]:
    return _lexical_scores_fn(job_text, documents)


async def _vector_scores(job_text: str, documents: list[ProfileDocument]) -> list[float | None]:
    return await _vector_scores_fn(job_text, documents)


async def rank_profile_documents(
    documents: Iterable[ProfileDocument],
    job: JobPosting,
) -> list[RankedProfileDocument]:
    """Score every selected document; callers decide how many to display."""
    selected_documents = list(documents)
    if not selected_documents:
        return []

    job_text = _job_text(job)
    lex_scores = _lexical_scores(job_text, selected_documents)
    vec_scores = await _vector_scores(job_text, selected_documents)

    ranked: list[RankedProfileDocument] = []
    for document, lexical_score, vector_score in zip(selected_documents, lex_scores, vec_scores):
        keyword_score = check_keywords(document.content_text, job, threshold=0.0).score
        if vector_score is not None:
            combined_score = lexical_score * 0.65 + keyword_score * 0.2 + vector_score * 0.15
        else:
            combined_score = lexical_score * 0.765 + keyword_score * 0.235
        ranked.append(
            RankedProfileDocument(
                document=document,
                lexical_score=lexical_score,
                keyword_score=keyword_score,
                vector_score=vector_score,
                score=combined_score,
                snippet=_build_snippet_fn(document, job),
            )
        )

    ranked.sort(key=lambda item: (item.score, item.document.timestamp), reverse=True)
    return ranked


def synthesize_profile_resume_source(
    profile: Profile,
    selected_documents: Iterable[ProfileDocument],
    ranked_documents: Iterable[RankedProfileDocument],
) -> ResumeSource:
    selected = list(selected_documents)
    ranked = list(ranked_documents)
    if not selected:
        raise ValueError("At least one profile document must be selected")

    header_lines = [f"Profile: {profile.display_name}"]
    if profile.full_name:
        header_lines.append(f"Candidate: {profile.full_name}")
    if profile.instructions:
        header_lines.extend(["", "Profile instructions:", profile.instructions.strip()])
    header = "\n".join(header_lines)

    max_chars = get_settings().profile_source_max_chars
    result = synthesize_from_extractions(profile, selected, ranked, header, max_chars)
    if result is not None:
        return result

    return synthesize_from_whole_docs(profile, selected, ranked, header, max_chars)
