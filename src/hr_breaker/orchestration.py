"""Core optimization loop - used by both CLI and Streamlit."""

import asyncio
import tempfile
import time
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path

from hr_breaker.agents import optimize_resume, parse_job_posting, translate_resume, review_translation
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
    Language,
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
    language: Language | None = None,
    on_translation_status: Callable[[str], None] | None = None,
) -> tuple[OptimizedResume, ValidationResult, JobPosting]:
    """
    Core optimization loop.

    Args:
        source: Source resume
        job_text: Job posting text (required if job not provided)
        max_iterations: Max optimization iterations (default from settings)
        on_iteration: Optional callback(iteration, optimized, validation)
        job: Pre-parsed job posting (optional, skips parsing if provided)
        parallel: Run filters in parallel
        no_shame: Lenient mode
        language: Target language for resume output (None = English, no translation)
        on_translation_status: Optional callback(status_message) for translation progress

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

    # Post-processing: translate if target language is not English
    if language is not None and language.code != "en" and optimized is not None and optimized.html:
        optimized = await translate_and_rerender(
            optimized, language, job, renderer, settings.translation_max_iterations,
            on_translation_status,
        )

    return optimized, validation, job


async def translate_and_rerender(
    optimized: OptimizedResume,
    language: Language,
    job: JobPosting,
    renderer: HTMLRenderer | None = None,
    max_translation_iterations: int | None = None,
    on_status: Callable[[str], None] | None = None,
) -> OptimizedResume:
    """Translate the optimized resume HTML and re-render the PDF.

    Public API for translating an already-optimized resume.
    Runs a mini translate-review loop (max_translation_iterations) to ensure quality.
    """
    if renderer is None:
        renderer = HTMLRenderer()
    if max_translation_iterations is None:
        max_translation_iterations = get_settings().translation_max_iterations
    original_html = optimized.html
    feedback: str | None = None

    for i in range(max_translation_iterations):
        iter_label = f"Translation iteration {i + 1}/{max_translation_iterations}"
        logger.debug(f"{iter_label}: translating to {language.english_name}")
        if on_status:
            status = f"Translating to {language.english_name}..."
            if i > 0:
                status = f"Refining {language.english_name} translation (attempt {i + 1})..."
            on_status(status)

        with log_time(f"translate_resume (iter {i + 1})"):
            translation = await translate_resume(original_html, language, job, feedback=feedback)

        logger.debug(f"{iter_label}: reviewing translation")
        if on_status:
            on_status(f"Reviewing {language.english_name} translation...")

        with log_time(f"review_translation (iter {i + 1})"):
            review = await review_translation(original_html, translation.html, language, job)

        logger.debug(
            f"{iter_label}: review score={review.score:.2f}, passed={review.passed}"
        )

        if review.passed:
            logger.debug(f"Translation approved (score={review.score:.2f})")
            break

        # Build feedback for next iteration
        feedback_parts = []
        if review.issues:
            feedback_parts.append("Issues: " + "; ".join(review.issues))
        if review.suggestions:
            feedback_parts.append("Suggestions: " + "; ".join(review.suggestions))
        feedback = "\n".join(feedback_parts)
        logger.debug(f"Translation feedback: {feedback}")
    else:
        logger.warning(
            f"Translation review did not pass after {max_translation_iterations} iterations "
            f"(score={review.score:.2f}), using last translation"
        )

    # Update optimized with translated HTML and re-render PDF
    translated_optimized = optimized.model_copy(update={"html": translation.html})
    translated_optimized = _render_and_extract(translated_optimized, renderer)

    if on_status:
        on_status("Translation complete")

    return translated_optimized


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
