"""CLI interface for HR-Breaker."""

import asyncio
from pathlib import Path

import click

from hr_breaker.agents import extract_name, parse_job_posting
from hr_breaker.config import get_settings
from hr_breaker.models import GeneratedPDF, ResumeSource
from hr_breaker.orchestration import optimize_for_job
from hr_breaker.services import PDFStorage, scrape_job_posting, ScrapingError, CloudflareBlockedError


@click.group()
def cli():
    """HR-Breaker: Optimize resumes for job postings."""
    pass


OUTPUT_DIR = Path("output")


@cli.command()
@click.argument("resume_path", type=click.Path(exists=True, path_type=Path))
@click.argument("job_input")
@click.option("--output", "-o", type=click.Path(path_type=Path), default=None)
@click.option("--max-iterations", "-n", type=int, default=None)
@click.option("--debug", "-d", is_flag=True, help="Save all iterations as PDFs to output/debug/")
@click.option("--seq", "-s", is_flag=True, help="Run filters sequentially (default: parallel)")
@click.option("--no-shame", is_flag=True, help="Lenient mode: allow aggressive content stretching")
def optimize(
    resume_path: Path,
    job_input: str,
    output: Path | None,
    max_iterations: int | None,
    debug: bool,
    seq: bool,
    no_shame: bool,
):
    """Optimize resume for job posting.

    RESUME_PATH: Path to resume file (any text format: .tex, .md, .txt, etc.)
    JOB_INPUT: URL or path to file with job description
    """
    settings = get_settings()
    if not settings.google_api_key:
        raise click.ClickException("GOOGLE_API_KEY not set in environment")

    resume_content = resume_path.read_text()

    # Get job text (sync - may need user interaction for Cloudflare)
    job_text = _get_job_text(job_input)

    pdf_storage = PDFStorage()
    debug_dir: Path | None = None

    def on_iteration(i, optimized, validation):
        status = "PASS" if validation.passed else "FAIL"
        scores = ", ".join(f"{r.filter_name}:{r.score:.2f}/{r.threshold:.2f}" for r in validation.results)
        click.echo(f"  Iteration {i + 1}: {status} [{scores}]")

        # Save intermediate PDF in debug mode
        if debug and debug_dir:
            debug_pdf = debug_dir / f"iteration_{i + 1}.pdf"
            # Save HTML or JSON depending on what's available
            if optimized.html:
                debug_html = debug_dir / f"iteration_{i + 1}.html"
                debug_html.write_text(optimized.html)
            elif optimized.data:
                debug_json = debug_dir / f"iteration_{i + 1}.json"
                debug_json.write_text(optimized.data.model_dump_json(indent=2))
            if optimized.pdf_bytes:
                debug_pdf.write_bytes(optimized.pdf_bytes)
                click.echo(f"    Debug: saved {debug_pdf}")
            else:
                click.echo(f"    Debug: no PDF (render failed)")

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
        click.echo(f"Optimizing (mode: {mode}{shame_mode})...")

        source = ResumeSource(
            content=resume_content,
            first_name=first_name,
            last_name=last_name,
        )
        optimized, validation, _ = await optimize_for_job(
            source, max_iterations=max_iterations, on_iteration=on_iteration, job=job,
            parallel=not seq, no_shame=no_shame
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
        output = OUTPUT_DIR / pdf_storage.generate_path(first_name, last_name, job.company, job.title).name

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
        return path.read_text()

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


if __name__ == "__main__":
    cli()
