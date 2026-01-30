"""Core optimization loop - used by both CLI and Streamlit."""

import asyncio
import tempfile
import time
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path

from hr_breaker.agents import optimize_resume, parse_job_posting
from hr_breaker.config import get_settings, logger
from hr_breaker.filters import (
    LLMChecker,
    DataValidator,
    FilterRegistry,
    HallucinationChecker,
    KeywordMatcher,
    VectorSimilarityMatcher,
)
from hr_breaker.models import (
    FilterResult,
    IterationContext,
    JobPosting,
    OptimizedResume,
    ResumeSource,
    ValidationResult,
)
from hr_breaker.services.pdf_parser import extract_text_from_pdf
from hr_breaker.services.renderer import RenderError, HTMLRenderer

# Ensure filters are registered
_ = DataValidator, LLMChecker, KeywordMatcher, VectorSimilarityMatcher, HallucinationChecker


@contextmanager
def log_time(operation: str):
    start = time.perf_counter()
    yield
    elapsed = time.perf_counter() - start
    logger.debug(f"{operation}: {elapsed:.2f}s")


async def run_filters(
    optimized: OptimizedResume,
    job: JobPosting,
    source: ResumeSource,
    parallel: bool = False,
    no_shame: bool = False,
) -> ValidationResult:
    """Run filters, either sequentially (early exit) or in parallel."""
    filters = FilterRegistry.all()

    if parallel:
        # Run all filters concurrently
        start = time.perf_counter()
        filter_instances = [filter_cls(no_shame=no_shame) for filter_cls in filters]
        tasks = [f.evaluate(optimized, job, source) for f in filter_instances]
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)
        logger.debug(f"All filters (parallel): {time.perf_counter() - start:.2f}s")

        # Convert exceptions to failed FilterResults
        results = []
        for f, result in zip(filter_instances, raw_results):
            if isinstance(result, Exception):
                logger.error(f"Filter {f.name} raised exception: {result}")
                results.append(FilterResult(
                    filter_name=f.name,
                    passed=False,
                    score=0.0,
                    threshold=getattr(f, 'threshold', 0.5),
                    issues=[f"Filter error: {type(result).__name__}: {result}"],
                    suggestions=["Check filter implementation"],
                ))
            else:
                results.append(result)
        return ValidationResult(results=results)

    # Sequential mode: sorted by priority, early exit on failure
    results = []
    filters = sorted(filters, key=lambda f: f.priority)

    for filter_cls in filters:
        # Skip high-priority (last) filters if earlier ones failed
        if filter_cls.priority >= 100 and results and not all(r.passed for r in results):
            continue

        f = filter_cls(no_shame=no_shame)
        start = time.perf_counter()
        result = await f.evaluate(optimized, job, source)
        logger.debug(f"{filter_cls.name}: {time.perf_counter() - start:.2f}s")
        results.append(result)

        # Early exit on failure (unless it's a final check)
        if not result.passed and filter_cls.priority < 100:
            break

    return ValidationResult(results=results)


async def optimize_for_job(
    source: ResumeSource,
    job_text: str | None = None,
    max_iterations: int | None = None,
    on_iteration: Callable | None = None,
    job: JobPosting | None = None,
    parallel: bool = False,
    no_shame: bool = False,
) -> tuple[OptimizedResume, ValidationResult, JobPosting]:
    """
    Core optimization loop.

    Args:
        source: Source resume
        job_text: Job posting text (required if job not provided)
        max_iterations: Max optimization iterations (default from settings)
        on_iteration: Optional callback(iteration, optimized, validation)
        job: Pre-parsed job posting (optional, skips parsing if provided)

    Returns:
        (optimized_resume, validation_result, job_posting)
    """
    settings = get_settings()
    if max_iterations is None:
        max_iterations = settings.max_iterations

    renderer = HTMLRenderer()

    if job is None:
        if job_text is None:
            raise ValueError("Either job_text or job must be provided")
        with log_time("parse_job_posting"):
            job = await parse_job_posting(job_text)
    optimized = None
    validation = None
    last_attempt: str | None = None

    if no_shame:
        logger.debug("No-shame mode enabled")

    for i in range(max_iterations):
        logger.debug(f"Iteration {i + 1}/{max_iterations}")
        ctx = IterationContext(
            iteration=i,
            original_resume=source.content,
            last_attempt=last_attempt,
            validation=validation,
        )
        with log_time("optimize_resume"):
            optimized = await optimize_resume(source, job, ctx, no_shame=no_shame)
        logger.debug(f"Optimizer changes: {optimized.changes}")
        # Store last attempt for feedback (html or data depending on mode)
        last_attempt = optimized.html if optimized.html else (
            optimized.data.model_dump_json() if optimized.data else None
        )

        # Render PDF and extract text for filters (like real ATS)
        optimized = _render_and_extract(optimized, renderer)

        if optimized.pdf_text is None:
            # PDF rendering failed - treat as validation failure
            validation = ValidationResult(
                results=[
                    FilterResult(
                        filter_name="PDFRender",
                        passed=False,
                        score=0.0,
                        threshold=1.0,
                        issues=["Failed to render resume to PDF"],
                        suggestions=["Check resume data structure"],
                    )
                ]
            )
        else:
            validation = await run_filters(optimized, job, source, parallel=parallel, no_shame=no_shame)

        if on_iteration:
            on_iteration(i, optimized, validation)

        if validation.passed:
            break

    return optimized, validation, job


def _render_and_extract(optimized: OptimizedResume, renderer) -> OptimizedResume:
    """Render PDF and extract text, updating the OptimizedResume."""
    try:
        with log_time("render_pdf"):
            # Use html if available, otherwise fall back to data (legacy)
            if optimized.html is not None:
                result = renderer.render(optimized.html)
            elif optimized.data is not None:
                result = renderer.render_data(optimized.data)
            else:
                raise RenderError("No content to render (neither html nor data)")

        # Extract text from rendered PDF
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(result.pdf_bytes)
            pdf_path = Path(f.name)

        try:
            with log_time("extract_text_from_pdf"):
                pdf_text = extract_text_from_pdf(pdf_path)
        finally:
            pdf_path.unlink()

        return optimized.model_copy(
            update={"pdf_text": pdf_text, "pdf_bytes": result.pdf_bytes}
        )
    except RenderError as e:
        logger.error(f"Render error: {e}")
        return optimized
