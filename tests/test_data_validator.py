"""Tests for DataValidator filter."""

import pytest

from hr_breaker.filters import DataValidator, FilterRegistry
from hr_breaker.filters.data_validator import validate_html, validate_resume_data
from hr_breaker.models import JobPosting, OptimizedResume, ResumeSource
from hr_breaker.models.resume_data import (
    ResumeData,
    ContactInfo,
    Experience,
    Education,
)


@pytest.fixture
def source_resume():
    return ResumeSource(content="Original resume content")


@pytest.fixture
def job_posting():
    return JobPosting(
        title="Engineer",
        company="Acme",
        requirements=["Python"],
        keywords=["python"],
    )


@pytest.fixture
def valid_resume_data():
    return ResumeData(
        contact=ContactInfo(name="John Doe", email="john@example.com"),
        summary="Experienced engineer",
        experience=[
            Experience(
                company="Acme",
                title="Engineer",
                start_date="2020",
                bullets=["Did things"],
            )
        ],
        skills=["Python"],
    )


@pytest.fixture
def optimized_resume(source_resume, valid_resume_data):
    return OptimizedResume(
        data=valid_resume_data,
        source_checksum=source_resume.checksum,
    )


# --- Registration Tests ---


def test_data_validator_registered():
    assert "DataValidator" in FilterRegistry.names()


def test_data_validator_priority():
    validator = DataValidator()
    assert validator.priority == 1  # Runs first


def test_data_validator_threshold():
    validator = DataValidator()
    assert validator.threshold == 1.0



# --- validate_resume_data Function Tests ---


def test_validate_valid_data(valid_resume_data, source_resume):
    optimized = OptimizedResume(
        data=valid_resume_data,
        source_checksum=source_resume.checksum,
    )
    valid, issues = validate_resume_data(optimized)
    assert valid
    assert issues == []


def test_validate_missing_name(source_resume):
    data = ResumeData(
        contact=ContactInfo(name="", email="test@example.com"),
        skills=["Python"],
    )
    optimized = OptimizedResume(data=data, source_checksum=source_resume.checksum)
    valid, issues = validate_resume_data(optimized)
    assert not valid
    assert "Missing contact name" in issues


def test_validate_missing_email(source_resume):
    data = ResumeData(
        contact=ContactInfo(name="John Doe", email=None),
        skills=["Python"],
    )
    optimized = OptimizedResume(data=data, source_checksum=source_resume.checksum)
    valid, issues = validate_resume_data(optimized)
    assert not valid
    assert "Missing contact email" in issues


def test_validate_no_content(source_resume):
    data = ResumeData(
        contact=ContactInfo(name="John Doe", email="john@example.com"),
        # All content sections empty
        summary=None,
        experience=[],
        education=[],
        skills=[],
        projects=[],
        certifications=[],
        publications=[],
    )
    optimized = OptimizedResume(data=data, source_checksum=source_resume.checksum)
    valid, issues = validate_resume_data(optimized)
    assert not valid
    assert "Resume has no content sections" in issues


def test_validate_experience_missing_company(source_resume):
    data = ResumeData(
        contact=ContactInfo(name="John Doe", email="john@example.com"),
        experience=[
            Experience(
                company="",  # Missing
                title="Engineer",
                start_date="2020",
            )
        ],
    )
    optimized = OptimizedResume(data=data, source_checksum=source_resume.checksum)
    valid, issues = validate_resume_data(optimized)
    assert not valid
    assert any("Experience #1: missing company" in i for i in issues)


def test_validate_experience_missing_title(source_resume):
    data = ResumeData(
        contact=ContactInfo(name="John Doe", email="john@example.com"),
        experience=[
            Experience(
                company="Acme",
                title="",  # Missing
                start_date="2020",
            )
        ],
    )
    optimized = OptimizedResume(data=data, source_checksum=source_resume.checksum)
    valid, issues = validate_resume_data(optimized)
    assert not valid
    assert any("Experience #1: missing title" in i for i in issues)


def test_validate_experience_missing_start_date(source_resume):
    data = ResumeData(
        contact=ContactInfo(name="John Doe", email="john@example.com"),
        experience=[
            Experience(
                company="Acme",
                title="Engineer",
                start_date="",  # Missing
            )
        ],
    )
    optimized = OptimizedResume(data=data, source_checksum=source_resume.checksum)
    valid, issues = validate_resume_data(optimized)
    assert not valid
    assert any("Experience #1: missing start_date" in i for i in issues)


def test_validate_education_missing_institution(source_resume):
    data = ResumeData(
        contact=ContactInfo(name="John Doe", email="john@example.com"),
        education=[
            Education(
                institution="",  # Missing
                degree="BS",
            )
        ],
    )
    optimized = OptimizedResume(data=data, source_checksum=source_resume.checksum)
    valid, issues = validate_resume_data(optimized)
    assert not valid
    assert any("Education #1: missing institution" in i for i in issues)


def test_validate_education_missing_degree(source_resume):
    data = ResumeData(
        contact=ContactInfo(name="John Doe", email="john@example.com"),
        education=[
            Education(
                institution="MIT",
                degree="",  # Missing
            )
        ],
    )
    optimized = OptimizedResume(data=data, source_checksum=source_resume.checksum)
    valid, issues = validate_resume_data(optimized)
    assert not valid
    assert any("Education #1: missing degree" in i for i in issues)


def test_validate_multiple_issues(source_resume):
    data = ResumeData(
        contact=ContactInfo(name="", email=""),  # Both missing
        experience=[
            Experience(company="", title="", start_date="")  # All missing
        ],
    )
    optimized = OptimizedResume(data=data, source_checksum=source_resume.checksum)
    valid, issues = validate_resume_data(optimized)
    assert not valid
    assert len(issues) >= 4  # name, email, company, title, start_date


def test_validate_skills_only_is_valid(source_resume):
    """Skills alone count as content."""
    data = ResumeData(
        contact=ContactInfo(name="John Doe", email="john@example.com"),
        skills=["Python", "SQL"],
    )
    optimized = OptimizedResume(data=data, source_checksum=source_resume.checksum)
    valid, issues = validate_resume_data(optimized)
    assert valid
    assert issues == []


def test_validate_summary_only_is_valid(source_resume):
    """Summary alone counts as content."""
    data = ResumeData(
        contact=ContactInfo(name="John Doe", email="john@example.com"),
        summary="Experienced software engineer.",
    )
    optimized = OptimizedResume(data=data, source_checksum=source_resume.checksum)
    valid, issues = validate_resume_data(optimized)
    assert valid
    assert issues == []


# --- Filter evaluate() Tests ---


@pytest.mark.asyncio
async def test_data_validator_passes(source_resume, job_posting, optimized_resume):
    validator = DataValidator()
    result = await validator.evaluate(optimized_resume, job_posting, source_resume)

    assert result.passed
    assert result.score == 1.0
    assert result.issues == []


@pytest.mark.asyncio
async def test_data_validator_fails(source_resume, job_posting):
    data = ResumeData(
        contact=ContactInfo(name="", email=""),
    )
    optimized = OptimizedResume(data=data, source_checksum=source_resume.checksum)

    validator = DataValidator()
    result = await validator.evaluate(optimized, job_posting, source_resume)

    assert not result.passed
    assert result.score == 0.0
    assert len(result.issues) > 0
    assert "Fix missing required fields" in result.suggestions[0]
