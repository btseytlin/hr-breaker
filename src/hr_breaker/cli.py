"""CLI interface for HR-Breaker."""

import asyncio
from pathlib import Path

import click

from hr_breaker.agents import extract_name, parse_job_posting
from hr_breaker.config import get_settings
from hr_breaker.filters import FilterRegistry
from hr_breaker.filters.keyword_matcher import check_keywords
from hr_breaker.models import (
    FilterResult,
    GeneratedPDF,
    JobPosting,
    OptimizedResume,
    ResumeSource,
    SUPPORTED_LANGUAGES,
    get_language, 
    ValidationResult,
)
from hr_breaker.orchestration import optimize_for_job
from hr_breaker.services import (
    PDFStorage,
    scrape_job_posting,
    ScrapingError,
    CloudflareBlockedError,
)
from hr_breaker.services.pdf_parser import (
    extract_text_from_pdf_bytes as extract_text_from_pdf_bytes_service,
    load_resume_content,
)
from hr_breaker.services.renderer import HTMLRenderer, RenderError


@click.group()
def cli():
    """HR-Breaker: Optimize resumes for job postings."""
    pass


OUTPUT_DIR = Path("output")


@cli.command()
@click.argument("resume_path", type=click.Path(exists=True, path_type=Path))
@click.argument("job_input")
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path),
    default=None,
    envvar="HR_BREAKER_OUTPUT",
)
@click.option(
    "--max-iterations", "-n", type=int, default=None, envvar="HR_BREAKER_MAX_ITERATIONS"
)
@click.option(
    "--debug",
    "-d",
    is_flag=True,
    help="Save all iterations as PDFs to output/debug/",
    envvar="HR_BREAKER_DEBUG",
)
@click.option(
    "--seq",
    "-s",
    is_flag=True,
    help="Run filters sequentially (default: parallel)",
    envvar="HR_BREAKER_SEQ",
)
@click.option(
    "--no-shame",
    is_flag=True,
    help="Lenient mode: allow aggressive content stretching",
    envvar="HR_BREAKER_NO_SHAME",
)
@click.option(
    "--lang",
    "-l",
    type=click.Choice(
        [lang.code for lang in SUPPORTED_LANGUAGES], case_sensitive=False
    ),
    default=None,
    help="Output language (default: en). Optimization runs in English, then translates.",
)
@click.option(
    "--instructions",
    "-i",
    type=str,
    default=None,
    help="Instructions for the optimizer (extra experience, emphasis areas)",
)
def optimize(
    resume_path: Path,
    job_input: str,
    output: Path | None,
    max_iterations: int | None,
    debug: bool,
    seq: bool,
    no_shame: bool,
    lang: str | None,
    instructions: str | None,
):
    """Optimize resume for job posting.

    RESUME_PATH: Path to resume file (.tex, .md, .txt, .pdf, etc.)
    JOB_INPUT: URL or path to file with job description
    """
    resume_content = load_resume_content(resume_path)

    # Get job text (sync - may need user interaction for Cloudflare)
    job_text = _get_job_text(job_input)

    pdf_storage = PDFStorage()
    debug_dir: Path | None = None

    def on_iteration(i, optimized, validation):
        status = "PASS" if validation.passed else "FAIL"
        scores = ", ".join(
            f"{r.filter_name}:{r.score:.2f}/{r.threshold:.2f}"
            for r in validation.results
        )
        click.echo(f"  Iteration {i + 1}: {status} [{scores}]")

        # Save intermediate PDF in debug mode
        if debug and debug_dir:
            debug_pdf = debug_dir / f"iteration_{i + 1}.pdf"
            # Save HTML or JSON depending on what's available
            if optimized.html:
                debug_html = debug_dir / f"iteration_{i + 1}.html"
                debug_html.write_text(optimized.html, encoding="utf-8")
            elif optimized.data:
                debug_json = debug_dir / f"iteration_{i + 1}.json"
                debug_json.write_text(
                    optimized.data.model_dump_json(indent=2), encoding="utf-8"
                )
            if optimized.pdf_bytes:
                debug_pdf.write_bytes(optimized.pdf_bytes)
                click.echo(f"    Debug: saved {debug_pdf}")
            else:
                click.echo(f"    Debug: no PDF (render failed)")

    # Resolve target language
    settings = get_settings()
    lang_code = lang or settings.default_language
    target_language = get_language(lang_code) if lang_code != "en" else None

    def on_translation_status(msg: str):
        click.echo(f"  {msg}")

    # Run all async work in single event loop
    async def run_optimization():
        nonlocal debug_dir
        first_name, last_name = await extract_name(resume_content)
        click.echo(f"Resume: {first_name or 'Unknown'} {last_name or ''}")

        # Parse job first to get company/role for debug dir
        job = await parse_job_posting(job_text)
        click.echo(f"Job: {job.title} at {job.company}")

        if debug:
            debug_dir = pdf_storage.generate_debug_dir(job.company, job.title)

        mode = "sequential" if seq else "parallel"
        shame_mode = " [no-shame]" if no_shame else ""
        lang_label = f" [lang: {lang_code}]" if target_language else ""
        click.echo(f"Optimizing (mode: {mode}{shame_mode}{lang_label})...")

        source = ResumeSource(
            content=resume_content,
            first_name=first_name,
            last_name=last_name,
        )
        optimized, validation, _ = await optimize_for_job(
            source,
            max_iterations=max_iterations,
            on_iteration=on_iteration,
            job=job,
            parallel=not seq,
            no_shame=no_shame,
            user_instructions=instructions,
            language=target_language,
            on_translation_status=on_translation_status,
        )
        return first_name, last_name, source, optimized, validation, job

    first_name, last_name, source, optimized, validation, job = asyncio.run(
        run_optimization()
    )

    if not validation.passed:
        click.echo("Warning: Not all filters passed")

    # Save final PDF (reuse bytes from last iteration)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if output is None:
        output = (
            OUTPUT_DIR
            / pdf_storage.generate_path(
                first_name,
                last_name,
                job.company,
                job.title,
                lang_code=lang_code,
            ).name
        )

    if not optimized.pdf_bytes:
        raise click.ClickException("No PDF generated (render failed)")
    output.write_bytes(optimized.pdf_bytes)

    pdf_record = GeneratedPDF(
        path=output,
        source_checksum=source.checksum,
        company=job.company,
        job_title=job.title,
        first_name=first_name,
        last_name=last_name,
    )
    pdf_storage.save_record(pdf_record)

    click.echo(f"PDF saved: {output}")


@cli.command("list")
def list_history():
    """List generated PDFs."""
    pdf_storage = PDFStorage()
    pdfs = pdf_storage.list_all()

    if not pdfs:
        click.echo("No PDFs generated yet")
        return

    for pdf in pdfs:
        exists = "+" if pdf.path.exists() else "-"
        click.echo(
            f"[{exists}] {pdf.path.name} - {pdf.job_title} @ {pdf.company} "
            f"({pdf.timestamp.strftime('%Y-%m-%d %H:%M')})"
        )


def _get_job_text(job_input: str) -> str:
    """Get job text from URL or file path."""
    # Check if file
    path = Path(job_input)
    if path.exists():
        return path.read_text(encoding="utf-8")

    # Check if URL
    if job_input.startswith(("http://", "https://")):
        try:
            return scrape_job_posting(job_input)
        except CloudflareBlockedError:
            click.echo(f"Site has bot protection. Opening in browser...")
            click.launch(job_input)
            click.echo("Please copy the job description and paste below.")
            click.echo("(Press Enter twice when done)")
            return _read_multiline_input()
        except ScrapingError as e:
            raise click.ClickException(str(e))

    # Treat as raw text
    return job_input


def _read_multiline_input() -> str:
    """Read multiline input until double Enter."""
    lines = []
    empty_count = 0
    while True:
        try:
            line = input()
            if line == "":
                empty_count += 1
                if empty_count >= 2:
                    break
                lines.append(line)
            else:
                empty_count = 0
                lines.append(line)
        except EOFError:
            break
    text = "\n".join(lines).strip()
    if not text:
        raise click.ClickException("No job description provided")
    return text


def _read_text_or_pdf(path: Path) -> str:
    """Read text from a file. If it's a PDF, extract text."""
    return load_resume_content(path)


def _extract_text_from_pdf_bytes(pdf_bytes: bytes) -> str:
    return extract_text_from_pdf_bytes_service(pdf_bytes)


def _load_job_posting(job_path: Path) -> JobPosting:
    """Load JobPosting from JSON, or fall back to raw text."""
    if job_path.suffix.lower() == ".json":
        return JobPosting.model_validate_json(job_path.read_text())
    text = job_path.read_text()
    return JobPosting(
        title="Unknown",
        company="Unknown",
        description=text,
        raw_text=text,
    )


def _format_validation_markdown(validation: ValidationResult, skipped_filters: list[str]) -> str:
    lines = [
        "# Validation Results",
        "",
        f"Overall: {'PASS' if validation.passed else 'FAIL'}",
        "",
    ]
    for r in validation.results:
        status = "PASS" if r.passed else "FAIL"
        lines.append(f"- [{status}] {r.filter_name} ({r.score:.2f}/{r.threshold:.2f})")
        for issue in r.issues:
            lines.append(f"  - Issue: {issue}")
        for suggestion in r.suggestions:
            lines.append(f"  - Suggestion: {suggestion}")
        if r.feedback:
            lines.append(f"  - Feedback: {r.feedback}")
    if skipped_filters:
        lines.extend(["", "## Skipped (requires LLM/API)", ""])
        for name in skipped_filters:
            lines.append(f"- {name}")
    lines.append("")
    return "\n".join(lines)


def _apply_ide_keyword_gate(
    validation: ValidationResult,
    *,
    resume_text: str | None,
    job: JobPosting,
    min_keyword_score: float,
) -> ValidationResult:
    if not resume_text:
        gate = FilterResult(
            filter_name="IDEKeywordGate",
            passed=False,
            score=0.0,
            threshold=min_keyword_score,
            issues=["No PDF text available for strict IDE keyword gate"],
            suggestions=["Ensure PDF text extraction succeeds before validation"],
        )
        return ValidationResult(results=[*validation.results, gate])

    keyword_result = check_keywords(resume_text, job, threshold=min_keyword_score)
    issues: list[str] = []
    suggestions: list[str] = []
    if not keyword_result.passed:
        issues.append(
            "Strict IDE parity gate failed: keyword score "
            f"{keyword_result.score:.2f} is below target {min_keyword_score:.2f}"
        )
        if keyword_result.missing_keywords:
            issues.append(
                "High-value missing keywords: "
                + ", ".join(keyword_result.missing_keywords)
            )
        suggestions.append(
            "Increase role-critical keywords only where supported by original resume."
        )

    gate = FilterResult(
        filter_name="IDEKeywordGate",
        passed=keyword_result.passed,
        score=keyword_result.score,
        threshold=min_keyword_score,
        issues=issues,
        suggestions=suggestions,
    )
    return ValidationResult(results=[*validation.results, gate])


async def _run_validation_filters(
    optimized: OptimizedResume,
    job: JobPosting,
    source: ResumeSource,
    *,
    no_llm: bool,
) -> tuple[ValidationResult, list[str]]:
    llm_filter_names = {
        "LLMChecker",
        "HallucinationChecker",
        "AIGeneratedChecker",
        "VectorSimilarityMatcher",
    }
    skipped = sorted(llm_filter_names) if no_llm else []

    filter_classes = FilterRegistry.all()
    selected = [
        f
        for f in filter_classes
        if not (no_llm and getattr(f, "name", f.__name__) in llm_filter_names)
    ]
    selected = sorted(selected, key=lambda f: getattr(f, "priority", 50))

    results: list[FilterResult] = []
    for filter_cls in selected:
        f = filter_cls()
        try:
            result = await f.evaluate(optimized, job, source)
        except Exception as e:  # pragma: no cover - defensive for CLI UX
            results.append(
                FilterResult(
                    filter_name=getattr(f, "name", type(f).__name__),
                    passed=False,
                    score=0.0,
                    threshold=getattr(f, "threshold", 0.5),
                    issues=[f"Filter error: {type(e).__name__}: {e}"],
                    suggestions=["Fix filter configuration or rerun with --no-llm"],
                )
            )
        else:
            results.append(result)

        # Early exit on failure (same idea as orchestration)
        if not results[-1].passed and getattr(filter_cls, "priority", 50) < 100:
            break

    return ValidationResult(results=results), skipped


def _ide_paths(out_dir: Path) -> dict[str, Path]:
    return {
        "pdf": out_dir / "resume.pdf",
        "text": out_dir / "resume.txt",
        "validation_md": out_dir / "validation.md",
        "validation_json": out_dir / "validation.json",
    }


def _render_pdf_and_text(html_body: str) -> tuple[bytes, int, list[str], str]:
    renderer = HTMLRenderer()
    render_result = renderer.render(html_body)
    pdf_text = _extract_text_from_pdf_bytes(render_result.pdf_bytes)
    return (
        render_result.pdf_bytes,
        render_result.page_count,
        render_result.warnings,
        pdf_text,
    )


def _get_job_text_ide(job_input: str) -> str:
    """Get job text from URL, file path, stdin marker '-', or raw text."""
    if job_input.strip() == "-":
        click.echo("Paste job description below (press Enter twice when done):")
        return _read_multiline_input()

    path = Path(job_input)
    if path.exists():
        return path.read_text()

    if job_input.startswith(("http://", "https://")):
        try:
            return scrape_job_posting(job_input)
        except CloudflareBlockedError:
            click.echo("Site has bot protection (Cloudflare).")
            click.echo("Open the URL in your browser, copy the job description, then paste it below.")
            click.echo("(Press Enter twice when done)")
            return _read_multiline_input()
        except ScrapingError as e:
            raise click.ClickException(str(e)) from e

    # Treat as raw text
    return job_input


def _get_resume_text_ide(resume_input: str | None) -> tuple[str, Path | None]:
    """Get resume text from path, stdin marker '-', or raw text."""
    if resume_input is None:
        raise click.ClickException(
            "Resume input is required. Pass --resume <path> or --resume - to paste text."
        )

    if resume_input.strip() == "-":
        click.echo("Paste resume text below (press Enter twice when done):")
        return _read_multiline_input(), None

    resume_path = Path(resume_input)
    if resume_path.exists():
        return _read_text_or_pdf(resume_path), resume_path

    # Treat as raw text
    return resume_input, None


@cli.command("ide-ingest")
@click.option(
    "--resume",
    "resume_input",
    type=str,
    required=True,
    help="Resume source: path to .txt/.md/.tex/.pdf, '-' to paste via stdin, or raw text.",
)
@click.option(
    "--job",
    "job_input",
    type=str,
    required=True,
    help="Job source: URL, path to a text file, '-' to paste via stdin, or raw text.",
)
@click.option(
    "--out-dir",
    type=click.Path(path_type=Path),
    default=Path("output/ide"),
    help="Directory where resume/job text files will be written.",
)
@click.option(
    "--resume-out",
    type=click.Path(path_type=Path),
    default=None,
    help="Where to write extracted resume text (default: <out-dir>/resume_source.txt).",
)
@click.option(
    "--job-out",
    type=click.Path(path_type=Path),
    default=None,
    help="Where to write extracted job text (default: <out-dir>/job_text.txt).",
)
def ide_ingest(
    resume_input: str | None,
    job_input: str,
    out_dir: Path,
    resume_out: Path | None,
    job_out: Path | None,
):
    """Prepare IDE inputs without LLM/API: extract resume text (incl. PDF) + fetch job text (incl. URL scrape)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    if resume_out is None:
        resume_out = out_dir / "resume_source.txt"
    if job_out is None:
        job_out = out_dir / "job_text.txt"

    resume_text, resume_path = _get_resume_text_ide(resume_input)
    job_text = _get_job_text_ide(job_input)

    resume_out.parent.mkdir(parents=True, exist_ok=True)
    resume_out.write_text(resume_text)
    click.echo(f"Resume text saved: {resume_out}")
    if resume_path is not None:
        click.echo(f"Resume source: {resume_path}")

    job_out.parent.mkdir(parents=True, exist_ok=True)
    job_out.write_text(job_text)
    click.echo(f"Job text saved: {job_out}")


def _ide_sync_impl(
    resume_path: Path | None,
    job_path: Path,
    html_path: Path,
    out_dir: Path,
    min_keyword_score: float,
):
    """One-shot IDE workflow: render PDF + extract text + run local validation (no API/LLM)."""
    if resume_path is None:
        raise click.ClickException("Resume path is required. Pass --resume <path>.")

    if not resume_path.exists():
        raise click.ClickException(f"Resume not found: {resume_path}")
    if not job_path.exists():
        raise click.ClickException(f"Job posting JSON not found: {job_path}")
    if not html_path.exists():
        raise click.ClickException(f"Resume HTML body not found: {html_path}")

    out_dir.mkdir(parents=True, exist_ok=True)
    paths = _ide_paths(out_dir)

    source = ResumeSource(content=_read_text_or_pdf(resume_path))
    job = _load_job_posting(job_path)
    html_body = html_path.read_text()

    try:
        pdf_bytes, page_count, warnings, pdf_text = _render_pdf_and_text(html_body)
    except RenderError as e:
        raise click.ClickException(str(e)) from e

    paths["pdf"].write_bytes(pdf_bytes)
    paths["text"].write_text(pdf_text)
    click.echo(f"PDF saved: {paths['pdf']} (pages: {page_count})")
    for w in warnings:
        click.echo(f"Warning: {w}")
    click.echo(f"Text extracted: {paths['text']}")

    optimized = OptimizedResume(
        html=html_body,
        source_checksum=source.checksum,
        pdf_bytes=pdf_bytes,
        pdf_text=pdf_text,
    )
    validation, skipped = asyncio.run(
        _run_validation_filters(optimized, job, source, no_llm=True)
    )
    validation = _apply_ide_keyword_gate(
        validation,
        resume_text=optimized.pdf_text,
        job=job,
        min_keyword_score=min_keyword_score,
    )
    paths["validation_md"].write_text(_format_validation_markdown(validation, skipped))
    paths["validation_json"].write_text(validation.model_dump_json(indent=2))
    click.echo(f"Validation saved: {paths['validation_md']}")

    if not validation.passed:
        raise click.ClickException("Validation failed (see output/ide/validation.md)")


@cli.command("ide-sync")
@click.option(
    "--resume",
    "resume_path",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Path to resume source file (.txt/.md/.tex/.pdf).",
)
@click.option(
    "--job",
    "job_path",
    type=click.Path(path_type=Path),
    default=Path("output/ide/job_posting.json"),
)
@click.option(
    "--html",
    "html_path",
    type=click.Path(path_type=Path),
    default=Path("output/ide/resume_body.html"),
)
@click.option(
    "--out-dir",
    type=click.Path(path_type=Path),
    default=Path("output/ide"),
    help="Directory for generated artifacts (pdf/text/validation).",
)
@click.option(
    "--min-keyword-score",
    type=click.FloatRange(0.0, 1.0),
    default=None,
    help="Strict IDE parity gate: fail if keyword score is below this value (default from config: 0.55).",
)
def ide_sync(
    resume_path: Path | None,
    job_path: Path,
    html_path: Path,
    out_dir: Path,
    min_keyword_score: float | None,
):
    """One-shot IDE workflow: render PDF + extract text + run local validation (no API/LLM)."""
    if min_keyword_score is None:
        min_keyword_score = get_settings().ide_keyword_target_score
    _ide_sync_impl(
        resume_path=resume_path,
        job_path=job_path,
        html_path=html_path,
        out_dir=out_dir,
        min_keyword_score=min_keyword_score,
    )


if __name__ == "__main__":
    cli()
