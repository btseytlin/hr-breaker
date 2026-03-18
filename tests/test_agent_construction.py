"""Smoke tests: actually construct every agent to catch config/wiring errors.

These tests do NOT call LLMs — they only verify that agent factory functions
run without AttributeError, ImportError, or similar wiring bugs.
"""

from hr_breaker.agents.job_parser import get_job_parser_agent
from hr_breaker.agents.optimizer import get_optimizer_agent
from hr_breaker.agents.combined_reviewer import get_combined_reviewer_agent
from hr_breaker.agents.hallucination_detector import get_hallucination_agent
from hr_breaker.agents.ai_generated_detector import get_ai_generated_agent
from hr_breaker.models import JobPosting, ResumeSource


def test_job_parser_agent():
    assert get_job_parser_agent() is not None


def test_optimizer_agent():
    job = JobPosting(
        title="Engineer", company="Acme",
        requirements=["Python"], keywords=["python"],
    )
    source = ResumeSource(content="Jane Doe\nPython dev")
    assert get_optimizer_agent(job, source) is not None


def test_combined_reviewer_agent():
    assert get_combined_reviewer_agent() is not None


def test_hallucination_agent():
    assert get_hallucination_agent() is not None


def test_hallucination_agent_no_shame():
    assert get_hallucination_agent(no_shame=True) is not None


def test_ai_generated_agent():
    assert get_ai_generated_agent() is not None
