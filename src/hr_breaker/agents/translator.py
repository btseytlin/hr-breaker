"""Translator agent - translates optimized HTML resume to target language."""

import logging
from datetime import date

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from hr_breaker.config import get_model_settings, get_settings
from hr_breaker.models import JobPosting
from hr_breaker.models.language import Language

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a professional resume translator specializing in {language_english} ({language_native}).

Your task: translate the HTML resume body from English to {language_english}.

CRITICAL RULES:
- Preserve ALL HTML tags, CSS classes, attributes, and structure EXACTLY as-is
- Translate ONLY the visible text content between HTML tags
- Output must be valid HTML with identical structure to the input
- Do NOT add, remove, or modify any HTML tags or CSS classes

TRANSLATION QUALITY:
- Use proper professional terminology for the {language_english} job market in this field
- Write naturally as a native {language_english} speaker would — NOT machine translation
- Maintain formal professional resume tone appropriate for {language_english}-language resumes
- Use industry-standard terms accepted in the {language_english}-speaking professional community
- Ensure phrasing sounds natural and human-written, not literal word-for-word translation

PRESERVE UNCHANGED:
- Company names (keep original)
- Product names and brand names
- Certifications and their abbreviations (AWS, PMP, etc.)
- Programming languages, frameworks, and tool names (Python, React, Docker, etc.)
- Numbers, dates, percentages, and metrics
- URLs, email addresses, and all links
- Personal name (keep as-is)

TECHNICAL TERMS:
- Keep widely recognized English technical terms that are standard in the {language_english}-speaking tech/professional community
- Use accepted {language_english} equivalents where they exist and are commonly used
- When in doubt, keep the English term — professionals in the field will understand it

JOB CONTEXT (use to pick correct field-specific terminology):
- Job field/industry context will be provided to help you choose the right professional vocabulary
"""


class TranslationResult(BaseModel):
    html: str = Field(description="Translated HTML body content")
    changes: list[str] = Field(
        default_factory=list,
        description="Notable translation decisions made",
    )


def get_translator_agent(language: Language) -> Agent:
    """Create translator agent for the given target language."""
    settings = get_settings()
    prompt = SYSTEM_PROMPT.format(
        language_english=language.english_name,
        language_native=language.native_name,
    )
    agent = Agent(
        f"google-gla:{settings.gemini_flash_model}",
        output_type=TranslationResult,
        system_prompt=prompt,
        model_settings=get_model_settings(),
    )

    @agent.system_prompt
    def add_current_date() -> str:
        return f"Today's date: {date.today().strftime('%B %Y')}"

    return agent


async def translate_resume(
    html: str,
    language: Language,
    job: JobPosting,
    feedback: str | None = None,
) -> TranslationResult:
    """Translate HTML resume body from English to target language.

    Args:
        html: English HTML body content
        language: Target language
        job: Job posting (for field-specific terminology context)
        feedback: Optional feedback from reviewer to improve translation
    """
    prompt = f"""Translate this resume HTML from English to {language.english_name}.

## Job Context (for terminology):
- Title: {job.title}
- Company: {job.company}
- Field keywords: {', '.join(job.keywords[:15])}

## English HTML to translate:
{html}
"""

    if feedback:
        prompt += f"""
## Reviewer Feedback (fix these issues):
{feedback}

IMPORTANT: Address all reviewer concerns while keeping the translation natural and professional.
"""

    prompt += """
Return JSON with:
- html: The translated HTML body (same structure, translated text)
- changes: List of notable translation decisions (e.g. "Kept 'Machine Learning' in English as standard term")
"""

    agent = get_translator_agent(language)
    result = await agent.run(prompt)
    logger.debug(
        "translate_resume: %d translation decisions",
        len(result.output.changes),
    )
    return result.output
