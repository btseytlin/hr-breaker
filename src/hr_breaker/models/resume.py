import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, computed_field, model_validator

from hr_breaker.models.resume_data import ResumeData


class ResumeSource(BaseModel):
    """Original resume as uploaded by user."""

    content: str  # Can be any text format (LaTeX, markdown, plain text, etc.)
    timestamp: datetime = Field(default_factory=datetime.now)
    first_name: str | None = None
    last_name: str | None = None
    instructions: str | None = None

    @model_validator(mode="before")
    @classmethod
    def handle_legacy_fields(cls, data: Any) -> Any:
        """Handle old cache files with renamed fields."""
        if isinstance(data, dict):
            if "latex" in data and "content" not in data:
                data["content"] = data.pop("latex")
            if "notes" in data and "instructions" not in data:
                data["instructions"] = data.pop("notes")
        return data

    # Legacy alias for backward compatibility
    @property
    def latex(self) -> str:
        return self.content

    @computed_field
    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.content.encode()).hexdigest()


class OptimizedResume(BaseModel):
    """Resume after optimization for a job posting."""

    data: ResumeData | None = None  # Used by LaTeX renderer (legacy)
    html: str | None = None  # Used by HTML renderer (LLM-generated body)
    iteration: int = 0
    changes: list[str] = Field(default_factory=list)
    source_checksum: str
    pdf_text: str | None = None
    pdf_bytes: bytes | None = None
    pdf_path: Path | None = None
