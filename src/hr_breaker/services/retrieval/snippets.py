"""Snippet extraction — pick the most relevant lines from a document for display."""

import re

from hr_breaker.config import get_settings
from hr_breaker.models.job_posting import JobPosting
from hr_breaker.models.profile import ProfileDocument

_TOKEN_PATTERN = r"(?u)\b[a-zA-Z][a-zA-Z0-9+#.-]*\b"


def _normalize_text(value: str) -> str:
    return " ".join(value.split())


def _tokenize(value: str) -> set[str]:
    return set(re.findall(_TOKEN_PATTERN, value.lower()))


def _job_text(job: JobPosting) -> str:
    return "\n".join([
        f"Title: {job.title}",
        f"Company: {job.company}",
        f"Requirements: {', '.join(job.requirements)}",
        f"Keywords: {', '.join(job.keywords)}",
        f"Description: {job.description or ''}",
    ])


def _segment_candidates(text: str) -> list[str]:
    lines = [line.strip(" \t•*-–") for line in text.splitlines()]
    filtered = [line for line in lines if line]
    if not filtered:
        filtered = [chunk.strip() for chunk in re.split(r"\n\s*\n", text) if chunk.strip()]
    return filtered[:80]


def _score_segment(segment: str, job_terms: set[str], keywords: set[str]) -> float:
    normalized = _normalize_text(segment)
    if not normalized:
        return 0.0
    segment_terms = _tokenize(normalized)
    if not segment_terms:
        return 0.0
    overlap = len(segment_terms & job_terms) / max(len(job_terms), 1)
    keyword_hits = len(segment_terms & keywords) / max(len(keywords), 1) if keywords else 0.0
    length_bonus = min(len(normalized), 220) / 2200
    return overlap * 0.6 + keyword_hits * 0.3 + length_bonus


def build_snippet(document: ProfileDocument, job: JobPosting) -> str:
    segments = _segment_candidates(document.content_text)
    if not segments:
        return document.preview_text

    job_terms = _tokenize(_job_text(job))
    keywords = {keyword.lower() for keyword in job.keywords}
    scored_segments = [
        (segment, _score_segment(segment, job_terms, keywords), index)
        for index, segment in enumerate(segments)
    ]
    best = sorted(scored_segments, key=lambda item: (item[1], -item[2]), reverse=True)[:3]
    ordered = [segment for segment, score, index in sorted(best, key=lambda item: item[2]) if score > 0]
    if not ordered:
        ordered = sorted(segments[:10], key=len, reverse=True)[:2]
    snippet = "\n".join(ordered)
    max_chars = get_settings().profile_snippet_max_chars
    if len(snippet) <= max_chars:
        return snippet
    return f"{snippet[: max_chars - 3]}..."
