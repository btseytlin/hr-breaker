from abc import ABC, abstractmethod

from hr_breaker.models import FilterResult, JobPosting, OptimizedResume, ResumeSource


class BaseFilter(ABC):
    """Abstract base class for resume filters."""

    name: str = "BaseFilter"
    priority: int = 50  # Lower runs first, 100 = run last (after all others pass)
    threshold: float = 0.5  # Score threshold for passing

    def __init__(self, no_shame: bool = False):
        self.no_shame = no_shame

    @abstractmethod
    async def evaluate(
        self,
        optimized: OptimizedResume,
        job: JobPosting,
        source: ResumeSource,
    ) -> FilterResult:
        """Evaluate the optimized resume against the job posting."""
        pass
