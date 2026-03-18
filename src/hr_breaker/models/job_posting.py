from pydantic import BaseModel, Field


class JobPosting(BaseModel):
    """Structured job posting data."""

    title: str
    company: str
    requirements: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    language_code: str = "en"
    description: str = ""
    raw_text: str = ""
