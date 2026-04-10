"""CLI interface for HR-Breaker."""

import asyncio
import threading
import webbrowser
from pathlib import Path

import click
import uvicorn

from hr_breaker.agents import extract_name, parse_job_posting
from hr_breaker.config import get_settings
from hr_breaker.models import (
    GeneratedPDF,
    JobPosting,
    ResumeSource,
    SUPPORTED_LANGUAGES,
    get_language_safe,
    resolve_target_language,
)
from hr_breaker.models.profile import document_needs_extraction, get_document_extraction
from hr_breaker.orchestration import optimize_for_job
from hr_breaker.services import (
    PDFStorage,
    scrape_job_posting,
    ScrapingError,
    CloudflareBlockedError,
)
from hr_breaker.services.pdf_storage import generate_run_id
from hr_breaker.services.pdf_parser import load_resume_content


# ---------------------------------------------------------------------------
# Helpers for extraction state display
# ---------------------------------------------------------------------------

def _format_extraction_state(doc) -> str:
    status = str(doc.metadata.get("extraction_status") or "").lower()
    if status == "empty":
        return "empty extraction"
    if status == "failed":
        return "failed extraction"
    if get_document_extraction(doc) is not None:
        return "extracted"
    return "no extraction"


def _print_extraction_result(doc) -> str:
    status = str(doc.metadata.get("extraction_status") or "").lower()
    if status == "done":
        click.echo(" ok")
        return "done"
    if status == "empty":
        click.echo(" empty")
        return "empty"
    click.echo(f" unexpected status: {status or 'missing'}")
    return "unexpected"


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------

@click.group()
def cli():
    """HR-Breaker: Optimize resumes for job postings."""
    pass


OUTPUT_DIR = Path("output")


# ---------------------------------------------------------------------------
# profile subcommand group
# ---------------------------------------------------------------------------

@cli.group()
def profile():
    """Manage profile archives."""
    pass


@profile.command("list")
def profile_list():
    """List all profiles."""
    from hr_breaker.services.profile_store import ProfileStore

    store = ProfileStore()
    profiles = store.list_profiles()
    if not profiles:
        click.echo("No profiles found. Create one with: hr-breaker profile create <name>")
        return
    for p in profiles:
        name_part = f" ({p.full_name})" if p.full_name else ""
        doc_count = len(store.list_documents(p.id))
        click.echo(f"  {p.id:30s}  {p.display_name}{name_part}  [{doc_count} doc(s)]")


@profile.command("create")
@click.argument("name")
@click.option("--first-name", default=None, help="Candidate first name")
@click.option("--last-name", default=None, help="Candidate last name")
@click.option("--instructions", "-i", default=None, help="Standing instructions for the optimizer")
def profile_create(name: str, first_name: str | None, last_name: str | None, instructions: str | None):
    """Create a new profile archive.

    NAME: Display name for the profile (e.g. "John Doe")
    """
    from hr_breaker.services.profile_store import ProfileStore

    store = ProfileStore()
    p = store.create_profile(name, first_name=first_name, last_name=last_name, instructions=instructions)
    click.echo(f"Created profile: {p.id}  ({p.display_name})")


@profile.command("show")
@click.argument("profile_id")
def profile_show(profile_id: str):
    """Show profile details and its documents."""
    from hr_breaker.services.profile_store import ProfileStore

    store = ProfileStore()
    p = store.get_profile(profile_id)
    if p is None:
        raise click.ClickException(f"Profile not found: {profile_id}")

    click.echo(f"ID:           {p.id}")
    click.echo(f"Name:         {p.display_name}")
    if p.full_name:
        click.echo(f"Full name:    {p.full_name}")
    if p.instructions:
        click.echo(f"Instructions: {p.instructions}")
    click.echo(f"Updated:      {p.updated_at.strftime('%Y-%m-%d %H:%M')}")

    docs = store.list_documents(p.id)
    click.echo(f"\nDocuments ({len(docs)}):")
    if not docs:
        click.echo("  (none — add with: hr-breaker profile add <profile-id> <file>)")
    for doc in docs:
        extraction_state = _format_extraction_state(doc)
        incl = "+" if doc.included_by_default else "-"
        click.echo(f"  [{incl}] {doc.id[:12]}  {doc.title:40s}  [{doc.kind}, {extraction_state}]")


@profile.command("add")
@click.argument("profile_id")
@click.argument("files", nargs=-1, required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--extract", is_flag=True, help="Run fact extraction immediately after adding")
@click.option("--exclude", is_flag=True, help="Add as excluded by default")
def profile_add(profile_id: str, files: tuple[Path, ...], extract: bool, exclude: bool):
    """Add one or more files to a profile.

    PROFILE_ID: Target profile ID (see: hr-breaker profile list)
    FILES:      One or more file paths to add
    """
    from hr_breaker.services.profile_store import ProfileStore

    store = ProfileStore()
    p = store.get_profile(profile_id)
    if p is None:
        raise click.ClickException(f"Profile not found: {profile_id}")

    added_ids: list[str] = []
    for file_path in files:
        click.echo(f"  Adding {file_path.name}...", nl=False)
        try:
            doc = store.add_upload(
                profile_id,
                filename=file_path.name,
                data=file_path.read_bytes(),
                included_by_default=not exclude,
            )
            added_ids.append(doc.id)
            click.echo(f" ok ({doc.id[:12]})")
        except Exception as exc:
            click.echo(f" failed: {exc}")

    if extract and added_ids:
        click.echo("Extracting facts...")

        async def run_extract():
            for doc_id in added_ids:
                click.echo(f"  {doc_id[:12]}...", nl=False)
                try:
                    updated = await store.extract_document_content(profile_id, doc_id)
                    if updated is None:
                        click.echo(" missing")
                        continue
                    _print_extraction_result(updated)
                except Exception as exc:
                    click.echo(f" failed: {exc}")

        asyncio.run(run_extract())
    elif added_ids and not extract:
        click.echo(f"Tip: run 'hr-breaker backfill --profile {profile_id}' to extract facts.")


# ---------------------------------------------------------------------------
# optimize command
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("resume_path", required=False, type=click.Path(path_type=Path), default=None)
@click.argument("job_input", required=False, default=None)
@click.option(
    "--profile", "-p", "profile_id",
    default=None,
    help="Use a profile archive instead of a resume file.",
)
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
    "--debug/--no-debug",
    "-d/-D",
    default=True,
    help="Save all iterations as PDFs to output/debug/ (default: on)",
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
        ["from_job", "from_resume"] + [lang.code for lang in SUPPORTED_LANGUAGES],
        case_sensitive=False,
    ),
    default=None,
    help="Language mode: from_job (detect from job), from_resume (detect from resume), or ISO code.",
)
@click.option(
    "--instructions",
    "-i",
    type=str,
    default=None,
    help="Instructions for the optimizer (extra experience, emphasis areas)",
)
@click.option(
    "--docs",
    default=None,
    help="Comma-separated document IDs to include (profile mode only; default: all included_by_default)",
)
def optimize(
    resume_path: Path | None,
    job_input: str | None,
    profile_id: str | None,
    output: Path | None,
    max_iterations: int | None,
    debug: bool,
    seq: bool,
    no_shame: bool,
    lang: str | None,
    instructions: str | None,
    docs: str | None,
):
    """Optimize a resume for a job posting.

    Direct upload mode:

        hr-breaker optimize resume.txt https://example.com/job

    Profile archive mode:

        hr-breaker optimize --profile <id> https://example.com/job

    JOB_INPUT: URL or path to a file containing the job description.
    """
    if profile_id is None and resume_path is None:
        raise click.UsageError(
            "Provide either RESUME_PATH or --profile <id>.\n"
            "  Direct:  hr-breaker optimize resume.txt <job>\n"
            "  Profile: hr-breaker optimize --profile <id> <job>"
        )
    if profile_id is not None and resume_path is not None:
        raise click.UsageError("Cannot use both RESUME_PATH and --profile at the same time.")

    # Handle: hr-breaker optimize --profile id <job>  (job ends up in resume_path slot)
    effective_job_input = job_input
    if profile_id is not None and job_input is None and resume_path is not None:
        effective_job_input = str(resume_path)
        resume_path = None

    if effective_job_input is None:
        raise click.UsageError("JOB_INPUT is required (URL or path to job description).")

    if resume_path is not None and not resume_path.exists():
        raise click.ClickException(f"Resume file not found: {resume_path}")

    job_text = _get_job_text(effective_job_input)

    pdf_storage = PDFStorage()
    run_id = generate_run_id()
    debug_dir: Path | None = None

    def on_iteration(i, optimized, validation):
        status = "PASS" if validation.passed else "FAIL"
        scores = ", ".join(
            f"{r.filter_name}:{r.score:.2f}/{r.threshold:.2f}"
            for r in validation.results
            if not r.skipped
        )
        click.echo(f"  Iteration {i + 1}: {status} [{scores}]")

        if debug and debug_dir:
            if optimized.html:
                debug_html = debug_dir / f"iteration_{i + 1}.html"
                debug_html.write_text(optimized.html, encoding="utf-8")
            elif optimized.data:
                debug_json = debug_dir / f"iteration_{i + 1}.json"
                debug_json.write_text(optimized.data.model_dump_json(indent=2), encoding="utf-8")
            if optimized.pdf_bytes:
                debug_pdf = debug_dir / f"iteration_{i + 1}.pdf"
                debug_pdf.write_bytes(optimized.pdf_bytes)
                click.echo(f"    Debug: saved {debug_pdf}")
            else:
                click.echo("    Debug: no PDF (render failed)")

    settings = get_settings()
    lang_mode = lang or settings.default_language

    async def run_optimization():
        nonlocal debug_dir

        pre_parsed_job = None
        if profile_id is not None:
            source, pre_parsed_job = await _build_profile_source(profile_id, job_text, docs_filter=docs)
            first_name = source.first_name
            last_name = source.last_name
            resume_lang_code = source.language_code or "en"
            name_str = f"{first_name or ''} {last_name or ''}".strip()
            click.echo(f"Profile: {profile_id}" + (f"  ({name_str})" if name_str else ""))
        else:
            resume_content = load_resume_content(resume_path)
            first_name, last_name, resume_lang_code = await extract_name(resume_content)
            click.echo(f"Resume: {first_name or 'Unknown'} {last_name or ''} (lang: {resume_lang_code})")
            source = ResumeSource(
                content=resume_content,
                first_name=first_name,
                last_name=last_name,
                language_code=resume_lang_code,
            )

        job = pre_parsed_job or await parse_job_posting(job_text)
        click.echo(f"Job: {job.title} at {job.company} (lang: {job.language_code})")

        target_language = resolve_target_language(lang_mode, job.language_code, resume_lang_code)
        source_lang = get_language_safe(resume_lang_code)

        if debug:
            debug_dir = pdf_storage.generate_debug_dir(job.company, job.title, run_id=run_id)

        mode = "sequential" if seq else "parallel"
        shame_mode = " [no-shame]" if no_shame else ""
        click.echo(f"Optimizing (mode: {mode}{shame_mode}, target: {target_language.english_name})...")

        optimized, validation, _ = await optimize_for_job(
            source,
            max_iterations=max_iterations,
            on_iteration=on_iteration,
            job=job,
            parallel=not seq,
            no_shame=no_shame,
            user_instructions=instructions,
            language=target_language,
            source_language=source_lang,
        )
        return first_name, last_name, source, optimized, validation, job, target_language

    first_name, last_name, source, optimized, validation, job, target_language = asyncio.run(
        run_optimization()
    )
    lang_code = target_language.code

    if not validation.passed:
        click.echo("Warning: Not all filters passed")

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
                run_id=run_id,
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


async def _build_profile_source(
    profile_id: str,
    job_text: str,
    *,
    docs_filter: str | None,
) -> "tuple[ResumeSource, JobPosting]":
    """Rank profile documents against the job and return (ResumeSource, JobPosting)."""
    from hr_breaker.services.profile_store import ProfileStore
    from hr_breaker.services.profile_retrieval import rank_profile_documents, synthesize_profile_resume_source

    store = ProfileStore()
    p = store.get_profile(profile_id)
    if p is None:
        raise click.ClickException(f"Profile not found: {profile_id}")

    all_docs = store.list_documents(profile_id)
    if not all_docs:
        raise click.ClickException(
            f"Profile '{profile_id}' has no documents. "
            f"Add some with: hr-breaker profile add {profile_id} <file>"
        )

    if docs_filter:
        wanted = {d.strip() for d in docs_filter.split(",")}
        selected = [d for d in all_docs if d.id in wanted or d.id[:12] in wanted]
        if not selected:
            raise click.ClickException(f"No documents matched --docs filter: {docs_filter}")
    else:
        selected = [d for d in all_docs if d.included_by_default] or all_docs

    missing_extraction = [d.title for d in selected if document_needs_extraction(d)]
    if missing_extraction:
        click.echo(
            f"Warning: {len(missing_extraction)} document(s) have no extracted facts "
            f"and will be used as raw text: {', '.join(missing_extraction)}\n"
            f"  Run 'hr-breaker backfill --profile {profile_id}' to fix this."
        )

    job = await parse_job_posting(job_text)
    ranked = await rank_profile_documents(selected, job)
    return synthesize_profile_resume_source(p, selected, ranked), job


# ---------------------------------------------------------------------------
# backfill command
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--profile", "-p", "profile_id", default=None, help="Profile ID to backfill (default: all)")
@click.option("--force", is_flag=True, help="Re-extract even if extraction already exists")
def backfill(profile_id: str | None, force: bool):
    """Extract facts from profile documents that are missing extraction data."""
    from hr_breaker.services.profile_store import ProfileStore

    store = ProfileStore()
    if profile_id:
        p = store.get_profile(profile_id)
        if p is None:
            raise click.ClickException(f"Profile not found: {profile_id}")
        profiles = [p]
    else:
        profiles = store.list_profiles()

    if not profiles:
        click.echo("No profiles found.")
        return

    total = done = empty = failed = 0

    async def run():
        nonlocal total, done, empty, failed
        for p in profiles:
            docs = store.list_documents(p.id)
            pending = [d for d in docs if force or document_needs_extraction(d)]
            click.echo(f"Profile '{p.display_name}': {len(pending)}/{len(docs)} document(s) to process")
            for doc in pending:
                total += 1
                click.echo(f"  {doc.title}...", nl=False)
                try:
                    updated = await store.extract_document_content(p.id, doc.id)
                    if updated is None:
                        failed += 1
                        click.echo(" failed: document disappeared")
                        continue
                    result = _print_extraction_result(updated)
                    if result == "done":
                        done += 1
                    elif result == "empty":
                        empty += 1
                    else:
                        failed += 1
                except Exception as exc:
                    failed += 1
                    click.echo(f" failed: {exc}")

    asyncio.run(run())
    click.echo(f"\nDone: {done} extracted, {empty} empty, {failed} failed, {total} total")


# ---------------------------------------------------------------------------
# list command
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# serve command
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--port", "-p", type=int, default=8899, help="Port to serve on")
@click.option("--open/--no-open", default=True, help="Auto-open browser")
def serve(port: int, open: bool):
    """Start the web UI server."""
    url = f"http://localhost:{port}"
    click.echo(f"Starting HR-Breaker at {url}")

    if open:
        threading.Timer(1.5, webbrowser.open, args=[url]).start()

    uvicorn.run("hr_breaker.server:app", host="0.0.0.0", port=port, log_level="warning")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _get_job_text(job_input: str) -> str:
    """Get job text from URL or file path."""
    path = Path(job_input)
    if path.exists():
        return path.read_text(encoding="utf-8")

    if job_input.startswith(("http://", "https://")):
        try:
            return scrape_job_posting(job_input)
        except CloudflareBlockedError:
            click.echo("Site has bot protection. Opening in browser...")
            click.launch(job_input)
            click.echo("Please copy the job description and paste below.")
            click.echo("(Press Enter twice when done)")
            return _read_multiline_input()
        except ScrapingError as e:
            raise click.ClickException(str(e))

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
