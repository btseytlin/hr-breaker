from datetime import date

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from hr_breaker.config import get_model_settings, get_settings
from hr_breaker.models import FilterResult, OptimizedResume, ResumeSource


class HallucinationResult(BaseModel):
    no_hallucination_score: float = Field(
        ge=0.0,
        le=1.0,
        description="Score from 0 to 1 where 1.0 = no fabrications, 0.0 = severe fabrications",
    )
    concerns: list[str] = Field(
        default_factory=list,
        description="List of potential concerns (may include minor acceptable additions)",
    )
    reasoning: str = Field(description="Brief explanation of the score")


STRICT_PROMPT = """You are a resume verification specialist. 
Compare an ORIGINAL resume with an OPTIMIZED version and return a no_hallucination_score from 0.0 to 1.0.

SCORING GUIDE:
- 1.0: Perfect - all content traceable to original, only rephrasing/restructuring
- 0.9-0.99: Minor acceptable additions (related tech inference, umbrella terms)
- 0.8-0.9: Light assumptions that are reasonable but noticeable
- 0.7-0.8: Questionable additions - somewhat plausible but stretching
- 0.5-0.69: Significant fabrications - claims that may not be true
- 0.0-0.49: Severe fabrications - fake jobs, degrees, major false claims

ACCEPTABLE (score 0.8+):
- Related technology inference: MySQL user -> PostgreSQL, React user -> Vue.js, Python user -> common Python libs
- General/umbrella terms: "NLP" for text work, "SQL" for database users, "CI/CD" for DevOps work
- Rephrasing metrics: "1% - 10%" -> "1-10%", "$10k" -> "$10,000"
- Summary sections synthesizing existing experience
- Reordering, restructuring, emphasizing existing content
- Commented-out content in original (LaTeX %, HTML <!-- -->) included in optimized

LIGHT ASSUMPTIONS (score 0.7-0.8):
- Adding specific and less related technologies, but still plausible from context
- Inferring common tools from workflow descriptions
- Reasonable skill extrapolations

SERIOUS FABRICATIONS (score below 0.5):
- Fabricated job titles, companies, or employment dates
- Invented degrees, certifications, or institutions
- Made-up metrics with specific numbers not in original
- Fake achievements, publications, or awards
- Completely unrelated technologies

"""

LENIENT_PROMPT = """You are a resume verification specialist. 
Compare an ORIGINAL resume with an OPTIMIZED version and return a no_hallucination_score from 0.0 to 1.0.

SCORING GUIDE:
- 1.0: All content directly traceable to original
- 0.8-0.99: Aggressive skill extrapolations that are plausible from context
- 0.6-0.79: Significant embellishment of achievements, creative reframing
- 0.5-0.59: Very aggressive stretching but still plausible
- 0.0-0.49: Blatant fabrications - fake jobs, degrees, made-up credentials

ACCEPTABLE (score 0.7+):
- Aggressive technology extrapolation: Python user -> any Python library, web dev -> full stack
- Adding plausible tools from job context even if not explicitly stated
- Creative reframing of responsibilities to match job requirements
- Inferring leadership/mentoring from senior roles
- Adding industry-standard practices plausible for their role

ACCEPTABLE (score 0.6+):
- Adding technologies commonly paired with stated ones
- Extrapolating scope/scale of projects

BLOCK (score below 0.5):
- Fabricated job titles, companies, or employment dates
- Invented degrees, certifications, or institutions
- Made-up awards, publications, or patents
- Completely fictional projects or achievements
- Technologies with zero connection to stated experience
- Made up specific metrics

"""


def get_hallucination_agent(no_shame: bool = False) -> Agent:
    settings = get_settings()
    prompt = LENIENT_PROMPT if no_shame else STRICT_PROMPT
    agent = Agent(
        f"google-gla:{settings.gemini_pro_model}",
        output_type=HallucinationResult,
        system_prompt=prompt,
        model_settings=get_model_settings(),
    )

    @agent.system_prompt
    def add_current_date() -> str:
        return f"Today's date: {date.today().strftime('%B %Y')}"

    return agent


async def detect_hallucinations(
    optimized: OptimizedResume,
    source: ResumeSource,
    no_shame: bool = False,
) -> FilterResult:
    """Detect hallucinations in optimized resume vs original."""
    # Use html or data depending on what's available
    if optimized.html:
        optimized_content = optimized.html
    elif optimized.data:
        optimized_content = optimized.data.model_dump_json(indent=2)
    else:
        optimized_content = "(no content)"

    prompt = f"""Compare these two resumes and score the optimized version for hallucinations.

=== ORIGINAL RESUME (source of truth, may include commented-out content which is valid) ===
{source.content}

=== OPTIMIZED RESUME (check for fabrication) ===
{optimized_content}

=== END ===

Return a no_hallucination_score (0.0-1.0) based on how faithful the optimized version is to the original.
List any concerns but remember: light assumptions about related technologies are acceptable."""

    threshold = 0.6 if no_shame else 0.9
    agent = get_hallucination_agent(no_shame=no_shame)
    result = await agent.run(prompt)
    r = result.output

    issues = []
    suggestions = []

    if r.concerns:
        issues.append(f"Concerns: {', '.join(r.concerns)}")
    if r.no_hallucination_score < threshold:
        suggestions.append(
            f"Score {r.no_hallucination_score:.2f} below {threshold} threshold. {r.reasoning}"
        )

    return FilterResult(
        filter_name="HallucinationChecker",
        passed=r.no_hallucination_score >= threshold,
        score=r.no_hallucination_score,
        threshold=threshold,
        issues=issues,
        suggestions=suggestions,
    )
