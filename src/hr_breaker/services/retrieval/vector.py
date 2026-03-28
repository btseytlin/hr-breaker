"""Vector (embedding) document scoring with in-memory cache."""

import hashlib
import logging
import threading

from litellm import aembedding as litellm_aembedding

from hr_breaker.config import get_embedding_api_base, get_settings, has_api_key_for_model
from hr_breaker.models.profile import ProfileDocument
from hr_breaker.utils.retry import run_with_retry

logger = logging.getLogger(__name__)

# Module-level cache shared across asyncio event loops and background threads.
# The lock serialises reads/writes to prevent cache corruption under concurrent
# extraction (ExtractionWorker uses ThreadPoolExecutor).
_embedding_cache: dict[str, list[float]] = {}
_cache_lock = threading.Lock()


def _cache_key(doc: ProfileDocument) -> str:
    """Cache key based on doc id + full content hash so stale entries are
    never returned when a document is re-uploaded with changed content."""
    content_hash = hashlib.sha256(doc.content_text.encode()).hexdigest()[:16]
    return f"{doc.id}:{content_hash}"


def _normalize_text(value: str) -> str:
    return " ".join(value.split())


def _has_embedding_api_key() -> bool:
    """Check if the configured embedding model has usable credentials."""
    return has_api_key_for_model(get_settings().embedding_model)


async def vector_scores(job_text: str, documents: list[ProfileDocument]) -> list[float | None]:
    """Return embedding cosine similarity of each document against job_text.

    Returns [None, ...] if no embedding API key is configured or on error.
    Results for unchanged documents are served from the module-level cache.
    """
    if not documents:
        return []
    if not _has_embedding_api_key():
        return [None for _ in documents]

    settings = get_settings()
    truncated = [_normalize_text(doc.content_text)[:4000] for doc in documents]
    for doc, text in zip(documents, truncated):
        if len(_normalize_text(doc.content_text)) > 4000:
            logger.debug("Truncating document '%s' for embedding", doc.title)

    to_embed_indices: list[int] = []
    to_embed_texts: list[str] = []
    cached_embeddings: dict[int, list[float]] = {}

    for i, (doc, text) in enumerate(zip(documents, truncated)):
        key = _cache_key(doc)
        with _cache_lock:
            cached = _embedding_cache.get(key)
        if cached is not None:
            cached_embeddings[i] = cached
        else:
            to_embed_indices.append(i)
            to_embed_texts.append(text)

    fresh_embeddings: dict[int, list[float]] = {}
    api_base = get_embedding_api_base()

    if to_embed_texts:
        payload = [job_text, *to_embed_texts]
        try:
            if api_base:
                result = await run_with_retry(
                    litellm_aembedding,
                    model=settings.embedding_model,
                    dimensions=settings.embedding_output_dimensionality,
                    api_base=api_base,
                    input=payload,
                )
            else:
                result = await run_with_retry(
                    litellm_aembedding,
                    model=settings.embedding_model,
                    dimensions=settings.embedding_output_dimensionality,
                    input=payload,
                )
        except Exception as exc:
            logger.warning("Profile retrieval embedding failed: %s", exc)
            return [None for _ in documents]

        raw = [item["embedding"] for item in result.data]
        job_embedding = raw[0]
        for list_idx, (doc_idx, text) in enumerate(zip(to_embed_indices, to_embed_texts)):
            emb = raw[list_idx + 1]
            key = _cache_key(documents[doc_idx])
            with _cache_lock:
                _embedding_cache[key] = emb
            fresh_embeddings[doc_idx] = emb
    else:
        # All documents were cached; still need job embedding for similarity
        try:
            if api_base:
                result = await run_with_retry(
                    litellm_aembedding,
                    model=settings.embedding_model,
                    dimensions=settings.embedding_output_dimensionality,
                    api_base=api_base,
                    input=[job_text],
                )
            else:
                result = await run_with_retry(
                    litellm_aembedding,
                    model=settings.embedding_model,
                    dimensions=settings.embedding_output_dimensionality,
                    input=[job_text],
                )
        except Exception as exc:
            logger.warning("Profile retrieval embedding (job only) failed: %s", exc)
            return [None for _ in documents]
        job_embedding = result.data[0]["embedding"]

    scores: list[float | None] = []
    for i in range(len(documents)):
        emb = cached_embeddings.get(i) or fresh_embeddings.get(i)
        if emb is None:
            scores.append(None)
            continue
        dot = sum(left * right for left, right in zip(job_embedding, emb))
        job_norm = sum(v * v for v in job_embedding) ** 0.5
        doc_norm = sum(v * v for v in emb) ** 0.5
        if not job_norm or not doc_norm:
            # Zero-norm vector means the embedding is degenerate; treat as no signal.
            scores.append(None)
            continue
        similarity = dot / (job_norm * doc_norm)
        scores.append((similarity + 1) / 2)
    return scores
