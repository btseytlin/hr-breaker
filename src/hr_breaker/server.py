"""FastAPI server for HR-Breaker web UI."""

import asyncio
import json
import logging
import os
import platform
import subprocess
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import FileResponse, StreamingResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from hr_breaker.agents import extract_name, parse_job_posting
from hr_breaker.config import get_settings, logger, settings_override
from hr_breaker.models import (
    GeneratedPDF,
    ResumeSource,
    LANGUAGE_MODES,
    get_language_safe,
    resolve_target_language,
)
from hr_breaker.orchestration import optimize_for_job
from hr_breaker.services import (
    PDFStorage,
    ResumeCache,
    JobCache,
    scrape_job_posting,
    ScrapingError,
    CloudflareBlockedError,
)
from hr_breaker.services.pdf_storage import generate_run_id
from hr_breaker.services.pdf_parser import load_resume_content_from_upload

STATIC_DIR = Path(__file__).parent / "static"


class _SSELogHandler(logging.Handler):
    """Logging handler that emits log records as SSE events."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            _emit("log", {
                "level": record.levelname,
                "message": self.format(record),
                "logger": record.name,
            })
        except Exception:
            self.handleError(record)

# NOTE: Module-level mutable state requires single-worker uvicorn (the default).
# Do not use multiple workers — _active_optimization is not shared across processes.

# Active optimization singleton — only one at a time
# When running: {"id": str, "task": asyncio.Task, "events": list[str], "subscribers": list[asyncio.Queue]}
_active_optimization: dict | None = None


app = FastAPI(title="HR-Breaker")


# --- API Models ---

class PasteResumeRequest(BaseModel):
    content: str


class ScrapeJobRequest(BaseModel):
    url: str


class PasteJobRequest(BaseModel):
    text: str


class OptimizeRequest(BaseModel):
    resume_checksum: str
    job_text: str
    sequential: bool = False
    debug: bool = True
    no_shame: bool = False
    language: str = "from_job"
    max_iterations: int | None = None
    instructions: str | None = None
    # Per-run overrides (None = use server defaults)
    pro_model: str | None = None
    flash_model: str | None = None
    embedding_model: str | None = None
    reasoning_effort: str | None = None
    api_keys: dict[str, str] | None = None
    filter_thresholds: dict[str, float] | None = None


# --- Endpoints ---

@app.get("/", response_class=HTMLResponse)
async def index():
    index_path = STATIC_DIR / "index.html"
    return HTMLResponse(index_path.read_text(encoding="utf-8"))


@app.get("/api/settings")
async def get_app_settings():
    settings = get_settings()
    return {
        "language_modes": LANGUAGE_MODES,
        "default_language": settings.default_language,
        "pro_model": settings.pro_model,
        "flash_model": settings.flash_model,
        "max_iterations": settings.max_iterations,
        "embedding_model": settings.embedding_model,
        "reasoning_effort": settings.reasoning_effort,
        "filter_thresholds": {
            "hallucination": settings.filter_hallucination_threshold,
            "keyword": settings.filter_keyword_threshold,
            "llm": settings.filter_llm_threshold,
            "vector": settings.filter_vector_threshold,
            "ai_generated": settings.filter_ai_generated_threshold,
            "translation": settings.filter_translation_threshold,
        },
        "api_keys_set": {
            "gemini": bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")),
            "openrouter": bool(os.environ.get("OPENROUTER_API_KEY")),
            "openai": bool(os.environ.get("OPENAI_API_KEY")),
            "anthropic": bool(os.environ.get("ANTHROPIC_API_KEY")),
            "moonshot": bool(os.environ.get("MOONSHOT_API_KEY")),
        },
    }


@app.post("/api/resume/upload")
async def upload_resume(file: UploadFile = File(...)):
    data = await file.read()
    try:
        content = load_resume_content_from_upload(file.filename, data)
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": f"Failed to read file: {e}"})

    try:
        first_name, last_name, language_code = await extract_name(content)
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"Failed to extract name: {e}"})

    source = ResumeSource(content=content, first_name=first_name, last_name=last_name, language_code=language_code, filename=file.filename)

    # Cache
    ResumeCache().put(source)

    return {
        "checksum": source.checksum,
        "first_name": first_name,
        "last_name": last_name,
    }


@app.post("/api/resume/paste")
async def paste_resume(req: PasteResumeRequest):
    content = req.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="Empty content")

    try:
        first_name, last_name, language_code = await extract_name(content)
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"Failed to extract name: {e}"})

    source = ResumeSource(content=content, first_name=first_name, last_name=last_name, language_code=language_code, filename="pasted")

    ResumeCache().put(source)

    return {
        "checksum": source.checksum,
        "first_name": first_name,
        "last_name": last_name,
    }


@app.post("/api/resume/select/{checksum}")
async def select_resume(checksum: str):
    ResumeCache().touch(checksum)
    return {"ok": True}


@app.delete("/api/resume/cached/{checksum}")
async def delete_cached_resume(checksum: str):
    ResumeCache().delete(checksum)
    return {"ok": True}


@app.get("/api/resume/cached")
async def cached_resumes():
    resumes = ResumeCache().list_all()
    return [
        {
            "checksum": r.checksum,
            "first_name": r.first_name,
            "last_name": r.last_name,
            "instructions": r.instructions,
            "filename": r.filename,
            "timestamp": r.timestamp.isoformat(),
        }
        for r in resumes
    ]


@app.get("/api/resume/{checksum}")
async def get_resume(checksum: str):
    source = ResumeCache().get(checksum)
    if not source:
        raise HTTPException(status_code=404, detail="Resume not found")
    return {"content": source.content}


@app.post("/api/job/scrape")
async def scrape_job(req: ScrapeJobRequest):
    try:
        text = scrape_job_posting(req.url)
        checksum = JobCache().put(text, source=req.url)
        return {"text": text, "checksum": checksum}
    except CloudflareBlockedError:
        return {"error": "cloudflare", "message": "Site has bot protection. Copy & paste instead."}
    except ScrapingError as e:
        return {"error": "scrape_failed", "message": str(e)}


@app.post("/api/job/paste")
async def paste_job(req: PasteJobRequest):
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Empty content")
    checksum = JobCache().put(text, source="pasted")
    return {"ok": True, "checksum": checksum}


@app.get("/api/job/cached")
async def cached_jobs():
    jobs = JobCache().list_all()
    return [
        {
            "checksum": j["checksum"],
            "preview": j["text"][:120].replace("\n", " "),
            "source": j.get("source"),
            "timestamp": j.get("timestamp"),
        }
        for j in jobs
    ]


@app.get("/api/job/{checksum}")
async def get_job(checksum: str):
    job = JobCache().get(checksum)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"text": job["text"]}


@app.post("/api/job/select/{checksum}")
async def select_job(checksum: str):
    JobCache().touch(checksum)
    return {"ok": True}


@app.delete("/api/job/cached/{checksum}")
async def delete_cached_job(checksum: str):
    JobCache().delete(checksum)
    return {"ok": True}


def _sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _emit(event: str, data: dict) -> None:
    """Record SSE event for replay and push to all subscriber queues."""
    global _active_optimization
    if _active_optimization is None:
        return
    msg = _sse_event(event, data)
    _active_optimization["events"].append(msg)
    _broadcast(msg)


def _cleanup_active() -> None:
    """Clear active optimization state."""
    global _active_optimization
    _active_optimization = None


@app.post("/api/optimize")
async def optimize_endpoint(req: OptimizeRequest):
    global _active_optimization

    # Concurrent prevention: reject if an optimization is already running
    if _active_optimization is not None and not _active_optimization["task"].done():
        return JSONResponse(
            status_code=409,
            content={"error": "Optimization already running", "id": _active_optimization["id"]},
        )

    # Clear stale completed optimization
    _cleanup_active()

    source = ResumeCache().get(req.resume_checksum)
    if not source:
        return JSONResponse(status_code=400, content={"error": "Resume not found. Upload or paste first."})

    opt_id = str(uuid.uuid4())
    events: list[str] = []

    _active_optimization = {"id": opt_id, "task": None, "events": events, "subscribers": []}

    task = asyncio.create_task(_run_optimization(req, source))
    _active_optimization["task"] = task

    # Return SSE stream (first event is 'started' with the id)
    return StreamingResponse(
        _sse_generator(opt_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _sse_generator(opt_id: str) -> AsyncGenerator[str, None]:
    """Stream events for the given optimization. Replays past events then streams live."""
    global _active_optimization
    if _active_optimization is None or _active_optimization["id"] != opt_id:
        return

    # Subscribe before taking the events snapshot to avoid missing events
    live_queue: asyncio.Queue = asyncio.Queue()
    _active_optimization.setdefault("subscribers", []).append(live_queue)

    try:
        # Replay already-accumulated events
        for evt in list(_active_optimization["events"]):
            yield evt

        # If task already done, no more live events
        if _active_optimization["task"] and _active_optimization["task"].done():
            return

        while True:
            msg = await live_queue.get()
            if msg is None:
                break
            yield msg
    finally:
        if _active_optimization and "subscribers" in _active_optimization:
            try:
                _active_optimization["subscribers"].remove(live_queue)
            except ValueError:
                pass


@app.get("/api/optimize/stream/{optimization_id}")
async def stream_optimization(optimization_id: str):
    """Reconnect to an active or completed optimization's SSE stream."""
    if _active_optimization is None or _active_optimization["id"] != optimization_id:
        return JSONResponse(status_code=404, content={"error": "Optimization not found"})

    return StreamingResponse(
        _sse_generator(optimization_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/optimize/cancel")
async def cancel_optimization():
    global _active_optimization

    if _active_optimization is None:
        return JSONResponse(status_code=404, content={"error": "No active optimization"})

    task = _active_optimization["task"]
    if not task.done():
        task.cancel()
        # Wait for cancellation to propagate
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    _cleanup_active()
    return {"ok": True}


@app.get("/api/optimize/status")
async def optimization_status():
    global _active_optimization

    if _active_optimization is None:
        return {"active": False, "id": None, "done": False}

    task_done = _active_optimization["task"].done() if _active_optimization["task"] else False
    return {
        "active": True,
        "id": _active_optimization["id"],
        "done": task_done,
    }


def _broadcast(msg: str | None) -> None:
    """Send a message to all subscriber queues (for reconnected clients)."""
    global _active_optimization
    if _active_optimization is None:
        return
    for sub_queue in _active_optimization.get("subscribers", []):
        sub_queue.put_nowait(msg)


def _build_overrides(req: OptimizeRequest) -> dict:
    """Build settings override dict from request fields."""
    overrides = {}
    if req.pro_model:
        overrides["pro_model"] = req.pro_model
    if req.flash_model:
        overrides["flash_model"] = req.flash_model
    if req.embedding_model:
        overrides["embedding_model"] = req.embedding_model
    if req.reasoning_effort:
        overrides["reasoning_effort"] = req.reasoning_effort
    if req.api_keys:
        overrides["api_keys"] = req.api_keys
    if req.filter_thresholds:
        threshold_map = {
            "hallucination": "filter_hallucination_threshold",
            "keyword": "filter_keyword_threshold",
            "llm": "filter_llm_threshold",
            "vector": "filter_vector_threshold",
            "ai_generated": "filter_ai_generated_threshold",
            "translation": "filter_translation_threshold",
        }
        for short_name, value in req.filter_thresholds.items():
            if short_name in threshold_map:
                overrides[threshold_map[short_name]] = value
    return overrides


async def _run_optimization(req: OptimizeRequest, source: ResumeSource):
    global _active_optimization
    overrides = _build_overrides(req)
    with settings_override(overrides):
        await _run_optimization_inner(req, source)


async def _run_optimization_inner(req: OptimizeRequest, source: ResumeSource):
    global _active_optimization

    # Attach SSE log handler so backend logs stream to the UI
    sse_handler = _SSELogHandler()
    sse_handler.setFormatter(logging.Formatter("%(message)s"))
    root_logger = logging.getLogger("hr_breaker")
    original_level = root_logger.level
    root_logger.addHandler(sse_handler)
    root_logger.setLevel(min(original_level, logging.INFO))

    try:
        opt_id = _active_optimization["id"]
        _emit("started", {"id": opt_id})

        settings = get_settings()
        pdf_storage = PDFStorage()
        run_id = generate_run_id()

        _emit("status", {"message": "Parsing job posting..."})

        job = await parse_job_posting(req.job_text)

        # Resolve language mode to concrete languages
        target_language = resolve_target_language(req.language, job.language_code, source.language_code)
        source_lang = get_language_safe(source.language_code)
        lang_code = target_language.code

        _emit("status", {
            "message": f"Job: {job.title} at {job.company} "
            f"(resume: {source_lang.english_name}, job: {get_language_safe(job.language_code).english_name}, "
            f"target: {target_language.english_name})"
        })

        # Setup debug dir
        debug_dir = None
        if req.debug:
            debug_dir = pdf_storage.generate_debug_dir(job.company, job.title, run_id=run_id)

        max_iterations = req.max_iterations or settings.max_iterations

        mode = "sequential" if req.sequential else "parallel"
        _emit("status", {"message": f"Optimizing (mode: {mode}, max: {max_iterations})..."})

        # Update instructions on source if provided
        if req.instructions:
            source = source.model_copy(update={"instructions": req.instructions})
            cache = ResumeCache()
            cache.put(source)

        def on_iteration(i, optimized, validation):
            # Save debug files
            if req.debug and debug_dir:
                if optimized.html:
                    (debug_dir / f"iteration_{i + 1}.html").write_text(optimized.html, encoding="utf-8")
                if optimized.pdf_bytes:
                    (debug_dir / f"iteration_{i + 1}.pdf").write_bytes(optimized.pdf_bytes)

            # Send iteration event
            results_data = [
                {
                    "filter_name": r.filter_name,
                    "passed": r.passed,
                    "score": r.score,
                    "threshold": r.threshold,
                    "skipped": r.skipped,
                    "issues": r.issues,
                    "suggestions": r.suggestions,
                }
                for r in validation.results
            ]
            _emit("iteration", {
                "iteration": i + 1,
                "max_iterations": max_iterations,
                "passed": validation.passed,
                "changes": optimized.changes,
                "results": results_data,
            })

        optimized, validation, _ = await optimize_for_job(
            source,
            max_iterations=max_iterations,
            on_iteration=on_iteration,
            job=job,
            parallel=not req.sequential,
            no_shame=req.no_shame,
            user_instructions=req.instructions,
            language=target_language,
            source_language=source_lang,
        )

        # Save PDF
        pdf_filename = None
        if optimized and optimized.pdf_bytes:
            pdf_path = pdf_storage.generate_path(
                source.first_name, source.last_name, job.company, job.title,
                lang_code=lang_code,
                run_id=run_id,
            )
            pdf_path.parent.mkdir(parents=True, exist_ok=True)
            pdf_path.write_bytes(optimized.pdf_bytes)
            pdf_filename = pdf_path.name

            pdf_record = GeneratedPDF(
                path=pdf_path,
                source_checksum=source.checksum,
                company=job.company,
                job_title=job.title,
                first_name=source.first_name,
                last_name=source.last_name,
            )
            pdf_storage.save_record(pdf_record)

        # Final results
        final_results = [
            {
                "filter_name": r.filter_name,
                "passed": r.passed,
                "score": r.score,
                "threshold": r.threshold,
                "skipped": r.skipped,
                "issues": r.issues,
                "suggestions": r.suggestions,
            }
            for r in validation.results
        ]
        _emit("complete", {
            "pdf_filename": pdf_filename,
            "passed": validation.passed,
            "validation": final_results,
            "job": {"title": job.title, "company": job.company},
        })

    except asyncio.CancelledError:
        _emit("cancelled", {"message": "Optimization cancelled by user"})
        raise
    except Exception as e:
        logger.exception("Optimization error")
        _emit("error", {"message": str(e)})
    finally:
        # Detach SSE log handler and restore original level
        root_logger.removeHandler(sse_handler)
        root_logger.setLevel(original_level)
        # Signal end of stream to all subscribers
        if _active_optimization is not None:
            _broadcast(None)


@app.get("/api/history")
async def list_history():
    pdf_storage = PDFStorage()
    pdfs = pdf_storage.list_all()
    return [
        {
            "filename": pdf.path.name,
            "company": pdf.company,
            "job_title": pdf.job_title,
            "timestamp": pdf.timestamp.isoformat(),
            "first_name": pdf.first_name,
            "last_name": pdf.last_name,
            "exists": pdf.path.exists(),
        }
        for pdf in pdfs
    ]


@app.get("/api/pdf/{filename}")
async def download_pdf(filename: str, inline: bool = False):
    settings = get_settings()
    pdf_path = (settings.output_dir / filename).resolve()
    if not pdf_path.is_relative_to(settings.output_dir.resolve()):
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="PDF not found")
    disposition = "inline" if inline else "attachment"
    return FileResponse(
        pdf_path,
        media_type="application/pdf",
        filename=filename,
        content_disposition_type=disposition,
    )


@app.post("/api/open-folder")
async def open_folder():
    settings = get_settings()
    folder = str(settings.output_dir.resolve())
    settings.output_dir.mkdir(parents=True, exist_ok=True)

    system = platform.system()
    if system == "Darwin":
        subprocess.Popen(["open", folder])
    elif system == "Windows":
        subprocess.Popen(["explorer", folder])
    else:
        subprocess.Popen(["xdg-open", folder])

    return {"ok": True}


# Mount static files LAST (catch-all)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
