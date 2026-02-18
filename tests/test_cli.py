"""Tests for CLI helpers and IDE workflow commands."""

import json
from pathlib import Path

import click
import fitz
import pytest
from click.testing import CliRunner

import hr_breaker.cli as cli_module
from hr_breaker.models import (
    FilterResult,
    JobPosting,
    OptimizedResume,
    ResumeSource,
    ValidationResult,
)
from hr_breaker.services.scrapers.base import CloudflareBlockedError, ScrapingError


def _make_pdf_bytes(*page_texts: str) -> bytes:
    doc = fitz.open()
    try:
        for text in page_texts:
            page = doc.new_page()
            page.insert_text((72, 72), text)
        return doc.tobytes()
    finally:
        doc.close()


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_read_text_or_pdf_reads_plain_text(tmp_path: Path):
    src = tmp_path / "resume.txt"
    src.write_text("plain text resume")

    result = cli_module._read_text_or_pdf(src)

    assert result == "plain text resume"


def test_read_text_or_pdf_reads_pdf_with_parser(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    src = tmp_path / "resume.pdf"
    src.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(cli_module, "load_resume_content", lambda p: f"pdf:{p.name}")

    result = cli_module._read_text_or_pdf(src)

    assert result == "pdf:resume.pdf"


def test_load_job_posting_from_json(tmp_path: Path):
    path = tmp_path / "job.json"
    payload = {
        "title": "Backend Engineer",
        "company": "Acme",
        "requirements": ["Python"],
        "keywords": ["python"],
        "description": "Build APIs",
        "raw_text": "Build APIs with Python",
    }
    path.write_text(json.dumps(payload))

    job = cli_module._load_job_posting(path)

    assert job.title == "Backend Engineer"
    assert job.company == "Acme"
    assert job.raw_text == "Build APIs with Python"


def test_load_job_posting_from_text_fallback(tmp_path: Path):
    path = tmp_path / "job.txt"
    path.write_text("Raw job text")

    job = cli_module._load_job_posting(path)

    assert job.title == "Unknown"
    assert job.company == "Unknown"
    assert job.description == "Raw job text"
    assert job.raw_text == "Raw job text"


def test_format_validation_markdown_includes_feedback_and_skipped():
    validation = ValidationResult(
        results=[
            FilterResult(
                filter_name="KeywordMatcher",
                passed=False,
                score=0.2,
                threshold=0.3,
                issues=["Missing keyword: python"],
                suggestions=["Add Python if truthful"],
                feedback="Tighten bullets",
            )
        ]
    )

    rendered = cli_module._format_validation_markdown(validation, ["LLMChecker"])

    assert "Overall: FAIL" in rendered
    assert "Missing keyword: python" in rendered
    assert "Feedback: Tighten bullets" in rendered
    assert "## Skipped (requires LLM/API)" in rendered
    assert "- LLMChecker" in rendered


def test_ide_paths_helper(tmp_path: Path):
    out_dir = tmp_path / "output" / "ide"
    paths = cli_module._ide_paths(out_dir)

    assert paths["pdf"] == out_dir / "resume.pdf"
    assert paths["text"] == out_dir / "resume.txt"
    assert paths["validation_md"] == out_dir / "validation.md"
    assert paths["validation_json"] == out_dir / "validation.json"


def test_get_job_text_ide_from_path(tmp_path: Path):
    job_path = tmp_path / "job.txt"
    job_path.write_text("Job from file")

    result = cli_module._get_job_text_ide(str(job_path))

    assert result == "Job from file"


def test_get_job_text_ide_from_stdin_marker(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(cli_module, "_read_multiline_input", lambda: "job from stdin")

    result = cli_module._get_job_text_ide("-")

    assert result == "job from stdin"


def test_get_job_text_ide_cloudflare_fallback(monkeypatch: pytest.MonkeyPatch):
    def _raise(_: str) -> str:
        raise CloudflareBlockedError("blocked")

    monkeypatch.setattr(cli_module, "scrape_job_posting", _raise)
    monkeypatch.setattr(cli_module, "_read_multiline_input", lambda: "Pasted fallback text")

    result = cli_module._get_job_text_ide("https://example.com/job")

    assert result == "Pasted fallback text"


def test_get_job_text_ide_scraping_error(monkeypatch: pytest.MonkeyPatch):
    def _raise(_: str) -> str:
        raise ScrapingError("boom")

    monkeypatch.setattr(cli_module, "scrape_job_posting", _raise)

    with pytest.raises(click.ClickException, match="boom"):
        cli_module._get_job_text_ide("https://example.com/job")


def test_get_job_text_ide_raw_text_input():
    result = cli_module._get_job_text_ide("raw job text")

    assert result == "raw job text"


def test_get_resume_text_ide_requires_resume_input():
    with pytest.raises(click.ClickException, match="Resume input is required"):
        cli_module._get_resume_text_ide(None)


def test_get_resume_text_ide_stdin(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(cli_module, "_read_multiline_input", lambda: "stdin resume")

    text, path = cli_module._get_resume_text_ide("-")

    assert text == "stdin resume"
    assert path is None


def test_get_resume_text_ide_from_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    src = tmp_path / "resume.txt"
    src.write_text("resume from file")
    monkeypatch.setattr(cli_module, "_read_text_or_pdf", lambda _p: "resume from helper")

    text, path = cli_module._get_resume_text_ide(str(src))

    assert text == "resume from helper"
    assert path == src


def test_get_resume_text_ide_raw_text_input():
    text, path = cli_module._get_resume_text_ide("direct raw resume")

    assert text == "direct raw resume"
    assert path is None


def test_read_multiline_input_returns_text(monkeypatch: pytest.MonkeyPatch):
    values = iter(["line1", "line2", "", ""])
    monkeypatch.setattr("builtins.input", lambda: next(values))

    result = cli_module._read_multiline_input()

    assert result == "line1\nline2"


def test_read_multiline_input_raises_on_empty(monkeypatch: pytest.MonkeyPatch):
    values = iter(["", ""])
    monkeypatch.setattr("builtins.input", lambda: next(values))

    with pytest.raises(click.ClickException, match="No job description provided"):
        cli_module._read_multiline_input()


def test_read_multiline_input_handles_eof(monkeypatch: pytest.MonkeyPatch):
    values = iter(["line1"])

    def _fake_input():
        try:
            return next(values)
        except StopIteration as e:
            raise EOFError from e

    monkeypatch.setattr("builtins.input", _fake_input)

    result = cli_module._read_multiline_input()

    assert result == "line1"


def test_extract_text_from_pdf_bytes_delegates(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        cli_module,
        "extract_text_from_pdf_bytes_service",
        lambda _b: "delegated text",
    )

    result = cli_module._extract_text_from_pdf_bytes(b"%PDF")

    assert result == "delegated text"


def test_render_pdf_and_text_uses_renderer_and_pdf_text_extractor(
    monkeypatch: pytest.MonkeyPatch,
):
    class _Renderer:
        def render(self, _html: str):
            return type(
                "_RenderResult",
                (),
                {
                    "pdf_bytes": b"%PDF-sync",
                    "page_count": 1,
                    "warnings": ["tight layout"],
                },
            )()

    monkeypatch.setattr(cli_module, "HTMLRenderer", _Renderer)
    monkeypatch.setattr(cli_module, "_extract_text_from_pdf_bytes", lambda _b: "pdf text")

    pdf_bytes, page_count, warnings, text = cli_module._render_pdf_and_text("<header></header>")

    assert pdf_bytes == b"%PDF-sync"
    assert page_count == 1
    assert warnings == ["tight layout"]
    assert text == "pdf text"


def test_apply_ide_keyword_gate_fails_when_pdf_text_missing():
    validation = ValidationResult(results=[])
    job = JobPosting(title="Engineer", company="Acme", raw_text="raw")

    result = cli_module._apply_ide_keyword_gate(
        validation,
        resume_text=None,
        job=job,
        min_keyword_score=0.55,
    )

    gate = result.results[-1]
    assert gate.filter_name == "IDEKeywordGate"
    assert not gate.passed
    assert gate.threshold == 0.55
    assert "No PDF text available for strict IDE keyword gate" in gate.issues[0]


@pytest.mark.asyncio
async def test_run_validation_filters_no_llm_skips_llm_filters(monkeypatch: pytest.MonkeyPatch):
    source = ResumeSource(content="resume")
    job = JobPosting(title="T", company="C")
    optimized = OptimizedResume(html="<header class='header'></header>", source_checksum=source.checksum)

    class _LLMFilter:
        name = "LLMChecker"
        priority = 5
        threshold = 0.5

        def __init__(self, **kwargs):
            pass

        async def evaluate(self, *_args, **_kwargs):
            return FilterResult(
                filter_name=self.name,
                passed=True,
                score=1.0,
                threshold=self.threshold,
                issues=[],
                suggestions=[],
            )

    class _LocalFilter:
        name = "DataValidator"
        priority = 1
        threshold = 1.0

        def __init__(self, **kwargs):
            pass

        async def evaluate(self, *_args, **_kwargs):
            return FilterResult(
                filter_name=self.name,
                passed=True,
                score=1.0,
                threshold=self.threshold,
                issues=[],
                suggestions=[],
            )

    monkeypatch.setattr(cli_module.FilterRegistry, "all", lambda: [_LLMFilter, _LocalFilter])

    validation, skipped = await cli_module._run_validation_filters(optimized, job, source, no_llm=True)

    assert [r.filter_name for r in validation.results] == ["DataValidator"]
    assert skipped == [
        "AIGeneratedChecker",
        "HallucinationChecker",
        "LLMChecker",
        "VectorSimilarityMatcher",
    ]


@pytest.mark.asyncio
async def test_run_validation_filters_stops_on_first_failure(monkeypatch: pytest.MonkeyPatch):
    source = ResumeSource(content="resume")
    job = JobPosting(title="T", company="C")
    optimized = OptimizedResume(html="<header class='header'></header>", source_checksum=source.checksum)
    calls: list[str] = []

    class _Pass:
        name = "Pass"
        priority = 1
        threshold = 1.0

        def __init__(self, **kwargs):
            pass

        async def evaluate(self, *_args, **_kwargs):
            calls.append(self.name)
            return FilterResult(filter_name=self.name, passed=True, score=1.0, threshold=1.0)

    class _Fail:
        name = "Fail"
        priority = 2
        threshold = 1.0

        def __init__(self, **kwargs):
            pass

        async def evaluate(self, *_args, **_kwargs):
            calls.append(self.name)
            return FilterResult(
                filter_name=self.name,
                passed=False,
                score=0.0,
                threshold=1.0,
                issues=["x"],
                suggestions=["y"],
            )

    class _NeverReached:
        name = "NeverReached"
        priority = 3
        threshold = 1.0

        def __init__(self, **kwargs):
            pass

        async def evaluate(self, *_args, **_kwargs):
            calls.append(self.name)
            return FilterResult(filter_name=self.name, passed=True, score=1.0, threshold=1.0)

    monkeypatch.setattr(cli_module.FilterRegistry, "all", lambda: [_Pass, _Fail, _NeverReached])

    validation, _ = await cli_module._run_validation_filters(optimized, job, source, no_llm=False)

    assert calls == ["Pass", "Fail"]
    assert [r.filter_name for r in validation.results] == ["Pass", "Fail"]
    assert not validation.passed


@pytest.mark.asyncio
async def test_run_validation_filters_converts_filter_exception(monkeypatch: pytest.MonkeyPatch):
    source = ResumeSource(content="resume")
    job = JobPosting(title="T", company="C")
    optimized = OptimizedResume(html="<header class='header'></header>", source_checksum=source.checksum)

    class _Exploding:
        name = "Exploding"
        priority = 1
        threshold = 0.5

        def __init__(self, **kwargs):
            pass

        async def evaluate(self, *_args, **_kwargs):
            raise RuntimeError("crashed")

    monkeypatch.setattr(cli_module.FilterRegistry, "all", lambda: [_Exploding])

    validation, _ = await cli_module._run_validation_filters(optimized, job, source, no_llm=False)

    assert len(validation.results) == 1
    result = validation.results[0]
    assert result.filter_name == "Exploding"
    assert not result.passed
    assert "Filter error: RuntimeError: crashed" in result.issues[0]


def test_ide_ingest_command_writes_normalized_files(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
):
    monkeypatch.setattr(cli_module, "scrape_job_posting", lambda _u: "job from url")

    with runner.isolated_filesystem():
        Path("resume.txt").write_text("resume source")
        result = runner.invoke(
            cli_module.cli,
            [
                "ide-ingest",
                "--resume",
                "resume.txt",
                "--job",
                "https://example.com/job",
                "--out-dir",
                "output/ide",
            ],
        )

        assert result.exit_code == 0, result.output
        assert Path("output/ide/resume_source.txt").read_text() == "resume source"
        assert Path("output/ide/job_text.txt").read_text() == "job from url"


def test_ide_ingest_requires_resume_option(runner: CliRunner):
    with runner.isolated_filesystem():
        result = runner.invoke(
            cli_module.cli,
            [
                "ide-ingest",
                "--job",
                "Raw job text from chat",
                "--out-dir",
                "output/ide",
            ],
        )

        assert result.exit_code != 0
        assert "Missing option '--resume'" in result.output


def test_ide_sync_requires_resume_option(runner: CliRunner):
    with runner.isolated_filesystem():
        result = runner.invoke(cli_module.cli, ["ide-sync"])

    assert result.exit_code != 0
    assert "Missing option '--resume'" in result.output


def test_ide_sync_command_generates_artifacts(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
):
    async def _fake_run_filters(_optimized, _job, _source, *, no_llm: bool):
        assert no_llm is True
        return (
            ValidationResult(
                results=[
                    FilterResult(
                        filter_name="DataValidator",
                        passed=True,
                        score=1.0,
                        threshold=1.0,
                    )
                ]
            ),
            ["LLMChecker"],
        )

    monkeypatch.setattr(
        cli_module,
        "_render_pdf_and_text",
        lambda _html: (b"%PDF-sync", 1, ["tight layout"], "engineer pdf extracted text"),
    )
    monkeypatch.setattr(cli_module, "_run_validation_filters", _fake_run_filters)

    with runner.isolated_filesystem():
        Path("resume.txt").write_text("resume")
        Path("output/ide").mkdir(parents=True, exist_ok=True)
        Path("output/ide/job_posting.json").write_text(
            json.dumps(
                {
                    "title": "Engineer",
                    "company": "Acme",
                    "requirements": ["engineer"],
                    "keywords": ["engineer", "pdf"],
                    "description": "engineer pdf extracted text",
                    "raw_text": "engineer pdf extracted text",
                }
            )
        )
        Path("output/ide/resume_body.html").write_text("<header class='header'><h1 class='name'>Jane</h1></header>")

        result = runner.invoke(cli_module.cli, ["ide-sync", "--resume", "resume.txt"])

        assert result.exit_code == 0, result.output
        assert Path("output/ide/resume.pdf").read_bytes() == b"%PDF-sync"
        assert Path("output/ide/resume.txt").read_text() == "engineer pdf extracted text"
        assert Path("output/ide/validation.md").exists()
        assert Path("output/ide/validation.json").exists()
        assert "Warning: tight layout" in result.output


def test_ide_sync_impl_requires_resume_path():
    with pytest.raises(click.ClickException, match="Resume path is required"):
        cli_module._ide_sync_impl(
            resume_path=None,
            job_path=Path("output/ide/job_posting.json"),
            html_path=Path("output/ide/resume_body.html"),
            out_dir=Path("output/ide"),
            min_keyword_score=0.55,
        )


def test_ide_sync_impl_fails_when_resume_file_missing(tmp_path: Path):
    missing_resume = tmp_path / "missing_resume.txt"

    with pytest.raises(click.ClickException, match="Resume not found"):
        cli_module._ide_sync_impl(
            resume_path=missing_resume,
            job_path=tmp_path / "job_posting.json",
            html_path=tmp_path / "resume_body.html",
            out_dir=tmp_path / "output" / "ide",
            min_keyword_score=0.55,
        )


def test_ide_sync_impl_fails_when_job_file_missing(tmp_path: Path):
    resume = tmp_path / "resume.txt"
    resume.write_text("resume")
    missing_job = tmp_path / "missing_job.json"

    with pytest.raises(click.ClickException, match="Job posting JSON not found"):
        cli_module._ide_sync_impl(
            resume_path=resume,
            job_path=missing_job,
            html_path=tmp_path / "resume_body.html",
            out_dir=tmp_path / "output" / "ide",
            min_keyword_score=0.55,
        )


def test_ide_sync_impl_fails_when_html_file_missing(tmp_path: Path):
    resume = tmp_path / "resume.txt"
    resume.write_text("resume")
    job = tmp_path / "job_posting.json"
    job.write_text(
        json.dumps(
            {
                "title": "Engineer",
                "company": "Acme",
                "requirements": [],
                "keywords": [],
                "description": "",
                "raw_text": "raw",
            }
        )
    )
    missing_html = tmp_path / "missing_resume_body.html"

    with pytest.raises(click.ClickException, match="Resume HTML body not found"):
        cli_module._ide_sync_impl(
            resume_path=resume,
            job_path=job,
            html_path=missing_html,
            out_dir=tmp_path / "output" / "ide",
            min_keyword_score=0.55,
        )


def test_ide_sync_impl_fails_on_render_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    resume = tmp_path / "resume.txt"
    resume.write_text("resume")
    job = tmp_path / "job_posting.json"
    job.write_text(
        json.dumps(
            {
                "title": "Engineer",
                "company": "Acme",
                "requirements": [],
                "keywords": [],
                "description": "",
                "raw_text": "raw",
            }
        )
    )
    html = tmp_path / "resume_body.html"
    html.write_text("<header class='header'></header>")

    def _raise(_html_body: str):
        raise cli_module.RenderError("render failed")

    monkeypatch.setattr(cli_module, "_render_pdf_and_text", _raise)

    with pytest.raises(click.ClickException, match="render failed"):
        cli_module._ide_sync_impl(
            resume_path=resume,
            job_path=job,
            html_path=html,
            out_dir=tmp_path / "output" / "ide",
            min_keyword_score=0.55,
        )


def test_ide_sync_command_fails_on_validation_fail(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
):
    async def _fake_run_filters(_optimized, _job, _source, *, no_llm: bool):
        assert no_llm is True
        return (
            ValidationResult(
                results=[
                    FilterResult(
                        filter_name="DataValidator",
                        passed=False,
                        score=0.0,
                        threshold=1.0,
                        issues=["broken"],
                        suggestions=["fix"],
                    )
                ]
            ),
            [],
        )

    monkeypatch.setattr(
        cli_module,
        "_render_pdf_and_text",
        lambda _html: (b"%PDF-sync", 1, [], "pdf extracted text"),
    )
    monkeypatch.setattr(cli_module, "_run_validation_filters", _fake_run_filters)

    with runner.isolated_filesystem():
        Path("resume.txt").write_text("resume")
        Path("output/ide").mkdir(parents=True, exist_ok=True)
        Path("output/ide/job_posting.json").write_text(
            json.dumps(
                {
                    "title": "Engineer",
                    "company": "Acme",
                    "requirements": [],
                    "keywords": [],
                    "description": "",
                    "raw_text": "raw",
                }
            )
        )
        Path("output/ide/resume_body.html").write_text("<header class='header'><h1 class='name'>Jane</h1></header>")

        result = runner.invoke(cli_module.cli, ["ide-sync", "--resume", "resume.txt"])

        assert result.exit_code != 0
        assert "Validation failed (see output/ide/validation.md)" in result.output


def test_ide_sync_fails_when_strict_keyword_gate_not_met(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
):
    async def _fake_run_filters(_optimized, _job, _source, *, no_llm: bool):
        assert no_llm is True
        return (
            ValidationResult(
                results=[
                    FilterResult(
                        filter_name="DataValidator",
                        passed=True,
                        score=1.0,
                        threshold=1.0,
                    )
                ]
            ),
            [],
        )

    monkeypatch.setattr(
        cli_module,
        "_render_pdf_and_text",
        lambda _html: (b"%PDF-sync", 1, [], "python"),
    )
    monkeypatch.setattr(cli_module, "_run_validation_filters", _fake_run_filters)

    with runner.isolated_filesystem():
        Path("resume.txt").write_text("resume")
        Path("output/ide").mkdir(parents=True, exist_ok=True)
        Path("output/ide/job_posting.json").write_text(
            json.dumps(
                {
                    "title": "ML Engineer",
                    "company": "Acme",
                    "requirements": ["Python", "LangChain"],
                    "keywords": ["python", "langchain"],
                    "description": "python and langchain required",
                    "raw_text": "python and langchain required",
                }
            )
        )
        Path("output/ide/resume_body.html").write_text(
            "<header class='header'><h1 class='name'>Jane</h1></header><section class='section'></section>"
        )

        result = runner.invoke(
            cli_module.cli,
            ["ide-sync", "--resume", "resume.txt", "--min-keyword-score", "0.95"],
        )

        assert result.exit_code != 0
        assert "Validation failed (see output/ide/validation.md)" in result.output
        md = Path("output/ide/validation.md").read_text()
        assert "IDEKeywordGate" in md
        assert "[FAIL] IDEKeywordGate" in md


def test_ide_sync_passes_when_strict_keyword_gate_met(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
):
    async def _fake_run_filters(_optimized, _job, _source, *, no_llm: bool):
        assert no_llm is True
        return (
            ValidationResult(
                results=[
                    FilterResult(
                        filter_name="DataValidator",
                        passed=True,
                        score=1.0,
                        threshold=1.0,
                    )
                ]
            ),
            [],
        )

    monkeypatch.setattr(
        cli_module,
        "_render_pdf_and_text",
        lambda _html: (b"%PDF-sync", 1, [], "ml engineer python langchain required"),
    )
    monkeypatch.setattr(cli_module, "_run_validation_filters", _fake_run_filters)

    with runner.isolated_filesystem():
        Path("resume.txt").write_text("resume")
        Path("output/ide").mkdir(parents=True, exist_ok=True)
        Path("output/ide/job_posting.json").write_text(
            json.dumps(
                {
                    "title": "ML Engineer",
                    "company": "Acme",
                    "requirements": ["Python", "LangChain"],
                    "keywords": ["python", "langchain"],
                    "description": "python and langchain required",
                    "raw_text": "python and langchain required",
                }
            )
        )
        Path("output/ide/resume_body.html").write_text(
            "<header class='header'><h1 class='name'>Jane</h1></header><section class='section'></section>"
        )

        result = runner.invoke(
            cli_module.cli,
            ["ide-sync", "--resume", "resume.txt", "--min-keyword-score", "0.55"],
        )

        assert result.exit_code == 0, result.output
        md = Path("output/ide/validation.md").read_text()
        assert "IDEKeywordGate" in md
        assert "[PASS] IDEKeywordGate" in md


def test_smoke_ide_sync_strict_gate_passes(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
):
    async def _fake_run_filters(_optimized, _job, _source, *, no_llm: bool):
        assert no_llm is True
        return (
            ValidationResult(
                results=[
                    FilterResult(
                        filter_name="DataValidator",
                        passed=True,
                        score=1.0,
                        threshold=1.0,
                    )
                ]
            ),
            [],
        )

    monkeypatch.setattr(cli_module, "_run_validation_filters", _fake_run_filters)
    monkeypatch.setattr(
        cli_module,
        "_render_pdf_and_text",
        lambda _html: (
            _make_pdf_bytes("python langchain rag"),
            1,
            [],
            "python langchain rag",
        ),
    )

    with runner.isolated_filesystem():
        Path("resume.txt").write_text("resume")
        Path("output/ide").mkdir(parents=True, exist_ok=True)
        Path("output/ide/job_posting.json").write_text(
            json.dumps(
                {
                    "title": "ML Engineer",
                    "company": "Acme",
                    "requirements": ["Python", "LangChain", "RAG"],
                    "keywords": ["python", "langchain", "rag"],
                    "description": "python langchain rag required",
                    "raw_text": "python langchain rag required",
                }
            )
        )
        Path("output/ide/resume_body.html").write_text(
            "<header class='header'><h1 class='name'>Jane</h1></header><section class='section'></section>"
        )

        sync_result = runner.invoke(
            cli_module.cli,
            ["ide-sync", "--resume", "resume.txt", "--min-keyword-score", "0.55"],
        )
        assert sync_result.exit_code == 0, sync_result.output
