"""Translation quality checker agent - evaluates non-English resume quality."""

import logging
from datetime import date

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from hr_breaker.config import get_flash_model, get_model_settings
from hr_breaker.models import FilterResult, JobPosting, OptimizedResume, ResumeSource
from hr_breaker.models.language import Language
from hr_breaker.utils.optimization_telemetry import run_tracked_agent

logger = logging.getLogger(__name__)


class TranslationQualityResult(BaseModel):
    score: float = Field(
        ge=0.0, le=1.0, description="Translation quality score (0-1)"
    )
    issues: list[str] = Field(
        default_factory=list,
        description="Specific translation quality issues found",
    )
    suggestions: list[str] = Field(
        default_factory=list,
        description="Concrete suggestions for improvement",
    )


SYSTEM_PROMPT = """You are a bilingual resume quality reviewer fluent in both English and {language_english} ({language_native}).

Your task: evaluate whether this resume, written in {language_english}, reads like a professional native speaker wrote it.

EVALUATION CRITERIA:

1. TERMINOLOGY (most important):
   - Correct professional terms for the job field/industry
   - Field-specific vocabulary matches what {language_english}-speaking professionals actually use
   - No awkward literal translations of industry terms
   - Technical terms handled correctly: kept in English when standard in the {language_english} tech community, translated when an accepted equivalent exists
   - Job titles should follow {language_english} market conventions

2. NATURALNESS:
   - Reads like a native {language_english} speaker wrote it
   - No "machine translation" feel — no unnatural word order, no awkward phrasing
   - No unnecessary mixing of English where {language_english} equivalents are standard
   - Professional tone maintained throughout

3. CONSISTENCY:
   - Same term used throughout for the same concept
   - Consistent style and register across all sections
   - Section headings all in {language_english} (not mixed with English)

4. GRAMMAR AND STYLE:
   - Correct grammar in {language_english}
   - Proper punctuation for {language_english}-language text
   - Professional resume style appropriate for {language_english}-language job market

ACCEPTABLE (do NOT penalize):
- Technical terms universally kept in English in the {language_english} tech community (Python, Docker, Kubernetes, API, ML, etc.)
- Company names in original language
- Proper nouns and brand names
- Mixed-language bullet points where English technical terms are embedded in {language_english} sentences (this is normal in tech resumes)

SCORING:
- 0.95-1.0: Natural, professional {language_english} resume — reads like a native wrote it
- 0.90-0.94: Only stylistic or debatable issues remain — nothing clearly wrong
- 0.70-0.89: Noticeable issues — awkward phrasing, wrong terms, unnecessary English
- 0.50-0.69: Significant problems — multiple wrong terms, unnatural language
- 0.0-0.49: At least one glaring error — completely wrong term, nonsensical phrase, or reads like machine translation

When listing issues, quote the problematic text and suggest the correct {language_english} phrasing.
"""


def get_translation_checker_agent(language: Language) -> Agent:
    """Create translation quality checker agent for the given language."""
    prompt = SYSTEM_PROMPT.format(
        language_english=language.english_name,
        language_native=language.native_name,
    )
    agent = Agent(
        get_flash_model(),
        output_type=TranslationQualityResult,
        system_prompt=prompt,
        model_settings=get_model_settings(),
    )

    @agent.system_prompt
    def add_current_date() -> str:
        return f"Today's date: {date.today().strftime('%B %Y')}"

    return agent


async def check_translation_quality(
    optimized: OptimizedResume,
    source: ResumeSource,
    job: JobPosting,
    language: Language,
) -> FilterResult:
    """Evaluate translation quality of a non-English resume.

    Args:
        optimized: The optimized resume (in target language)
        source: Original resume source (English)
        job: Job posting for field context
        language: Target language
    """
    content = optimized.pdf_text or optimized.html or "(no content)"

    prompt = f"""Evaluate the {language.english_name} language quality of this resume.

## Job Context (for terminology reference):
- Title: {job.title}
- Company: {job.company}
- Field keywords: {', '.join(job.keywords[:15])}

## Original Resume (English):
{source.content[:3000]}

## Resume in {language.english_name} to evaluate:
{content}

Rate the {language.english_name} language quality. Be specific about issues — quote problematic text and suggest corrections.
"""

    agent = get_translation_checker_agent(language)
    result = await run_tracked_agent(agent, prompt, component="TranslationQualityChecker")
    r = result.output

    logger.debug(
        "check_translation_quality: score=%.2f, issues=%d",
        r.score,
        len(r.issues),
    )

    return FilterResult(
        filter_name="TranslationQualityChecker",
        passed=False,  # threshold set by filter
        score=r.score,
        issues=r.issues,
        suggestions=r.suggestions,
    )
