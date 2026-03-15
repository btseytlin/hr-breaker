import asyncio
import logging

from pydantic import BaseModel
from pydantic_ai import Agent

from hr_breaker.config import (
    get_flash_model,
    get_model_settings,
    get_settings,
    has_api_key_for_model,
    required_api_key_env_for_model,
 )
from hr_breaker.models.profile import (
    DocumentExtraction,
    EducationEntry,
    ExperienceEntry,
    PersonalInfo,
    ProjectEntry,
    SkillsEntry,
)
from hr_breaker.utils.retry import run_with_retry

logger = logging.getLogger(__name__)


class _ExperienceResult(BaseModel):
    experience: list[ExperienceEntry]


class _EducationResult(BaseModel):
    education: list[EducationEntry]


class _ProjectsResult(BaseModel):
    projects: list[ProjectEntry]


class _PublicationsResult(BaseModel):
    publications: list[str]


class _SummaryResult(BaseModel):
    summary: list[str]


_SUMMARY_PROMPT = """Extract career summaries from the document.
Include: professional headlines, "About" / LinkedIn summaries, career objective statements.
Each distinct paragraph = one list entry. Return [] if none found.
Do not invent anything not explicitly stated."""

_EXPERIENCE_PROMPT = """Extract work experience entries from the document.
Each distinct role = one entry: employer, title, start date, end date, bullet points.
Preserve exact dates, company names, and metrics as written.
Return [] if none found. Do not invent anything."""

_EDUCATION_PROMPT = """Extract education entries from the document.
Each degree or program = one entry: institution, degree, field, start/end dates, notes (GPA, honors, coursework).
Return [] if none found. Do not invent anything."""

_SKILLS_PROMPT = """Extract skills from the document into four categories:
- technical: programming languages, frameworks, tools, cloud platforms, databases
- languages: spoken/written human languages (English, Russian, etc.)
- certifications: named certifications with year if present
- awards: prizes, honors, recognition
Return empty lists for absent categories. Do not invent anything."""

_PROJECTS_PROMPT = """Extract side projects, open-source work, and research projects.
Each project: name, short description, URL only if explicitly written in the document, key bullet points.
Return [] if none found.
STRICT: Do NOT construct or infer URLs — only copy URLs that appear verbatim in the document text."""

_PUBLICATIONS_PROMPT = """Extract publications, papers, patents, and articles.
One free-form string per item: authors, title, venue, year — and include the DOI at the end in parentheses if present in the document, e.g. "(DOI: 10.xxxx/xxxx)".
Return [] if none found. Do not invent anything."""

_CONTACT_PROMPT = """Extract personal contact information from the document.
- name: full name of the candidate as written (first + last)
- email: email address
- phone: phone number (any format)
- linkedin: LinkedIn profile URL (full URL or linkedin.com/in/...)
- github: GitHub profile URL or username (full URL or github.com/...)
- website: personal website or portfolio URL
- other_links: ONLY URLs that are explicitly written in the document verbatim — do NOT construct or infer URLs from names or usernames

STRICT RULES:
- Return null for any field not explicitly present in the document
- NEVER construct a URL by combining a username with a domain (e.g. do NOT write github.com/user/project unless that exact URL appears in the text)
- other_links must only contain URLs copied verbatim from the document"""


def _strip_hallucinated_urls(info: PersonalInfo, source_text: str) -> PersonalInfo:
    """Remove any URL fields not verbatim in source_text to prevent hallucinated links."""
    updates: dict = {}
    for field in ("linkedin", "github", "website"):
        url = getattr(info, field)
        if url and url not in source_text:
            logger.warning("Stripping hallucinated URL %s=%r (not in source)", field, url)
            updates[field] = None
    valid_other = [u for u in info.other_links if u in source_text]
    if len(valid_other) != len(info.other_links):
        stripped = set(info.other_links) - set(valid_other)
        logger.warning("Stripping hallucinated other_links: %s", stripped)
        updates["other_links"] = valid_other
    if updates:
        return info.model_copy(update=updates)
    return info

def _ensure_extraction_credentials(model_name: str) -> None:
    required_env = required_api_key_env_for_model(model_name)
    if required_env and not has_api_key_for_model(model_name):
        raise RuntimeError(
            f"Missing API key for extraction model '{model_name}'. Set {required_env} before running profile extraction."
        )



async def _run_category(agent: Agent, doc_text: str, label: str):
    try:
        result = await run_with_retry(agent.run, doc_text)

        return result.output
    except Exception as exc:
        logger.warning("Extraction category '%s' failed: %s", label, exc)
        return None


async def extract_document(content_text: str) -> DocumentExtraction:
    """Extract structured facts via 6 parallel focused calls. Partial failures use empty defaults."""
    model_name = get_settings().flash_model
    _ensure_extraction_credentials(model_name)
    model = get_flash_model()
    settings = get_model_settings()

    agents = [
        Agent(model, output_type=PersonalInfo, system_prompt=_CONTACT_PROMPT, model_settings=settings),
        Agent(model, output_type=_SummaryResult, system_prompt=_SUMMARY_PROMPT, model_settings=settings),
        Agent(model, output_type=_ExperienceResult, system_prompt=_EXPERIENCE_PROMPT, model_settings=settings),
        Agent(model, output_type=_EducationResult, system_prompt=_EDUCATION_PROMPT, model_settings=settings),
        Agent(model, output_type=SkillsEntry, system_prompt=_SKILLS_PROMPT, model_settings=settings),
        Agent(model, output_type=_ProjectsResult, system_prompt=_PROJECTS_PROMPT, model_settings=settings),
        Agent(model, output_type=_PublicationsResult, system_prompt=_PUBLICATIONS_PROMPT, model_settings=settings),
    ]
    labels = ["contact", "summary", "experience", "education", "skills", "projects", "publications"]

    results = await asyncio.gather(*[
        _run_category(agent, content_text, label)
        for agent, label in zip(agents, labels)
    ])

    contact_r, summary_r, experience_r, education_r, skills_r, projects_r, publications_r = results
    personal_info = contact_r if contact_r else PersonalInfo()
    personal_info = _strip_hallucinated_urls(personal_info, content_text)
    return DocumentExtraction(
        personal_info=personal_info,
        summary=summary_r.summary if summary_r else [],
        experience=experience_r.experience if experience_r else [],
        education=education_r.education if education_r else [],
        skills=skills_r if skills_r else SkillsEntry(),
        projects=projects_r.projects if projects_r else [],
        publications=publications_r.publications if publications_r else [],
    )
