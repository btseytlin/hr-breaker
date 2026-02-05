import logging
from datetime import date

from pydantic_ai import Agent

from hr_breaker.config import get_model_settings, get_settings
from hr_breaker.models import CoverLetter, JobPosting, ResumeSource

logger = logging.getLogger(__name__)

COVER_LETTER_SYSTEM_PROMPT = """
You are an expert career coach and professional copywriter.
Your task is to write a compelling, professional cover letter for a candidate based on their resume and a specific job posting.

GUIDELINES:
- Tone: Professional, enthusiastic, confident, yet authentic. Avoid overly stiff or generated-sounding language (e.g., "I am writing to express my eager interest...").
- Structure:
    - **Header**: Standard business letter format (Candidate Name, Date, Company Name, etc.).
    - **Opening**: strong hook connecting the candidate's background to the company/role.
    - **Body**: 2-3 paragraphs highlighting specific achievements from the resume that directly map to the job requirements. Use metrics and specific outcomes where possible.
    - **Closing**: Reiterate interest and Call to Action (interview request).
- Customization: tailored specifically to the company and role. Mention the company name naturally.
- Length: Concise, about 300-400 words.

INPUT:
- Candidate Resume (text)
- Job Posting (title, company, description)

OUTPUT:
- Return a JSON object with the 'markdown' field containing the full text of the cover letter in Markdown format.
- Do NOT include placeholders like [Your Name] if you can find the name in the resume. If not found, use a generic placeholder or "Hiring Manager".
"""


def get_cover_letter_agent() -> Agent:
    settings = get_settings()
    return Agent(
        f"google-gla:{settings.gemini_pro_model}",
        output_type=CoverLetter,
        system_prompt=COVER_LETTER_SYSTEM_PROMPT,
        model_settings=get_model_settings(),
    )


async def generate_cover_letter(
    source: ResumeSource,
    job: JobPosting,
    user_notes: str | None = None,
) -> CoverLetter:
    """Generate a cover letter based on resume and job posting."""
    agent = get_cover_letter_agent()

    prompt = f"""
    ## Candidate Resume
    {source.content}
    
    ## Job Posting
    Title: {job.title}
    Company: {job.company}
    Description:
    {job.description}
    
    Current Date: {date.today().strftime('%B %d, %Y')}
    """

    if user_notes:
        prompt += f"""
        ## User Instructions
        The user has provided the following specific instructions/notes. 
        Please PRIORITIZE these instructions when writing the cover letter:
        "{user_notes}"
        """

    prompt += """
    Write a tailored cover letter.
    """

    result = await agent.run(prompt)
    cover_letter = result.output
    # Enrich with metadata if missing (though model should try to fill it)
    if not cover_letter.job_company:
        cover_letter.job_company = job.company
    if not cover_letter.job_title:
        cover_letter.job_title = job.title
        
    return cover_letter
