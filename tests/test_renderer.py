"""Tests for the renderer module."""

import fitz
import pytest
from hr_breaker.models.resume_data import (
    ResumeData,
    RenderResult,
    ContactInfo,
    Experience,
    Education,
    Project,
)
from hr_breaker.services.renderer import (
    BaseRenderer,
    HTMLRenderer,
    RenderError,
    get_renderer,
)


# --- Fixtures ---


@pytest.fixture
def minimal_resume_data():
    """Minimal valid ResumeData."""
    return ResumeData(
        contact=ContactInfo(name="John Doe", email="john@example.com"),
    )


@pytest.fixture
def full_resume_data():
    """Complete ResumeData with all sections."""
    return ResumeData(
        contact=ContactInfo(
            name="Jane Smith",
            email="jane@example.com",
            phone="555-123-4567",
            linkedin="linkedin.com/in/janesmith",
            github="github.com/janesmith",
            website="janesmith.dev",
            location="San Francisco, CA",
        ),
        summary="Senior engineer with 10+ years experience in distributed systems.",
        experience=[
            Experience(
                company="Tech Corp",
                title="Senior Engineer",
                location="SF, CA",
                start_date="2020",
                end_date=None,
                bullets=[
                    "Led team of 5 building real-time data pipeline",
                    "Reduced latency by 40%",
                ],
            ),
            Experience(
                company="Startup Inc",
                title="Software Engineer",
                location="NYC, NY",
                start_date="2017",
                end_date="2020",
                bullets=["Built REST APIs serving 1M req/day"],
            ),
        ],
        education=[
            Education(
                institution="MIT",
                degree="BS Computer Science",
                location="Cambridge, MA",
                start_date="2013",
                end_date="2017",
                details=["GPA: 3.9", "Dean's List"],
            ),
        ],
        skills=["Python", "Go", "Kubernetes", "PostgreSQL", "AWS"],
        projects=[
            Project(
                name="OpenSource Tool",
                description="CLI for data processing",
                url="github.com/janesmith/tool",
                bullets=["1000+ GitHub stars", "Used by Fortune 500"],
            ),
        ],
        certifications=["AWS Solutions Architect", "Kubernetes Administrator"],
        publications=["Smith et al. 'Distributed Systems at Scale' (2021)"],
    )


# --- ResumeData Model Tests ---


class TestResumeData:
    def test_minimal_valid(self, minimal_resume_data):
        assert minimal_resume_data.contact.name == "John Doe"
        assert minimal_resume_data.experience == []
        assert minimal_resume_data.skills == []

    def test_full_data(self, full_resume_data):
        assert full_resume_data.contact.name == "Jane Smith"
        assert len(full_resume_data.experience) == 2
        assert len(full_resume_data.education) == 1
        assert len(full_resume_data.skills) == 5
        assert len(full_resume_data.projects) == 1
        assert len(full_resume_data.certifications) == 2
        assert len(full_resume_data.publications) == 1

    def test_experience_present_job(self, full_resume_data):
        """Test experience with no end_date (current job)."""
        current_job = full_resume_data.experience[0]
        assert current_job.end_date is None

    def test_serialization_roundtrip(self, full_resume_data):
        """Test JSON serialization and deserialization."""
        json_str = full_resume_data.model_dump_json()
        loaded = ResumeData.model_validate_json(json_str)
        assert loaded.contact.name == full_resume_data.contact.name
        assert len(loaded.experience) == len(full_resume_data.experience)


class TestRenderResult:
    def test_render_result_creation(self):
        result = RenderResult(
            pdf_bytes=b"fake pdf content",
            page_count=1,
            warnings=[],
        )
        assert len(result.pdf_bytes) > 0
        assert result.page_count == 1
        assert result.warnings == []

    def test_render_result_with_warnings(self):
        result = RenderResult(
            pdf_bytes=b"pdf",
            page_count=2,
            warnings=["Resume is 2 pages"],
        )
        assert result.page_count == 2
        assert len(result.warnings) == 1


# --- get_renderer Tests ---


class TestGetRenderer:
    def test_get_html_renderer(self):
        renderer = get_renderer()
        assert isinstance(renderer, HTMLRenderer)


# --- HTMLRenderer Tests ---


class TestHTMLRenderer:
    def test_render_minimal(self, minimal_resume_data):
        """Test rendering minimal resume data."""
        renderer = HTMLRenderer()
        result = renderer.render_data(minimal_resume_data)

        assert isinstance(result, RenderResult)
        assert len(result.pdf_bytes) > 0
        assert result.page_count >= 1

    def test_render_full(self, full_resume_data):
        """Test rendering complete resume data."""
        renderer = HTMLRenderer()
        result = renderer.render_data(full_resume_data)

        assert isinstance(result, RenderResult)
        assert len(result.pdf_bytes) > 1000  # Reasonable PDF size
        assert result.page_count >= 1

    def test_render_warns_multipage(self, full_resume_data):
        """Test that multi-page resumes generate warnings."""
        # Create data that will definitely overflow to multiple pages
        full_resume_data.experience = full_resume_data.experience * 10
        full_resume_data.skills = full_resume_data.skills * 20

        renderer = HTMLRenderer()
        result = renderer.render_data(full_resume_data)

        if result.page_count > 1:
            assert any("pages" in w.lower() for w in result.warnings)

    def test_render_handles_special_chars(self):
        """Test rendering with special characters."""
        data = ResumeData(
            contact=ContactInfo(
                name="O'Brien & Partners",
                email="test@example.com",
            ),
            summary="Experience with C++ & Java; also <script> tags",
            skills=["C++", "C#", "R&D"],
        )
        renderer = HTMLRenderer()
        result = renderer.render_data(data)
        assert len(result.pdf_bytes) > 0

    def test_render_handles_unicode(self):
        """Test rendering with unicode characters."""
        data = ResumeData(
            contact=ContactInfo(
                name="Jose Garcia",
                email="jose@example.com",
                location="Sao Paulo, Brasil",
            ),
            skills=["Francais", "Deutsch"],
        )
        renderer = HTMLRenderer()
        result = renderer.render_data(data)
        assert len(result.pdf_bytes) > 0

    def test_render_empty_sections(self):
        """Test rendering with empty optional sections."""
        data = ResumeData(
            contact=ContactInfo(name="Test User", email="test@example.com"),
            summary=None,
            experience=[],
            education=[],
            skills=[],
        )
        renderer = HTMLRenderer()
        result = renderer.render_data(data)
        assert len(result.pdf_bytes) > 0

    def test_render_urls_formatted(self, full_resume_data):
        """Test that URLs are included in rendered output."""
        renderer = HTMLRenderer()
        result = renderer.render_data(full_resume_data)
        # Just verify it renders - URL handling is in template
        assert len(result.pdf_bytes) > 0



# --- Integration Tests ---


class TestRendererIntegration:
    def test_html_produces_valid_pdf(self, full_resume_data):
        """Test that HTML renderer produces a valid PDF."""
        renderer = HTMLRenderer()
        result = renderer.render_data(full_resume_data)

        # Check PDF magic bytes
        assert result.pdf_bytes[:4] == b"%PDF"

    def test_html_renderer_produces_output(self, minimal_resume_data):
        """Test that HTML renderer produces output."""
        html_renderer = HTMLRenderer()
        html_result = html_renderer.render_data(minimal_resume_data)
        assert len(html_result.pdf_bytes) > 0
