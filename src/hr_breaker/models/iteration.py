from pydantic import BaseModel

from hr_breaker.models.feedback import ValidationResult


class IterationContext(BaseModel):
    """Context passed to optimizer on each iteration."""

    iteration: int
    original_resume: str  # Original source LaTeX/text
    last_attempt: str | None = None  # Previous iteration's LaTeX output
    validation: ValidationResult | None = None  # Full filter results with scores

    def format_filter_results(self) -> str:
        """Format filter results for the optimizer prompt."""
        if not self.validation:
            return ""

        lines = []
        for r in self.validation.results:
            if r.skipped:
                continue
            status = "PASSED" if r.passed else "FAILED"
            lines.append(f"[{r.filter_name}] Score: {r.score:.2f}/{r.threshold:.2f} ({status})")
            if r.issues:
                for issue in r.issues:
                    lines.append(f"  Issue: {issue}")
            if r.suggestions:
                for suggestion in r.suggestions:
                    lines.append(f"  Suggestion: {suggestion}")
        return "\n".join(lines)
