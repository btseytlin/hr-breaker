from datetime import date
from functools import lru_cache

import fitz
from pydantic import BaseModel, Field
from pydantic_ai import Agent, BinaryContent

from hr_breaker.config import get_flash_model, get_model_settings
from hr_breaker.models import JobPosting, OptimizedResume
from hr_breaker.models.language import Language
from hr_breaker.services.renderer import get_renderer, RenderError
from hr_breaker.utils.retry import run_with_retry


class CombinedReviewResult(BaseModel):
    """Combined vision + ATS review result."""

    # Vision fields
    looks_professional: bool = Field(description="True if resume looks professional")
    visual_issues: list[str] = Field(
        default_factory=list,
        description="List of visual/formatting issues found",
    )
    visual_feedback: str = Field(
        default="",
        description="Free-form description of visual issues and suggestions",
    )

    # ATS fields
    keyword_score: float = Field(
        ge=0.0, le=1.0, description="Keyword/skills match rate"
    )
    experience_score: float = Field(ge=0.0, le=1.0, description="Experience alignment")
    education_score: float = Field(ge=0.0, le=1.0, description="Education fit")
    overall_fit_score: float = Field(
        ge=0.0, le=1.0, description="Holistic fit for the role"
    )
    disqualified: bool = Field(description="Auto-reject triggered")
    ats_issues: list[str] = Field(
        default_factory=list, description="ATS-related issues"
    )


# Weights for ATS score aggregation
SCORE_WEIGHTS = {
    "keyword": 0.25,
    "experience": 0.175,
    "education": 0.075,
    "overall_fit": 0.50,
}


SYSTEM_PROMPT = """
You are TalentScreen ATS v4.2, an enterprise applicant tracking system.

Your task is to perform two evaluations:

1. Visual resume quality assessment
2. ATS resume screening

=== PART 1: VISUAL QUALITY CHECK  ===

CHECK FOR PROFESSIONALISM:
- Does this look like a polished, professionally formatted resume document?
- Is the overall visual impression clean and organized?
- Would this make a good first impression on a recruiter?

FLAG THESE VISUAL ISSUES:
- Text running off the page or cut off
- Broken/mangled lines or words split incorrectly
- Overlapping text or elements
- Inconsistent spacing (too cramped or too sparse)
- Unreadable fonts or garbled characters
- Missing sections that appear cut off
- Tables or lists that are misaligned
- Page breaks in wrong places
- Duplicate or repeated visual elements
- Visual artifacts or rendering glitches
- Inconsistent bullet styles within the same list

CHECK FORMATTING STANDARDS:
- Font size appears readable (roughly 11-14pt range)
- Section headers are clear
- Dates are aligned consistently
- Bullets are properly indented and aligned
- Consistent date format throughout
- No orphan lines
- Balanced whitespace
- ~3-5 lines per bullet point
- All bullets same indent level within a section

CHECK PROFESSIONAL LANGUAGE:
- Active voice
- No slang or casual tone
- Natural sounding language

ACCEPTABLE (DO NOT FLAG):
- Standard resume formatting variations
- Different font choices (as long as readable)
- Various layout styles (single/multi-column)
- Dense but readable content
- A few minor spelling mistakes (1-3 total)
- Underlined links and other intentional styling
- Technical jargon or abbreviations

=== PART 2: ATS SCREENING ===

Score each category 0.0-1.0:

1. KEYWORD MATCH: Exact and semantic matches to job requirements/keywords.
   - Full match = 100%, partial/synonym = 50%, missing = 0%
   - Required skills weighted 2x vs preferred skills

2. EXPERIENCE ALIGNMENT: Does work history demonstrate required competencies?
   - Years of experience vs requirement
   - Relevance of previous roles/industries
   - Progression and seniority level match

3. EDUCATION FIT: Degree level, field relevance, institution
   - Required degree = pass, missing required = automatic fail

4. OVERALL FIT: Holistic assessment of candidate for the role
   - Would this person succeed in the position?
   - Culture/team fit signals
   - Career trajectory alignment

DISQUALIFICATION RULES (Auto-Reject) - Set disqualified=true if ANY:
- Missing required degree/certification
- Less than minimum required years of experience
- Missing 3+ required skills
- Obvious red flags (unreadable, clearly machine generated, obvious lies/inconsistencies)

=== OUTPUT ===
Return ALL fields:
- looks_professional: true only if visually clean and properly formatted
- visual_issues: specific visual problems found
- visual_feedback: detailed description for the optimizer to fix visual issues
- keyword_score, experience_score, education_score, overall_fit_score: 0.0-1.0
- disqualified: true if auto-reject triggered
- ats_issues: specific ATS failures found
"""


@lru_cache
def get_combined_reviewer_agent() -> Agent:
    agent = Agent(
        get_flash_model(),
        output_type=CombinedReviewResult,
        system_prompt=SYSTEM_PROMPT,
        model_settings=get_model_settings(),
    )

    @agent.system_prompt
    def add_current_date() -> str:
        return f"Today's date: {date.today().strftime('%B %Y')}"

    return agent


def pdf_to_image(pdf_bytes: bytes) -> tuple[bytes, int]:
    """Convert first page of PDF to PNG bytes.

    Returns (image_bytes, page_count).
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        page_count = len(doc)
        page = doc[0]
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
        return pix.tobytes("png"), page_count
    finally:
        doc.close()


async def combined_review(
    optimized: OptimizedResume,
    job: JobPosting,
    language: Language | None = None,
) -> tuple[CombinedReviewResult, bytes | None, int, list[str]]:
    """Combined vision + ATS review in single LLM call.

    Args:
        optimized: The optimized resume
        job: Job posting to match against

    Returns (result, pdf_bytes, page_count, render_warnings).
    pdf_bytes is None if rendering failed.
    """
    renderer = get_renderer()

    # Render PDF
    content = optimized.html if optimized.html is not None else optimized.data
    try:
        if isinstance(content, str):
            render_result = renderer.render(content)
        else:
            render_result = renderer.render_data(content)
        pdf_bytes = render_result.pdf_bytes
        render_warnings = render_result.warnings
        page_count = render_result.page_count
    except RenderError as e:
        return (
            CombinedReviewResult(
                looks_professional=False,
                visual_issues=[f"Rendering failed: {str(e)}"],
                visual_feedback=str(e),
                keyword_score=0.0,
                experience_score=0.0,
                education_score=0.0,
                overall_fit_score=0.0,
                disqualified=True,
                ats_issues=["Cannot evaluate - rendering failed"],
            ),
            None,
            0,
            [],
        )

    # Convert to image
    try:
        image_bytes, page_count = pdf_to_image(pdf_bytes)
    except Exception as e:
        return (
            CombinedReviewResult(
                looks_professional=False,
                visual_issues=[f"PDF to image conversion failed: {str(e)}"],
                visual_feedback=str(e),
                keyword_score=0.0,
                experience_score=0.0,
                education_score=0.0,
                overall_fit_score=0.0,
                disqualified=True,
                ats_issues=["Cannot evaluate - PDF conversion failed"],
            ),
            pdf_bytes,
            0,
            render_warnings,
        )

    # Get resume text for ATS evaluation
    if optimized.pdf_text:
        resume_text = optimized.pdf_text
    elif optimized.html:
        resume_text = optimized.html
    elif optimized.data:
        resume_text = optimized.data.model_dump_json(indent=2)
    else:
        resume_text = "(no content)"

    # Build prompt with both image and text
    prompt = f"""COMBINED RESUME REVIEW

=== JOB POSTING ===
Position: {job.title}
Company: {job.company}

{job.description or job.raw_text}

Required Skills: {', '.join(job.requirements)}
Keywords: {', '.join(job.keywords)}

=== RESUME TEXT (for ATS evaluation) ===
{resume_text}

=== RESUME IMAGE (for visual evaluation) ===
See attached image.

Perform BOTH visual quality check AND ATS screening. Return all fields.
"""

    if language is not None and language.code != "en":
        prompt += f"""
=== LANGUAGE NOTE ===
This resume is written in {language.english_name}. This is intentional — the candidate is targeting a {language.english_name}-language job market.
- Evaluate professional language quality IN {language.english_name} (natural phrasing, proper grammar, formal tone)
- Technical terms (Python, Docker, AWS) must be kept in English — this is standard practice
- Keyword matching: compare against job requirements semantically, accounting for the language difference
"""

    agent = get_combined_reviewer_agent()
    result = await run_with_retry(
        agent.run,
        [
            prompt,
            BinaryContent(data=image_bytes, media_type="image/png"),
        ],
    )

    return result.output, pdf_bytes, page_count, render_warnings


def compute_ats_score(result: CombinedReviewResult) -> float:
    """Compute weighted ATS score from individual scores."""
    return (
        result.keyword_score * SCORE_WEIGHTS["keyword"]
        + result.experience_score * SCORE_WEIGHTS["experience"]
        + result.education_score * SCORE_WEIGHTS["education"]
        + result.overall_fit_score * SCORE_WEIGHTS["overall_fit"]
    )
