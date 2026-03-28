from datetime import datetime
from unittest.mock import patch

import pytest

from hr_breaker.models import JobPosting, ProfileDocument
from hr_breaker.services.profile_retrieval import rank_profile_documents


@pytest.mark.asyncio
async def test_rank_profile_documents_orders_by_relevance_without_embeddings():
    job = JobPosting(
        title="Machine Learning Engineer",
        company="Acme",
        requirements=["Python", "LLM", "Vector search"],
        keywords=["python", "llm", "vector search"],
        description="Build retrieval systems and production ML services.",
    )
    relevant = ProfileDocument(
        profile_id="candidate",
        kind="resume",
        title="ML Resume",
        source_name="ml_resume.md",
        content_text="Built Python LLM retrieval services with vector search and ranking pipelines.",
        timestamp=datetime(2026, 1, 2),
    )
    unrelated = ProfileDocument(
        profile_id="candidate",
        kind="note",
        title="Music Club",
        source_name="music.txt",
        content_text="Organized campus music events and weekly rehearsals for the club.",
        timestamp=datetime(2026, 1, 1),
    )

    with patch("hr_breaker.services.profile_retrieval._vector_scores", return_value=[None, None]):
        ranked = await rank_profile_documents([unrelated, relevant], job)

    assert [match.document.title for match in ranked] == ["ML Resume", "Music Club"]
    assert ranked[0].score > ranked[1].score
    assert "vector search" in ranked[0].snippet.lower()


@pytest.mark.asyncio
async def test_rank_profile_documents_returns_all_selected_docs():
    """rank_profile_documents scores every selected doc — callers decide how many to show."""
    job = JobPosting(
        title="Backend Engineer",
        company="Acme",
        requirements=["Python"],
        keywords=["python"],
        description="Build APIs.",
    )
    documents = [
        ProfileDocument(
            profile_id="candidate",
            kind="resume",
            title=f"Resume {index}",
            source_name=f"resume_{index}.md",
            content_text=f"Python API work #{index}",
        )
        for index in range(3)
    ]

    with patch("hr_breaker.services.profile_retrieval._vector_scores", return_value=[None, None, None]):
        ranked = await rank_profile_documents(documents, job)

    assert len(ranked) == 3
