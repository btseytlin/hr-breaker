import pytest
from unittest.mock import patch

from hr_breaker.models import (
    DocumentExtraction,
    EducationEntry,
    ExperienceEntry,
    PersonalInfo,
    Profile,
    ProfileDocument,
    RankedProfileDocument,
    SkillsEntry,
)
from hr_breaker.models.job_posting import JobPosting
from hr_breaker.services.profile_retrieval import (
    _format_extraction,
    _merge_extractions,
    rank_profile_documents,
    synthesize_profile_resume_source,
)


def _make_profile(**kwargs):
    defaults = dict(id="jane_doe", display_name="Jane Doe", first_name="Jane", last_name="Doe")
    return Profile(**{**defaults, **kwargs})


def _make_doc(profile_id="jane_doe", *, kind="resume", title="Resume", content="some content", metadata=None):
    return ProfileDocument(
        profile_id=profile_id,
        kind=kind,
        title=title,
        source_name=f"{title}.md",
        content_text=content,
        metadata=metadata or {},
    )


def _ranked(doc, score=0.5):
    return RankedProfileDocument(
        document=doc,
        lexical_score=score,
        keyword_score=score,
        vector_score=None,
        score=score,
        snippet="",
    )


# --- Whole-doc fallback (no extractions) ---

def test_fallback_includes_profile_header():
    profile = _make_profile(instructions="Focus on ML.")
    doc = _make_doc(content="Built Python ML systems.")
    source = synthesize_profile_resume_source(profile, [doc], [_ranked(doc)])
    assert source.first_name == "Jane"
    assert source.last_name == "Doe"
    assert "Focus on ML." in source.content
    assert "Profile documents:" in source.content
    assert "Built Python ML systems." in source.content


def test_fallback_whole_doc_skip_not_bisect():
    """Over-budget docs are skipped whole; smaller subsequent docs still fit."""
    profile = _make_profile()
    big = _make_doc(title="Big", content="x" * 15000)
    small = _make_doc(title="Small", content="small content", kind="note")
    source = synthesize_profile_resume_source(profile, [big, small], [_ranked(big, 0.9), _ranked(small, 0.1)])
    # Small doc fits, big is skipped whole
    assert "small content" in source.content
    assert "omitted" in source.content
    assert "Big" in source.content   # mentioned in omitted note
    # Content must not end mid-word (no bisection)
    assert "x" * 100 not in source.content


def test_fallback_omits_all_docs_when_everything_is_over_budget():
    """If every doc exceeds budget, omit them rather than blowing past the cap."""
    profile = _make_profile()
    doc = _make_doc(content="x" * 15000)
    source = synthesize_profile_resume_source(profile, [doc], [_ranked(doc)])
    assert "x" * 100 not in source.content
    assert "Note: omitted (over budget): Resume" in source.content


def test_fallback_uses_last_name_without_duplication():
    profile = _make_profile(first_name=None, last_name="Doe", display_name="Candidate")
    doc = _make_doc(content="Built Python ML systems.")
    source = synthesize_profile_resume_source(profile, [doc], [_ranked(doc)])

    assert source.first_name is None
    assert source.last_name == "Doe"


# --- Extraction path ---

def _extraction(**kwargs):
    defaults = dict(
        summary=[], experience=[], education=[],
        skills=SkillsEntry(), projects=[], publications=[],
    )
    return DocumentExtraction(**{**defaults, **kwargs})


def test_extraction_path_used_when_present():
    profile = _make_profile()
    ext = _extraction(summary=["ML engineer with 8 years experience."])
    doc = _make_doc(metadata={"extraction": ext.model_dump()})
    source = synthesize_profile_resume_source(profile, [doc], [_ranked(doc)])
    assert "ML engineer with 8 years experience." in source.content
    assert "## Summary" in source.content
    # Whole-doc section header should NOT appear
    assert "Profile documents:" not in source.content


def test_extraction_path_sets_contact_info_in_content():
    profile = _make_profile()
    ext = _extraction(
        personal_info=PersonalInfo(
            email="candidate@example.test",
            phone="555-0100",
            linkedin="mock-candidate",
            github="mock-candidate",
            website="portfolio.example.test",
        ),
        summary=["Researcher."],
    )
    doc = _make_doc(metadata={"extraction": ext.model_dump()})
    source = synthesize_profile_resume_source(profile, [doc], [_ranked(doc)])

    assert "Candidate name: Jane Doe" in source.content
    assert "mock-candidate" in source.content
    assert "portfolio.example.test" in source.content


def test_extraction_path_uses_profile_display_name_for_missing_last_name():
    profile = _make_profile(first_name="Mock", last_name=None, display_name="Mock Candidate")
    ext = _extraction(
        personal_info=PersonalInfo(name="Mock"),
        summary=["Policy researcher."],
    )
    doc = _make_doc(metadata={"extraction": ext.model_dump()})
    source = synthesize_profile_resume_source(profile, [doc], [_ranked(doc)])

    assert source.first_name == "Mock"
    assert source.last_name == "Candidate"
    assert "Candidate name: Mock Candidate" in source.content


def test_extraction_path_notes_missing_extractions():
    profile = _make_profile()
    ext = _extraction(summary=["Data scientist."])
    doc_with = _make_doc(title="CV", metadata={"extraction": ext.model_dump()})
    doc_without = _make_doc(title="Note", kind="note", content="extra context")
    source = synthesize_profile_resume_source(profile, [doc_with, doc_without], [_ranked(doc_with)])
    assert "Data scientist." in source.content
    assert "extraction unavailable" in source.content
    assert "Note" in source.content


# --- _merge_extractions ---

def test_merge_deduplicates_experience():
    exp = ExperienceEntry(employer="Acme", title="Engineer", start="2020", end="Present", bullets=["Built things"])
    ext1 = _extraction(experience=[exp])
    ext2 = _extraction(experience=[exp])  # exact duplicate
    merged = _merge_extractions([ext1, ext2])
    assert len(merged.experience) == 1


def test_merge_deduplicates_skills():
    ext1 = _extraction(skills=SkillsEntry(technical=["Python", "SQL"]))
    ext2 = _extraction(skills=SkillsEntry(technical=["SQL", "Go"]))
    merged = _merge_extractions([ext1, ext2])
    assert set(merged.skills.technical) == {"Go", "Python", "SQL"}


def test_merge_sorts_experience_present_first():
    old = ExperienceEntry(employer="A", title="Dev", start="2015", end="2018", bullets=[])
    current = ExperienceEntry(employer="B", title="Lead", start="2021", end="Present", bullets=[])
    merged = _merge_extractions([_extraction(experience=[old, current])])
    assert merged.experience[0].end == "Present"


# --- _format_extraction ---

def test_format_extraction_sections():
    ext = DocumentExtraction(
        summary=["Experienced ML engineer."],
        experience=[ExperienceEntry(employer="Acme", title="MLE", start="2020", end="Present", bullets=["Led infra"])],
        education=[EducationEntry(institution="MIT", degree="BS", field="CS", start="2016", end="2020")],
        skills=SkillsEntry(technical=["Python"], certifications=["AWS SAA"]),
        projects=[],
        publications=["Smith et al. 2023"],
    )
    text = _format_extraction(ext)
    assert "## Summary" in text
    assert "Experienced ML engineer." in text
    assert "## Experience" in text
    assert "Acme — MLE | 2020 – Present" in text
    assert "Led infra" in text
    assert "## Education" in text
    assert "MIT" in text
    assert "## Skills" in text
    assert "Python" in text
    assert "AWS SAA" in text
    assert "## Publications" in text
    assert "Smith et al. 2023" in text


def test_format_extraction_contact():
    ext = DocumentExtraction(
        personal_info=PersonalInfo(email="x@y.com", linkedin="linkedin.com/in/x", github="github.com/x"),
    )
    text = _format_extraction(ext)
    assert "## Contact" in text
    assert "x@y.com" in text
    assert "linkedin.com/in/x" in text
    assert "github.com/x" in text


def test_merge_contact_first_non_none():
    ext1 = DocumentExtraction(personal_info=PersonalInfo(email="a@b.com"))
    ext2 = DocumentExtraction(personal_info=PersonalInfo(email="c@d.com", phone="123"))
    merged = _merge_extractions([ext1, ext2])
    assert merged.personal_info.email == "a@b.com"  # first wins
    assert merged.personal_info.phone == "123"       # filled from second


def test_merge_preserves_name_from_first_extraction():
    """name field must be merged like email/phone — it was previously missing from the loop."""
    ext1 = DocumentExtraction(personal_info=PersonalInfo(name="Alice Smith", email="alice@example.test"))
    ext2 = DocumentExtraction(personal_info=PersonalInfo(phone="555-0100"))
    merged = _merge_extractions([ext1, ext2])
    assert merged.personal_info.name == "Alice Smith"
    assert merged.personal_info.email == "alice@example.test"
    assert merged.personal_info.phone == "555-0100"


def test_synthesize_with_empty_ranked_and_unextracted_doc():
    """synthesize_profile_resume_source must not crash when ranked=[] and unextracted docs exist."""
    profile = _make_profile()
    ext = _extraction(summary=["Senior engineer."])
    doc_extracted = _make_doc(title="CV", metadata={"extraction": ext.model_dump()})
    doc_raw = _make_doc(title="Note", kind="note", content="UniqueRawContent")

    # Pass empty ranked list — should not crash, raw doc should still appear
    source = synthesize_profile_resume_source(profile, [doc_extracted, doc_raw], [])
    assert "Senior engineer." in source.content
    assert "UniqueRawContent" in source.content or "Note" in source.content


def test_merge_caps_summaries():
    ext = DocumentExtraction(summary=["S1", "S2", "S3", "S4"])
    merged = _merge_extractions([ext])
    assert len(merged.summary) == 2


def test_merge_experience_unions_bullets_for_same_role():
    """Same role across two docs should merge bullets, not drop the second entry."""
    exp1 = ExperienceEntry(employer="Acme", title="Engineer", start="2020", end="2022", bullets=["Built API"])
    exp2 = ExperienceEntry(employer="Acme", title="Engineer", start="2020", end="2022", bullets=["Led migration"])
    merged = _merge_extractions([_extraction(experience=[exp1]), _extraction(experience=[exp2])])
    assert len(merged.experience) == 1
    bullets = merged.experience[0].bullets
    assert "Built API" in bullets
    assert "Led migration" in bullets


def test_merge_experience_prefers_more_specific_end_date():
    """When two entries match, the more recent/specific end date wins."""
    exp_present = ExperienceEntry(employer="Acme", title="Engineer", start="2020", end="Present", bullets=[])
    exp_old = ExperienceEntry(employer="Acme", title="Engineer", start="2020", end="2022", bullets=[])
    merged = _merge_extractions([_extraction(experience=[exp_old]), _extraction(experience=[exp_present])])
    assert merged.experience[0].end == "Present"


def test_merge_education_unions_notes_and_fills_dates():
    """Same degree across two docs merges notes and fills in missing dates."""
    edu1 = EducationEntry(institution="MIT", degree="BS", field="CS", start="2016", end=None, notes=["GPA 3.9"])
    edu2 = EducationEntry(institution="MIT", degree="BS", field=None, start=None, end="2020", notes=["Dean's list"])
    merged = _merge_extractions([_extraction(education=[edu1]), _extraction(education=[edu2])])
    assert len(merged.education) == 1
    edu = merged.education[0]
    assert edu.end == "2020"
    assert edu.start == "2016"
    assert edu.field == "CS"
    assert "GPA 3.9" in edu.notes
    assert "Dean's list" in edu.notes


# --- Integration: mixed extracted + unextracted docs ---

def test_mixed_mode_includes_raw_text_of_unextracted_docs():
    """Selected docs without extraction must still appear as raw text in synthesis output."""
    profile = _make_profile()
    ext = _extraction(summary=["Senior engineer."])
    doc_extracted = _make_doc(title="CV", metadata={"extraction": ext.model_dump()})
    doc_raw = _make_doc(title="Hackathon note", kind="note", content="Won first place at HackMTL 2024.")

    source = synthesize_profile_resume_source(
        profile,
        [doc_extracted, doc_raw],
        [_ranked(doc_extracted, 0.8), _ranked(doc_raw, 0.6)],
    )

    # Structured extraction is present
    assert "Senior engineer." in source.content
    assert "## Summary" in source.content
    # Raw content of unextracted doc is also present
    assert "Won first place at HackMTL 2024." in source.content
    # Whole-doc fallback header must NOT appear (we used extraction path)
    assert "Profile documents:" not in source.content


def test_mixed_mode_over_budget_unextracted_doc_is_noted():
    """Unextracted docs that exceed remaining budget are noted but not silently dropped."""
    profile = _make_profile()
    ext = _extraction(summary=["x" * 10000])  # big extraction fills most of budget
    doc_extracted = _make_doc(title="CV", metadata={"extraction": ext.model_dump()})
    doc_raw = _make_doc(title="Big note", kind="note", content="y" * 5000)

    source = synthesize_profile_resume_source(
        profile,
        [doc_extracted, doc_raw],
        [_ranked(doc_extracted, 0.9), _ranked(doc_raw, 0.5)],
    )

    # The over-budget unextracted doc should be mentioned explicitly
    assert "Big note" in source.content
    # Content should not be silently dropped without acknowledgement
    assert "omitted" in source.content or "y" * 100 in source.content


# --- End-to-end: rank_profile_documents → synthesize_profile_resume_source ---

@pytest.mark.asyncio
async def test_e2e_all_selected_docs_survive_into_source():
    """All selected documents must contribute content to the synthesized resume source.

    This exercises the real rank_profile_documents → synthesize_profile_resume_source
    pipeline with 5 docs (some extracted, some not) and verifies that each doc's
    unique content appears in the output — no silent drops due to top_k cutoffs
    or missing score_by_id entries.
    """
    profile = _make_profile()
    job = JobPosting(
        title="Senior Python Engineer",
        company="Acme",
        requirements=["Python", "AWS"],
        keywords=["python", "aws"],
        description="Build scalable cloud systems in Python.",
    )

    # Two docs with extraction data
    ext_a = _extraction(
        summary=["8 years Python at scale."],
        experience=[ExperienceEntry(employer="Alpha", title="Lead", start="2018", end="Present", bullets=["UniqueAlphaBullet"])],
    )
    ext_b = _extraction(
        skills=SkillsEntry(technical=["UniqueSkillBeta", "AWS"])
    )
    doc_a = _make_doc(title="Main CV", kind="resume", content="Python cloud work", metadata={"extraction": ext_a.model_dump()})
    doc_b = _make_doc(title="Skills doc", kind="note", content="AWS skills", metadata={"extraction": ext_b.model_dump()})

    # Three docs without extraction (raw text only)
    doc_c = _make_doc(title="Hackathon", kind="note", content="UniqueHackathonContent won first place")
    doc_d = _make_doc(title="Paper", kind="paper", content="UniqueResearchPaperAbstract on distributed systems")
    doc_e = _make_doc(title="Side project", kind="other", content="UniqueProjectContent built in Rust")

    all_docs = [doc_a, doc_b, doc_c, doc_d, doc_e]

    # Mock vector scores (None = no embedding API), still get lexical/keyword scores
    with patch("hr_breaker.services.profile_retrieval._vector_scores", return_value=[None] * 5):
        ranked = await rank_profile_documents(all_docs, job)

    # rank_profile_documents must return a score for every selected doc
    assert len(ranked) == 5, "rank_profile_documents must score all selected docs"
    ranked_ids = {r.document.id for r in ranked}
    assert all(d.id in ranked_ids for d in all_docs), "every selected doc must appear in ranked list"

    source = synthesize_profile_resume_source(profile, all_docs, ranked)

    # Extracted content from doc_a and doc_b
    assert "UniqueAlphaBullet" in source.content
    assert "UniqueSkillBeta" in source.content

    # Raw text from the three unextracted docs (or they are noted as over-budget)
    for unique_marker, doc_title in [
        ("UniqueHackathonContent", "Hackathon"),
        ("UniqueResearchPaperAbstract", "Paper"),
        ("UniqueProjectContent", "Side project"),
    ]:
        present = unique_marker in source.content
        noted = doc_title in source.content  # mentioned in "omitted (over budget)" note
        assert present or noted, f"Doc '{doc_title}' must appear in content or be explicitly noted as omitted"
