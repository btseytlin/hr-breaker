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

from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse, StreamingResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pydantic_ai.exceptions import ModelHTTPError

from hr_breaker.agents import extract_name, parse_job_posting
from hr_breaker.config import get_settings, logger, settings_override
from hr_breaker.models import (
    GeneratedPDF,
    ResumeSource,
    LANGUAGE_MODES,
    get_language_safe,
    resolve_target_language,
 )
from hr_breaker.models.profile import ProfileDocument
from hr_breaker.orchestration import optimize_for_job
from hr_breaker.services import (
    PDFStorage,
    ResumeCache,
    JobCache,
    scrape_job_posting,
    ScrapingError,
    CloudflareBlockedError,
)
from hr_breaker.utils.optimization_telemetry import accumulate_usage_totals, telemetry_reporter, zero_usage_totals

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

class ProviderOverride(BaseModel):
    base_url: str | None = None


class LLMOverrideRequest(BaseModel):
    flash_model: str | None = None
    embedding_model: str | None = None
    reasoning_effort: str | None = None
    api_keys: dict[str, str] | None = None
    providers: dict[str, ProviderOverride] | None = None


class PasteResumeRequest(LLMOverrideRequest):
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
    providers: dict[str, ProviderOverride] | None = None
    filter_thresholds: dict[str, float] | None = None


class ProviderCheckRequest(BaseModel):
    provider: str  # gemini | openrouter | openai | custom
    api_key: str | None = None
    base_url: str | None = None


class QuickCreateProfileRequest(BaseModel):
    content: str | None = None


class CreateProfileRequest(BaseModel):
    name: str
    instructions: str | None = None


class ProfileActionRequest(LLMOverrideRequest):
    pass


class SynthesizeProfileRequest(ProfileActionRequest):
    job_text: str | None = None
    selected_doc_ids: list[str] | None = None


class AddNoteRequest(BaseModel):
    title: str
    content: str


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
async def upload_resume(
    file: UploadFile = File(...),
    flash_model: str | None = Form(None),
    reasoning_effort: str | None = Form(None),
    api_keys_json: str | None = Form(None),
    providers_json: str | None = Form(None),
):
    data = await file.read()
    try:
        content = load_resume_content_from_upload(file.filename, data)
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": f"Failed to read file: {e}"})

    try:
        req = _request_from_form(flash_model, reasoning_effort, api_keys_json, providers_json)
        with settings_override(_build_overrides(req)):
            first_name, last_name, language_code = await extract_name(content)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"Failed to extract name: {e}"})

    source = ResumeSource(content=content, first_name=first_name, last_name=last_name, language_code=language_code, filename=file.filename, source_type="upload")

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
        with settings_override(_build_overrides(req)):
            first_name, last_name, language_code = await extract_name(content)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"Failed to extract name: {e}"})

    source = ResumeSource(content=content, first_name=first_name, last_name=last_name, language_code=language_code, filename="pasted", source_type="paste")

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
            "source_type": r.source_type,
            "source_profile_id": r.source_profile_id,
            "source_profile_name": r.source_profile_name,
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


# --- Provider endpoints ---

@app.post("/api/providers/check")
async def check_provider(req: ProviderCheckRequest):
    from hr_breaker.services.llm_providers import fetch_provider_catalog
    result = await fetch_provider_catalog(
        provider=req.provider,
        api_key=req.api_key,
        base_url=req.base_url,
    )
    return result


# --- Profile endpoints ---

@app.get("/api/profile/")
async def list_profiles():
    from hr_breaker.services.profile_store import ProfileStore
    store = ProfileStore()
    profiles = store.list_profiles()
    return [
        {
            "id": p.id,
            "display_name": p.display_name or p.id,
            "document_count": len(store.list_documents(p.id)),
            "created_at": p.created_at.isoformat() if p.created_at else None,
            "updated_at": p.updated_at.isoformat() if p.updated_at else None,
        }
        for p in profiles
    ]


@app.post("/api/profile/quick-create")
async def quick_create_profile(
    file: UploadFile | None = File(None),
    content: str | None = Form(None),
    flash_model: str | None = Form(None),
    reasoning_effort: str | None = Form(None),
    api_keys_json: str | None = Form(None),
    providers_json: str | None = Form(None),
):
    """Create a profile from a single file or pasted text in one call."""
    from hr_breaker.services.profile_store import ProfileStore
    from hr_breaker.services.extraction_worker import extraction_worker

    if not file and not content:
        return JSONResponse(status_code=400, content={"error": "Provide either file or content"})
    if file and content:
        return JSONResponse(status_code=400, content={"error": "Provide file or content, not both"})

    store = ProfileStore()

    try:
        req = _request_from_form(flash_model, reasoning_effort, api_keys_json, providers_json)
        overrides = _build_overrides(req)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})

    if file:
        data = await file.read()
        filename = file.filename or "uploaded"
        try:
            raw_content = load_resume_content_from_upload(filename, data)
        except Exception as e:
            return JSONResponse(status_code=400, content={"error": f"Failed to read file: {e}"})

        # Sync name extraction for immediate display name
        try:
            with settings_override(overrides):
                first_name, last_name, language_code = await extract_name(raw_content)
        except ValueError as e:
            return JSONResponse(status_code=400, content={"error": str(e)})
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": f"Failed to extract name: {e}"})

        display_name = " ".join(filter(None, [first_name, last_name])) or filename
        profile = store.create_profile(display_name=display_name, first_name=first_name, last_name=last_name)
        try:
            doc = store.add_upload(profile.id, filename=filename, data=data)
        except Exception as e:
            return JSONResponse(status_code=400, content={"error": f"Failed to add document: {e}"})
    else:
        raw_content = (content or "").strip()
        if not raw_content:
            return JSONResponse(status_code=400, content={"error": "Empty content"})

        try:
            with settings_override(overrides):
                first_name, last_name, language_code = await extract_name(raw_content)
        except ValueError as e:
            return JSONResponse(status_code=400, content={"error": str(e)})
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": f"Failed to extract name: {e}"})

        display_name = " ".join(filter(None, [first_name, last_name])) or "Pasted resume"
        profile = store.create_profile(display_name=display_name, first_name=first_name, last_name=last_name)
        doc = store.add_note(profile.id, title=display_name, content_text=raw_content)

    # Start background extraction
    extraction_worker.submit(profile.id, [doc.id], overrides=overrides or None)

    # Also cache as ResumeSource for immediate use
    source = ResumeSource(
        content=raw_content,
        first_name=first_name,
        last_name=last_name,
        language_code=language_code,
        filename=file.filename if file else "pasted",
        source_type="profile",
        source_profile_id=profile.id,
        source_profile_name=profile.display_name,
    )
    ResumeCache().put(source)

    return {
        "profile_id": profile.id,
        "display_name": profile.display_name,
        "first_name": first_name,
        "last_name": last_name,
        "checksum": source.checksum,
        "document_count": 1,
    }


@app.post("/api/profile/")
async def create_profile(req: CreateProfileRequest):
    from hr_breaker.services.profile_store import ProfileStore
    store = ProfileStore()
    profile = store.create_profile(display_name=req.name, instructions=req.instructions)
    return {"id": profile.id, "display_name": profile.display_name}


@app.delete("/api/profile/{profile_id}")
async def delete_profile(profile_id: str):
    from hr_breaker.services.profile_store import ProfileStore
    store = ProfileStore()
    profile = store.get_profile(profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    store.delete_profile(profile_id)
    return {"ok": True}


@app.get("/api/profile/{profile_id}")
async def get_profile(profile_id: str):
    from hr_breaker.services.profile_store import ProfileStore
    from hr_breaker.services.extraction_worker import extraction_worker
    store = ProfileStore()
    profile = store.get_profile(profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    documents = store.list_documents(profile_id)
    docs = [
        {
            "id": d.id,
            "title": d.title,
            "kind": d.kind,
            "included_by_default": d.included_by_default,
            "extraction_status": d.metadata.get("extraction_status", "pending"),
        }
        for d in documents
    ]
    active_doc_ids = [
        d.id for d in documents
        if extraction_worker.get_status(d.id) in ("pending", "running")
    ]
    return {
        "id": profile.id,
        "name": profile.display_name,
        "instructions": profile.instructions,
        "documents": docs,
        "extracting": active_doc_ids,
    }


@app.post("/api/profile/{profile_id}/document")
async def add_profile_document(
    profile_id: str,
    file: UploadFile = File(...),
    flash_model: str | None = Form(None),
    reasoning_effort: str | None = Form(None),
    api_keys_json: str | None = Form(None),
    providers_json: str | None = Form(None),
):
    from hr_breaker.services.profile_store import ProfileStore
    from hr_breaker.services.extraction_worker import extraction_worker
    store = ProfileStore()
    profile = store.get_profile(profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    data = await file.read()
    try:
        doc = store.add_upload(profile_id, filename=file.filename, data=data)
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": f"Failed to read file: {e}"})

    try:
        overrides = _build_overrides(_request_from_form(flash_model, reasoning_effort, api_keys_json, providers_json))
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    extraction_worker.submit(profile_id, [doc.id], overrides=overrides or None)
    return {"id": doc.id, "title": doc.title, "kind": doc.kind}


@app.delete("/api/profile/{profile_id}/document/{doc_id}")
async def delete_profile_document(profile_id: str, doc_id: str):
    from hr_breaker.services.profile_store import ProfileStore
    from hr_breaker.services.extraction_worker import extraction_worker
    store = ProfileStore()
    doc = store.get_document(profile_id, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    extraction_worker.cancel([doc_id])
    store.remove_document(profile_id, doc_id)
    return {"ok": True}


@app.post("/api/profile/{profile_id}/note")
async def add_profile_note(profile_id: str, req: AddNoteRequest):
    from hr_breaker.services.profile_store import ProfileStore
    from hr_breaker.services.extraction_worker import extraction_worker
    store = ProfileStore()
    if not store.get_profile(profile_id):
        raise HTTPException(status_code=404, detail="Profile not found")
    doc = store.add_note(profile_id, title=req.title.strip(), content_text=req.content.strip())
    extraction_worker.submit(profile_id, [doc.id])
    return {"id": doc.id, "title": doc.title, "kind": doc.kind}


@app.get("/api/profile/{profile_id}/extraction-status")
async def profile_extraction_status(profile_id: str):
    from hr_breaker.services.profile_store import ProfileStore
    from hr_breaker.services.extraction_worker import extraction_worker
    store = ProfileStore()
    profile = store.get_profile(profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    documents = store.list_documents(profile_id)
    statuses = {
        d.id: extraction_worker.get_status(d.id) or d.metadata.get("extraction_status", "pending")
        for d in documents
    }
    logs = extraction_worker.drain_logs()
    active = any(s in ("pending", "running") for s in statuses.values())
    return {"statuses": statuses, "logs": logs, "active": active}


@app.post("/api/profile/{profile_id}/extract")
async def re_extract_profile(profile_id: str, req: ProfileActionRequest | None = None):
    """Re-submit all profile documents for background extraction."""
    from hr_breaker.services.profile_store import ProfileStore
    from hr_breaker.services.extraction_worker import extraction_worker
    store = ProfileStore()
    if not store.get_profile(profile_id):
        raise HTTPException(status_code=404, detail="Profile not found")
    documents = store.list_documents(profile_id)
    doc_ids = [d.id for d in documents]
    overrides = _build_overrides(req or ProfileActionRequest())
    extraction_worker.resubmit(profile_id, doc_ids, overrides=overrides or None)
    return {"submitted": len(doc_ids)}


@app.post("/api/profile/{profile_id}/synthesize")
async def synthesize_profile(profile_id: str, req: SynthesizeProfileRequest):
    """Synthesize profile into a ResumeSource, cache it, and return the checksum."""
    from hr_breaker.models.job_posting import JobPosting
    from hr_breaker.services.profile_retrieval import (
        rank_profile_documents,
        synthesize_profile_resume_source,
    )
    from hr_breaker.services.profile_store import ProfileStore
    store = ProfileStore()
    profile = store.get_profile(profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    all_documents = store.list_documents(profile_id)
    if not all_documents:
        return JSONResponse(status_code=400, content={"error": "Profile has no documents"})

    try:
        documents = _resolve_synthesis_documents(all_documents, req.selected_doc_ids)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})

    overrides = _build_overrides(req)
    try:
        with settings_override(overrides):
            job_text = req.job_text or ""
            job = JobPosting(title="", company="", raw_text=job_text, description=job_text)
            ranked = await rank_profile_documents(documents, job)
            source = synthesize_profile_resume_source(profile, documents, ranked).model_copy(update={"source_type": "profile", "source_profile_id": profile.id, "source_profile_name": profile.display_name})
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"Synthesis failed: {e}"})

    ResumeCache().put(source)
    return {
        "checksum": source.checksum,
        "first_name": source.first_name,
        "last_name": source.last_name,
    }


# --- Optimization ---

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


def _selected_optimization_models(req: OptimizeRequest) -> list[str]:
    return [model for model in (req.pro_model, req.flash_model, req.embedding_model) if model]


def _selected_custom_provider_bases(req: OptimizeRequest) -> list[str]:
    selected: list[str] = []
    for scope in ("pro", "flash", "embedding"):
        override = (req.providers or {}).get(scope)
        if override and override.base_url:
            selected.append(f"{scope}: {override.base_url}")
    return selected


def _normalize_optimization_error(exc: Exception, req: OptimizeRequest) -> str:
    if isinstance(exc, ModelHTTPError):
        text = str(exc)
        if (
            exc.status_code == 500
            and "APIConnectionError" in text
            and "argument of type 'NoneType' is not iterable" in text
        ):
            models = ", ".join(_selected_optimization_models(req)) or "the selected models"
            custom_bases = _selected_custom_provider_bases(req)
            if custom_bases:
                return (
                    f"Custom OpenAI-compatible provider request failed before the model returned a usable response. "
                    f"Check the base URL configuration ({'; '.join(custom_bases)}), API key, and that the endpoint supports models: {models}."
                )
            return (
                f"OpenAI-compatible provider request failed before the model returned a usable response. "
                f"Check the API key, provider configuration, and model compatibility for: {models}."
            )
    return str(exc)


def _broadcast(msg: str | None) -> None:
    """Send a message to all subscriber queues (for reconnected clients)."""
    global _active_optimization
    if _active_optimization is None:
        return
    for sub_queue in _active_optimization.get("subscribers", []):
        sub_queue.put_nowait(msg)


def _default_synthesis_documents(documents: list[ProfileDocument]) -> list[ProfileDocument]:
    selected = [document for document in documents if document.included_by_default]
    return selected or documents


def _resolve_synthesis_documents(
    documents: list[ProfileDocument],
    selected_doc_ids: list[str] | None,
 ) -> list[ProfileDocument]:
    if selected_doc_ids is None:
        return _default_synthesis_documents(documents)

    requested_ids = list(dict.fromkeys(selected_doc_ids))
    if not requested_ids:
        raise ValueError("Select at least one document for synthesis")

    documents_by_id = {document.id: document for document in documents}
    unknown_ids = [doc_id for doc_id in requested_ids if doc_id not in documents_by_id]
    if unknown_ids:
        raise ValueError("Unknown profile documents in synthesis scope")

    return [documents_by_id[doc_id] for doc_id in requested_ids]


def _build_overrides(req: BaseModel | None) -> dict:
    """Build settings override dict from request fields."""
    data = req.model_dump(exclude_none=True) if req is not None else {}
    overrides: dict = {}
    for field in ("pro_model", "flash_model", "embedding_model", "reasoning_effort", "api_keys"):
        if field in data:
            overrides[field] = data[field]
    for scope, field_name in (
        ("pro", "pro_openai_api_base"),
        ("flash", "flash_openai_api_base"),
        ("embedding", "embedding_openai_api_base"),
    ):
        provider_override = (data.get("providers") or {}).get(scope) or {}
        if provider_override.get("base_url"):
            overrides[field_name] = provider_override["base_url"]
    filter_thresholds = data.get("filter_thresholds")
    if filter_thresholds:
        threshold_map = {
            "hallucination": "filter_hallucination_threshold",
            "keyword": "filter_keyword_threshold",
            "llm": "filter_llm_threshold",
            "vector": "filter_vector_threshold",
            "ai_generated": "filter_ai_generated_threshold",
            "translation": "filter_translation_threshold",
        }
        for short_name, value in filter_thresholds.items():
            if short_name in threshold_map:
                overrides[threshold_map[short_name]] = value
    return overrides

def _request_from_form(
    flash_model: str | None,
    reasoning_effort: str | None,
    api_keys_json: str | None,
    providers_json: str | None,
    *,
    embedding_model: str | None = None,
    content: str | None = None,
    job_text: str | None = None,
    request_cls: type[LLMOverrideRequest] = ProfileActionRequest,
 ) -> LLMOverrideRequest:
    payload: dict = {}
    if flash_model:
        payload["flash_model"] = flash_model
    if embedding_model:
        payload["embedding_model"] = embedding_model
    if reasoning_effort:
        payload["reasoning_effort"] = reasoning_effort
    if api_keys_json:
        try:
            payload["api_keys"] = json.loads(api_keys_json)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid api_keys_json: {exc.msg}") from exc
    if providers_json:
        try:
            payload["providers"] = json.loads(providers_json)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid providers_json: {exc.msg}") from exc
    if content is not None:
        payload["content"] = content
    if job_text is not None:
        payload["job_text"] = job_text
    return request_cls(**payload)



async def _run_optimization(req: OptimizeRequest, source: ResumeSource):
    global _active_optimization
    overrides = _build_overrides(req)
    with settings_override(overrides):
        await _run_optimization_inner(req, source)


async def _run_optimization_inner(req: OptimizeRequest, source: ResumeSource):
    global _active_optimization

    usage_totals = zero_usage_totals()

    def emit_usage(entry: dict) -> None:
        nonlocal usage_totals
        usage_totals = accumulate_usage_totals(usage_totals, entry)
        _emit("usage", {"entry": entry, "totals": usage_totals})

    # Attach SSE log handler so backend logs stream to the UI
    sse_handler = _SSELogHandler()
    sse_handler.setFormatter(logging.Formatter("%(asctime)s.%(msecs)03d %(message)s", "%H:%M:%S"))
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

        with telemetry_reporter(emit_usage):
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
                if req.debug and debug_dir:
                    if optimized.html:
                        (debug_dir / f"iteration_{i + 1}.html").write_text(optimized.html, encoding="utf-8")
                    if optimized.pdf_bytes:
                        (debug_dir / f"iteration_{i + 1}.pdf").write_bytes(optimized.pdf_bytes)

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
        message = _normalize_optimization_error(e, req)
        logger.error("Optimization error: %s", message)
        logger.debug("Optimization error details", exc_info=True)
        _emit("error", {"message": message})
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
