from datetime import datetime
from pathlib import Path
from pydantic import BaseModel, Field


class FilterResult(BaseModel):
    """Result from a single filter evaluation."""

    filter_name: str
    passed: bool
    score: float = Field(ge=0.0, le=1.0)
    threshold: float = Field(ge=0.0, le=1.0, default=0.5)
    skipped: bool = Field(default=False, description="Filter was not applicable and skipped")
    issues: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    feedback: str = Field(default="", description="Free-form feedback for optimizer")


class ValidationResult(BaseModel):
    """Aggregate result from all filters."""

    results: list[FilterResult] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results if not r.skipped)

    @property
    def feedback_text(self) -> str:
        lines = []
        for r in self.results:
            if r.skipped or r.passed:
                continue
                lines.append(f"[{r.filter_name}] Score: {r.score:.2f} (threshold: {r.threshold:.2f})")
                for issue in r.issues:
                    lines.append(f"  - Issue: {issue}")
                for suggestion in r.suggestions:
                    lines.append(f"  - Suggestion: {suggestion}")
                if r.feedback:
                    lines.append(f"  - Feedback: {r.feedback}")
        return "\n".join(lines)


class GeneratedPDF(BaseModel):
    """Record of a generated PDF."""

    path: Path
    source_checksum: str
    company: str
    job_title: str
    timestamp: datetime = Field(default_factory=datetime.now)
    first_name: str | None = None
    last_name: str | None = None
    run_id: str | None = None
