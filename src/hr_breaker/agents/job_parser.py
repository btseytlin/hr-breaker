from functools import lru_cache

from pydantic_ai import Agent

from hr_breaker.config import get_flash_model, get_model_settings
from hr_breaker.models import JobPosting
from hr_breaker.utils.retry import run_with_retry

SYSTEM_PROMPT = """You are a job posting parser. Extract structured information from job postings.

Extract:
- title: The job title
- company: Company name
- requirements: List of specific requirements (skills, experience, education)
- keywords: Technical keywords, tools, technologies mentioned
- description: Brief summary of the role
- language_code: ISO 639-1 code of the posting's primary language (e.g. "en", "ru", "de", "fr")

Be thorough in extracting keywords - include all technologies, tools, frameworks, methodologies mentioned.
"""


@lru_cache
def get_job_parser_agent() -> Agent:
    return Agent(
        get_flash_model(),
        output_type=JobPosting,
        system_prompt=SYSTEM_PROMPT,
        model_settings=get_model_settings(),
    )


async def parse_job_posting(text: str) -> JobPosting:
    """Parse job posting text into structured data."""
    agent = get_job_parser_agent()
    result = await run_with_retry(agent.run, f"Parse this job posting:\n\n{text}")
    job = result.output
    job.raw_text = text
    return job
