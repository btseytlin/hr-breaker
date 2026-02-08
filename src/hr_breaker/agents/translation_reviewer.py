"""Translation reviewer agent - checks translation quality for resumes."""

import logging
from datetime import date

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from hr_breaker.config import get_model_settings, get_settings
from hr_breaker.models import JobPosting
from hr_breaker.models.language import Language

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a bilingual resume quality reviewer fluent in both English and {language_english} ({language_native}).

Your task: review a resume translation from English to {language_english} and verify its quality.

EVALUATION CRITERIA (check all):

1. TERMINOLOGY (most important):
   - Correct professional terms for the job field/industry
   - Field-specific vocabulary matches what {language_english}-speaking professionals actually use
   - No awkward literal translations of industry terms
   - Technical terms handled correctly (kept in English when standard, translated when accepted equivalent exists)

2. NATURALNESS:
   - Reads like a native {language_english} speaker wrote it
   - No "machine translation" feel — no unnatural word order, no awkward phrasing
   - Idiomatic expressions used where appropriate
   - Professional tone maintained throughout

3. COMPLETENESS:
   - No content lost in translation (all sections, bullets, details preserved)
   - No content added that wasn't in the original
   - Numbers, dates, metrics preserved exactly

4. CONSISTENCY:
   - Same term used throughout for the same concept
   - Consistent style and register across all sections
   - No mixing of formal/informal tone

5. GRAMMAR AND STYLE:
   - Correct grammar in {language_english}
   - Proper punctuation for {language_english}-language text
   - Professional resume style appropriate for {language_english}-language job market

6. HTML STRUCTURE:
   - All HTML tags preserved intact
   - No broken or modified HTML structure
   - CSS classes unchanged

SCORING:
- 1.0: Perfect translation — natural, accurate, professional
- 0.8-0.99: Minor issues — small phrasing improvements possible
- 0.6-0.79: Noticeable issues — awkward phrasing, wrong terms, or missing content
- 0.4-0.59: Significant problems — multiple wrong terms, unnatural language
- 0.0-0.39: Poor quality — reads like machine translation, major errors

Set passed=true if score >= 0.8
"""


class TranslationReview(BaseModel):
    passed: bool = Field(description="True if translation quality is acceptable")
    score: float = Field(
        ge=0.0, le=1.0, description="Translation quality score (0-1)"
    )
    issues: list[str] = Field(
        default_factory=list,
        description="Specific issues found in the translation",
    )
    suggestions: list[str] = Field(
        default_factory=list,
        description="Concrete suggestions for improvement",
    )
    reasoning: str = Field(description="Brief explanation of the score")


def get_translation_reviewer_agent(language: Language) -> Agent:
    """Create translation reviewer agent for the given language."""
    settings = get_settings()
    prompt = SYSTEM_PROMPT.format(
        language_english=language.english_name,
        language_native=language.native_name,
    )
    agent = Agent(
        f"google-gla:{settings.gemini_flash_model}",
        output_type=TranslationReview,
        system_prompt=prompt,
        model_settings=get_model_settings(),
    )

    @agent.system_prompt
    def add_current_date() -> str:
        return f"Today's date: {date.today().strftime('%B %Y')}"

    return agent


async def review_translation(
    original_html: str,
    translated_html: str,
    language: Language,
    job: JobPosting,
) -> TranslationReview:
    """Review translation quality of a resume.

    Args:
        original_html: English HTML body
        translated_html: Translated HTML body
        language: Target language
        job: Job posting (for field context)
    """
    prompt = f"""Review this resume translation from English to {language.english_name}.

## Job Context:
- Title: {job.title}
- Company: {job.company}
- Field: {', '.join(job.keywords[:10])}

## Original English HTML:
{original_html}

## Translated {language.english_name} HTML:
{translated_html}

Evaluate the translation quality. Be specific about any issues — quote the problematic text and suggest corrections.
Focus especially on:
- Are professional terms correct for this industry in {language.english_name}?
- Does it read naturally, like a native speaker wrote it?
- Is all content preserved?
"""

    agent = get_translation_reviewer_agent(language)
    result = await agent.run(prompt)
    r = result.output
    logger.debug(
        "review_translation: score=%.2f, passed=%s, issues=%d",
        r.score,
        r.passed,
        len(r.issues),
    )
    return r
