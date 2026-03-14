"""Lexical (TF-IDF) document scoring against a job posting."""

import re

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from hr_breaker.config import get_settings
from hr_breaker.models.profile import ProfileDocument

_TOKEN_PATTERN = r"(?u)\b[a-zA-Z][a-zA-Z0-9+#.-]*\b"


def _normalize_text(value: str) -> str:
    return " ".join(value.split())


def lexical_scores(job_text: str, documents: list[ProfileDocument]) -> list[float]:
    """Return TF-IDF cosine similarity of each document against job_text."""
    if not documents:
        return []
    corpus = [job_text, *[_normalize_text(document.content_text) for document in documents]]
    try:
        vectorizer = TfidfVectorizer(
            stop_words="english",
            ngram_range=(1, 2),
            max_features=get_settings().keyword_tfidf_max_features,
            token_pattern=_TOKEN_PATTERN,
        )
        matrix = vectorizer.fit_transform(corpus)
    except ValueError:
        return [0.0 for _ in documents]

    job_vector = matrix[0:1]
    similarities = cosine_similarity(job_vector, matrix[1:]).flatten()
    return [float(value) for value in similarities]
