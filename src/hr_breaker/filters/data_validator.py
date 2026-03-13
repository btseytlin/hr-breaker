"""Validates OptimizedResume completeness before rendering."""

import re

from hr_breaker.filters.base import BaseFilter
from hr_breaker.filters.registry import FilterRegistry
from hr_breaker.models import FilterResult, JobPosting, OptimizedResume, ResumeSource
from hr_breaker.models.language import Language


def validate_html(html: str) -> tuple[bool, list[str]]:
    """Check HTML body content for validity."""
    issues = []

    # Must have header with name
    if not re.search(r'<header[^>]*class="header"', html):
        issues.append("Missing header element with class='header'")
    elif not re.search(r'<h1[^>]*class="name"[^>]*>', html):
        issues.append("Missing name (h1 with class='name') in header")

    # Must have at least one section
    if not re.search(r'<section[^>]*class="section"', html):
        issues.append("Resume has no content sections")

    # No script tags allowed (style tags are OK)
    if re.search(r'<script', html, re.IGNORECASE):
        issues.append("Script tags are not allowed")

    return len(issues) == 0, issues


def validate_resume_data(optimized: OptimizedResume) -> tuple[bool, list[str]]:
    """Check ResumeData for completeness and validity."""
    issues = []
    data = optimized.data

    if data is None:
        issues.append("No ResumeData provided")
        return False, issues

    # Contact info is required
    if not data.contact.name:
        issues.append("Missing contact name")
    if not data.contact.email:
        issues.append("Missing contact email")

    # Must have some content
    has_content = any([
        data.summary,
        data.experience,
        data.education,
        data.skills,
        data.projects,
        data.certifications,
        data.publications,
    ])
    if not has_content:
        issues.append("Resume has no content sections")

    # Experience entries should have required fields
    for i, exp in enumerate(data.experience):
        if not exp.company:
            issues.append(f"Experience #{i+1}: missing company")
        if not exp.title:
            issues.append(f"Experience #{i+1}: missing title")
        if not exp.start_date:
            issues.append(f"Experience #{i+1}: missing start_date")

    # Education entries should have required fields
    for i, edu in enumerate(data.education):
        if not edu.institution:
            issues.append(f"Education #{i+1}: missing institution")
        if not edu.degree:
            issues.append(f"Education #{i+1}: missing degree")

    return len(issues) == 0, issues


@FilterRegistry.register
class DataValidator(BaseFilter):
    """Validates OptimizedResume structure before rendering. Runs first."""

    name = "DataValidator"
    priority = 1  # Run first
    threshold = 1.0  # Must pass fully

    async def evaluate(
        self,
        optimized: OptimizedResume,
        job: JobPosting,
        source: ResumeSource,
        language: Language | None = None,
        source_language: Language | None = None,
    ) -> FilterResult:
        # Choose validation based on which field is present
        if optimized.html is not None:
            valid, issues = validate_html(optimized.html)
        elif optimized.data is not None:
            valid, issues = validate_resume_data(optimized)
        else:
            valid, issues = False, ["No resume content (neither html nor data)"]

        score = 1.0 if valid else 0.0

        return FilterResult(
            filter_name=self.name,
            passed=score >= self.threshold,
            score=score,
            threshold=self.threshold,
            issues=issues,
            suggestions=(
                ["Fix missing required fields/elements in resume"]
                if issues
                else []
            ),
        )
