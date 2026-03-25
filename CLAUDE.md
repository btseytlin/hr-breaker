# CLAUDE.md

# HR-Breaker

Tool for optimizing resumes for job postings and passing automated filters.

## How it works

1. User uploads resume in ANY text format (LaTeX, plain text, markdown, HTML) - content source only
2. User provides job posting URL or text description
3. LLM extracts content from resume and generates NEW HTML resume that:
   - Maximally fits the job posting
   - Follows guidelines: one-page PDF, no misinformation, etc.
   - Generated in target language (auto-detected from job/resume, or fixed)
4. System runs internal filters (LLM-based ATS simulation, keyword matching, hallucination detection, etc.)
5. If filters reject, repeat from step 3 using feedback
6. When all checks pass, render HTML→PDF via WeasyPrint and return

## Architecture

1. FastAPI backend (async-native, serves static files + API)
2. Alpine.js frontend (CDN-loaded, zero build steps, in `src/hr_breaker/static/`)
3. SSE (Server-Sent Events) for real-time optimization progress
4. Pydantic-AI LLM agent framework + pydantic-ai-litellm (any LLM provider)
5. Default: Google Gemini models (configurable to OpenAI, Anthropic, etc. via litellm)
6. Modular filter system - easy to add new checks
7. Resume caching - input once, apply to many jobs
8. Per-run settings overrides via UI (models, API keys, filter thresholds)

Python: 3.10–3.13
Package manager: uv
Always use venv: `source .venv/bin/activate`
Unit-tests: pytest
HTTP library: httpx

Pydantic-AI docs: https://ai.pydantic.dev/llms-full.txt
LiteLLM docs: https://docs.litellm.ai/docs/

## Guidelines

When debugging use 1-2 iterations only (costs money). Use these settings:
```
REASONING_EFFORT=low
PRO_MODEL=gemini/gemini-2.5-flash
FLASH_MODEL=gemini/gemini-2.5-flash
```

## Current Implementation

### Structure
```
src/hr_breaker/
├── models/          # Pydantic data models
├── agents/          # Pydantic-AI agents
├── filters/         # Plugin-based filter system
├── services/        # Rendering, scraping, caching
│   └── scrapers/    # Job scraper implementations
├── utils/           # Helpers (retry with backoff, HTML text extraction)
├── static/          # Frontend (Alpine.js + CSS + JS, served by FastAPI)
│   ├── index.html   # SPA: header + main area + settings drawer
│   ├── js/app.js    # Alpine.js state, SSE client, settings overrides
│   └── css/style.css
├── orchestration.py # Core optimization loop
├── server.py        # FastAPI app (API endpoints + SSE streaming)
├── cli.py           # Click CLI (optimize, list, serve)
├── config.py        # Settings (pydantic-settings BaseSettings, auto-reads env vars)
└── litellm_patch.py # Monkey-patch for pydantic-ai-litellm vision support
```

### Agents
- `job_parser` - Parse job posting → title, company, requirements, keywords, language_code
- `optimizer` - Generate optimized HTML resume from source + job
- `combined_reviewer` - Vision + ATS screening in single LLM call
- `name_extractor` - Extract name + language_code from any resume format (returns 3-tuple)
- `hallucination_detector` - Detect fabricated content
- `ai_generated_detector` - Detect AI-generated content indicators
- `translation_checker` - Evaluate translation quality for non-English resumes

### Filter System
Filters run by priority (lower first). Default: parallel execution. Use `--seq` for early exit on failure.

| Priority | Filter | Purpose |
|----------|--------|---------|
| 0 | ContentLengthChecker | Pre-render size check (fits in one page) |
| 1 | DataValidator | Validate HTML structure |
| 3 | HallucinationChecker | Detect fabricated claims not supported by original resume |
| 4 | KeywordMatcher | TF-IDF keyword matching |
| 5 | LLMChecker | Combined vision + ATS simulation |
| 6 | VectorSimilarityMatcher | Embedding similarity (via litellm) |
| 7 | AIGeneratedChecker | AI content detection |
| 8 | TranslationQualityChecker | Translation quality (skipped when source == target language) |

To add filter: subclass `BaseFilter`, set `name` and `priority`, use `@FilterRegistry.register`

### Services
- `renderer.py` - HTMLRenderer (WeasyPrint)
- `job_scraper.py` - Scrape job URLs (httpx → Wayback → Playwright fallback). 
- `pdf_parser.py` - Extract text from PDF
- `cache.py` - Resume + Job caching (file-based, mtime-ordered)
- `pdf_storage.py` - Save/list generated PDFs
- `length_estimator.py` - Content length estimation for resume sizing

### Commands
```bash
# Web UI (FastAPI + Alpine.js, auto-opens browser)
uv run hr-breaker serve                    # default: http://localhost:8899
uv run hr-breaker serve -p 3000            # custom port
uv run hr-breaker serve --no-open          # don't auto-open browser

# CLI
uv run hr-breaker optimize resume.txt https://example.com/job
uv run hr-breaker optimize resume.txt https://example.com/job -l ru        # force Russian output
uv run hr-breaker optimize resume.txt https://example.com/job -l from_job  # detect from job (default)
uv run hr-breaker optimize resume.txt job.txt -D              # disable debug mode (on by default)
uv run hr-breaker optimize resume.txt job.txt --seq           # sequential filters (early exit)
uv run hr-breaker optimize resume.txt job.txt --no-shame      # massively relax lies/hallucination/AI checks (use with caution!)
uv run hr-breaker optimize resume.txt job.txt --instructions "Focus on Python, add K8s cert"  # user instructions
uv run hr-breaker list                                        # list generated PDFs

# Tests
uv run pytest tests/
```

### Output
- Final PDFs: `output/<MMDD_HHMM>_<name>_<company>_<role>_<lang>.pdf` (run ID prefix for uniqueness)
- Debug iterations: `output/<MMDD_HHMM>_debug_<company>_<role>/` (with -d flag)
- Records: `output/index.json`

### Resume Rendering
- LLM generates HTML body → WeasyPrint renders to PDF
- Templates in `templates/` (resume_wrapper.html, resume_guide.md)
- Name extraction uses LLM - handles any input format

### pydantic-ai-litellm Vision Bug

`pydantic-ai-litellm` v0.2.3 does not support vision/`BinaryContent`. When an agent receives a list with text + `BinaryContent` (image), the library stringifies the image object (`str(item)`) instead of base64-encoding it into an OpenAI-compatible `image_url` part. The model receives garbage text like `"BinaryContent(data=b'\\x89PNG...')"` and never sees the actual image.

This breaks `combined_reviewer` which sends a rendered resume PNG for visual quality assessment.

Fix: `litellm_patch.py` monkey-patches `LiteLLMModel._map_messages` to properly convert `BinaryContent` images to `{"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}`. Applied at startup via `config.py`. Remove when upstream fixes the bug.

Repro: `uv run python scripts/repro_vision_bug.py` (without patch) vs `uv run python scripts/repro_vision_bug.py --patch` (with patch).

### UI Architecture
- Two-column layout: main area (left, max 900px) + sticky sidebar (right, 380px) with collapsible sections
- Sidebar sections: Run Options, Models, API Keys, Filter Thresholds, History
- **Profile-first resume UI**: profiles are the primary concept in the Resume card
  - Profile list shown directly (no tabs). Clicking a profile auto-synthesizes it as resume
  - Inline editor expands under profile item (edit icon) — shows docs, extraction badges, add note, re-extract
  - Document selection: all docs selected by default. "Customize" checkbox reveals per-doc checkboxes (only for 2+ docs)
  - `POST /api/profile/quick-create` — file or paste creates a profile + caches resume in one call
  - Legacy cached resumes (non-profile `source_type`) shown in separate "Previous uploads" section below profiles
  - Drop zone and paste toggle below profile list for quick profile creation
- Per-run overrides: frontend sends model/key/threshold overrides with optimize request
- Backend: `settings_override()` context manager temporarily sets env vars + clears `get_settings` cache for the duration of a run (safe since only one optimization runs at a time)
- API keys: never persisted to localStorage, only sent per-request. Backend only returns boolean "is set" status, never actual values
- Settings (models, thresholds, reasoning effort) prefilled from server defaults on load
- Only settings and drawer state persisted to localStorage (no job text, no API keys)

### Language Mode System
- Language is a **mode**, not a fixed code: `from_job` (default), `from_resume`, `en`, `ru`
- `from_job`/`from_resume` auto-detect language by piggybacking on existing LLM calls (job_parser, name_extractor)
- `resolve_target_language(mode, job_lang_code, resume_lang_code)` in `models/language.py` resolves mode → Language
- `get_language_safe(code)` returns English for unknown codes (never raises)
- Translation checker skips when source language == target language (not just when target is English)
- `source_language` is threaded through `orchestration.py` → `run_filters()` → all filter `evaluate()` calls

### Environment Variables

`Settings` uses `pydantic-settings` `BaseSettings` — env vars are auto-mapped from uppercased field names. All settings in `config.py` are configurable via env vars. See `.env.example` for the full list.

Key model config vars (litellm format):
- `PRO_MODEL` - Pro model (default: `gemini/gemini-3-pro-preview`)
- `FLASH_MODEL` - Flash model (default: `gemini/gemini-3-flash-preview`)
- `EMBEDDING_MODEL` - Embedding model (default: `openrouter/google/gemini-embedding-001`)
- `REASONING_EFFORT` - none/low/medium/high (default: `medium`)
- `GEMINI_API_KEY` - API key for Gemini (also accepts `GOOGLE_API_KEY` for backward compat)
- `RETRY_MAX_ATTEMPTS` - Max retry attempts for rate limits (default: `5`)
- `RETRY_MAX_WAIT` - Max backoff wait in seconds (default: `60`)

CLI options (settable via env vars, CLI flags override):
- `HR_BREAKER_OUTPUT` - output path
- `HR_BREAKER_MAX_ITERATIONS` - max optimization iterations
- `HR_BREAKER_DEBUG` - enable debug mode (true/1/yes)
- `HR_BREAKER_SEQ` - run filters sequentially (true/1/yes)
- `HR_BREAKER_NO_SHAME` - lenient mode (true/1/yes)

