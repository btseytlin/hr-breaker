import logging
from datetime import date
from pathlib import Path

from pydantic import BaseModel
from pydantic_ai import Agent, BinaryContent

from hr_breaker.agents.combined_reviewer import pdf_to_image
from hr_breaker.config import get_model_settings, get_settings
from hr_breaker.filters.data_validator import validate_html
from hr_breaker.filters.keyword_matcher import check_keywords
from hr_breaker.models import (
    IterationContext,
    JobPosting,
    OptimizedResume,
    ResumeSource,
)
from hr_breaker.services.length_estimator import estimate_content_length
from hr_breaker.services.renderer import HTMLRenderer, RenderError
from hr_breaker.utils import extract_text_from_html

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).parent.parent.parent.parent / "templates"


def _load_resume_guide() -> str:
    """Load the HTML generation guide for the optimizer."""
    guide_path = TEMPLATE_DIR / "resume_guide.md"
    return guide_path.read_text()


OPTIMIZER_BASE = r"""
You are a resume optimization expert. Extract content from user's resume and create an optimized HTML resume for a job posting.

INPUT: The user's resume text (any format).

OUTPUT: Generate HTML for the <body> of a resume PDF. Do NOT include <html>, <head>, or <body> tags - only the content.

CONTENT RULES:
- When describing job experiences, show concrete results: focus on impact, not tasks.
- Include specific technologies within achievement descriptions.
- Feature keywords matching job requirements IF they exist in the original resume. You can add umbrella terms if relevant (e.g. if user was making transformer LLM models you can add "NLP")
- Prioritize and highlight experiences most relevant to the role
- If going over the one page limit: remove unrelated content to save space.
- Remove obvious skills (Excel, VS Code, Jupyter, GitHub, Jira) unless specifically required by the job or very relevant fot it.
- Exclude: location, language proficiency, age, hobbies unless required by job posting.
- Add a summary section highlighting the most relevant experiences.
- Try to preserve the original writing style if possible.
- Avoid leaving an empty space at the bottom of the page if you have useful content to fill.

{content_rules}

CONTENT BUDGET:
- Target: ~500 words, ~4000 characters (these are rough estimates, actual fit depends on formatting)
- The ONLY authoritative check is page_count from check_content_length

TOOLS:
- Use check_content_length(html) to verify your output fits 1 page BEFORE returning
  - Returns actual page_count from rendered PDF (authoritative)
  - Also returns character/word estimates (rough guidance only)
- Use preview_resume(html) to see rendered PDF preview - call at least once before returning
- If page_count > 1, trim content and check again
- Do not return until check_content_length confirms fits_one_page=true

OPTIONAL TOOLS (use when helpful):
- check_keywords_tool(html) - Returns missing job keywords ranked by TF-IDF importance. Use if unsure about keyword coverage.
- validate_structure(html) - Check HTML has proper headers/sections. Use after major structural changes.

LINKS:
- Preserve contacts info as in the original and never delete it
- Preserve URLs from the original resume (email, LinkedIn, GitHub, website, project links)
- Use full URLs (include https://) for all links

{resume_guide}
"""

OPTIMIZER_STRICT_RULES = """
ALLOWED:
- You CAN add related technologies plausible from context (e.g. Python user likely knows pip, venv; React user likely knows npm, webpack)
- General/umbrella terms inferable from context: "NLP" if they did text processing, "SQL" if they used databases
- Rephrasing metrics with same values: "1% - 10%" -> "1-10%"
- Reordering and emphasizing existing content
- Using content that is commented out and making it visible
- You CAN use <style> tags if you need custom styling beyond the provided classes 

STRICT RULES - NEVER VIOLATE:
- Only add specific technologies, products, or platforms not in original (e.g. "Amazon Bedrock", "LangChain", "Pinecone") they can be justified (user has experience with PostgreSQL -> maybe they are familiar with MySQL too) and ONLY IF there is no other way to increase fit 
- NEVER fabricate job titles, companies, degrees, certifications, or achievements
- NEVER invent metrics, numbers and achievements not in original
- DO NOT drop work experience or achievements (publications, patents, awards, etc.) unless they decrease fit
- Never use the em dash symbol, the word "delve" or other common markers of LLM-generated text.
- NEVER add <script> tags
- Do not cut critical content (like work experience, education, etc) if you can cut something else (like summary)
"""

OPTIMIZER_LENIENT_RULES = """
ALLOWED:
- You CAN add related technologies plausible from context (e.g. Python user likely knows pip, venv; React user likely knows npm, webpack)
- You CAN extrapolate skills from adjacent experience
- You CAN make light assumptions about the candidate
- General/umbrella terms inferable from context: "NLP" if they did text processing, "SQL" if they used databases
- Rephrasing metrics with same values: "1% - 10%" -> "1-10%"
- Reordering and emphasizing existing content
- Using content that is commented out and making it visible
- You CAN use <style> tags if you need custom styling beyond the provided classes 

STRICT RULES - NEVER VIOLATE:
- NEVER fabricate job titles, companies, degrees, certifications, or achievements
- NEVER invent metrics, numbers and achievements not in original
- Never use the em dash symbol, the word "delve" or other common markers of LLM-generated text.
- NEVER add <script> tags
- Do not cut critical content (like work experience, education, etc) if you can cut something else (like summary)
"""


class OptimizerResult(BaseModel):
    html: str
    changes: list[str]


def get_optimizer_agent(
    job: JobPosting, source: ResumeSource, no_shame: bool = False
) -> Agent:
    """Create optimizer agent with job/source context for filter tools."""
    settings = get_settings()
    resume_guide = _load_resume_guide()
    content_rules = OPTIMIZER_LENIENT_RULES if no_shame else OPTIMIZER_STRICT_RULES
    system_prompt = OPTIMIZER_BASE.format(
        content_rules=content_rules, resume_guide=resume_guide
    )
    agent = Agent(
        f"google-gla:{settings.gemini_pro_model}",
        output_type=OptimizerResult,
        system_prompt=system_prompt,
        model_settings=get_model_settings(),
    )

    @agent.system_prompt
    def add_current_date() -> str:
        return f"Today's date: {date.today().strftime('%B %Y')}"

    @agent.tool_plain
    def check_content_length(html: str) -> dict:
        """Check if HTML content fits one page by rendering PDF. Call before finalizing."""
        est = estimate_content_length(html)

        # Actually render PDF to check real page count
        try:
            renderer = HTMLRenderer()
            render_result = renderer.render(html)
            page_count = render_result.page_count
            fits_one_page = page_count == 1
        except RenderError as e:
            return {
                "fits_one_page": False,
                "error": f"Render failed: {e}",
                "estimates": {
                    "chars": est.chars,
                    "words": est.words,
                    "note": "Estimates only - fix render error first",
                },
            }

        result = {
            "fits_one_page": fits_one_page,
            "page_count": page_count,
            "estimates": {
                "chars": est.chars,
                "words": est.words,
                "limits": {
                    "chars": settings.resume_max_chars,
                    "words": settings.resume_max_words,
                },
                "note": "Character/word counts are rough estimates, page_count is authoritative",
            },
        }
        if not fits_one_page:
            result["suggestion"] = (
                f"Content spans {page_count} pages. Remove ~{est.overflow_words} words (estimate)"
            )
        logger.debug(
            "check_content_length called: %d pages, %d chars, %d words, fits=%s",
            page_count,
            est.chars,
            est.words,
            fits_one_page,
        )
        return result

    @agent.tool_plain
    def preview_resume(html: str) -> BinaryContent:
        """Render HTML to PDF and return preview image. Use to visually check layout."""
        logger.debug("preview_resume called")
        renderer = HTMLRenderer()
        result = renderer.render(html)
        image_bytes, _ = pdf_to_image(result.pdf_bytes)
        return BinaryContent(data=image_bytes, media_type="image/png")

    @agent.tool_plain
    def check_keywords_tool(html: str) -> dict:
        """Check keyword coverage vs job posting. Returns missing keywords ranked by TF-IDF importance."""
        resume_text = extract_text_from_html(html)
        result = check_keywords(resume_text, job)
        logger.debug(
            "check_keywords called: score=%.2f, missing=%d",
            result.score,
            len(result.missing_keywords),
        )
        return {
            "passed": result.passed,
            "score": round(result.score, 2),
            "missing_keywords": result.missing_keywords,
        }

    @agent.tool_plain
    def validate_structure(html: str) -> dict:
        """Check HTML structure - headers, sections, no scripts."""
        valid, issues = validate_html(html)
        logger.debug(
            "validate_structure called: valid=%s, issues=%d", valid, len(issues)
        )
        return {"valid": valid, "issues": issues}

    return agent


async def optimize_resume(
    source: ResumeSource,
    job: JobPosting,
    context: IterationContext,
    no_shame: bool = False,
) -> OptimizedResume:
    """Optimize resume for job posting."""
    prompt = f"""## Original Resume:
{context.original_resume}

## Job Posting:
Title: {job.title}
Company: {job.company}
Requirements: {', '.join(job.requirements)}
Keywords: {', '.join(job.keywords)}
Description: {job.description}
"""

    if context.last_attempt:
        estimate = estimate_content_length(context.last_attempt)
        prompt += f"""
## Last Attempt (Iteration {context.iteration}):
{context.last_attempt}

## Current Content Stats:
- Current: {estimate.chars} chars, {estimate.words} words

NOTE: This is a REFINEMENT iteration. Make the smallest possible change to pass failed filters.
Do NOT rewrite from scratch - modify the last attempt minimally.
"""

    if context.validation:
        prompt += f"""
## Filter Results:
{context.format_filter_results()}

IMPORTANT: Make MINIMAL changes to fix ONLY the failed filters.
- Start from the Last Attempt HTML above
- Change ONLY what's needed to pass the failed filter(s)
- Do NOT rewrite, rephrase, or restructure content that isn't causing failures
- Do NOT add new spelling mistakes, keywords, or stylistic changes if they were already added before
- Preserve everything that already works
"""

    prompt += """
Return JSON with:
- html: The HTML body content (no wrapper tags, just the content for <body>)
- changes: List of changes made (for tracking)

Output ONLY valid JSON. The html field should contain the raw HTML string.
"""

    agent = get_optimizer_agent(job, source, no_shame=no_shame)
    result = await agent.run(prompt)
    return OptimizedResume(
        html=result.output.html,
        iteration=context.iteration,
        changes=result.output.changes,
        source_checksum=source.checksum,
    )
