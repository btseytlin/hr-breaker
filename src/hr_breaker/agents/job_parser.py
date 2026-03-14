from functools import lru_cache

from pydantic_ai import Agent

from hr_breaker.config import get_flash_model, get_model_settings
from hr_breaker.models import JobPosting
from hr_breaker.utils.optimization_telemetry import run_tracked_agent

SYSTEM_PROMPT = """You are a job posting parser. Extract structured information from job postings.

Extract:
- title: The job title
- company: Company name
- requirements: List of specific requirements (skills, experience, education)
- keywords: Technical keywords, tools, technologies mentioned
- language_code: ISO 639-1 code of the job posting's language (e.g. "en", "ru")
- description: Brief summary of the role

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
    result = await run_tracked_agent(agent, f"Parse this job posting:\n\n{text}", component="JobParser")
    job = result.output
    job.raw_text = text
    return job
