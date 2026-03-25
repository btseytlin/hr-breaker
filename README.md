# HR-Breaker

Resume optimization tool that transforms any resume into a job-specific, ATS-friendly PDF.

![Python 3.10–3.13](https://img.shields.io/badge/python-3.10--3.13-blue.svg)

## Features

- **Any format in** - LaTeX, plain text, markdown, HTML, PDF
- **Optimized PDF out** - Single-page, professionally formatted
- **LLM-powered optimization** - Tailors content to job requirements
- **Minimal changes** - Preserves your content, only restructures for fit
- **No fabrication** - Hallucination detection prevents made-up claims
- **Opinionated formatting** - Follows proven resume guidelines (one page, no fluff, etc.)
- **Multi-filter validation** - ATS simulation, keyword matching, structure checks
- **Profile system** - Group multiple resume documents into profiles with LLM-powered extraction and synthesis
- **User instructions** - Guide the optimizer with extra context ("Focus on Python", "Add K8s cert")
- **Multi-language output** - Auto-detect language from job/resume, or force a specific language (e.g. `-l ru`)
- **Web UI + CLI** - FastAPI + Alpine.js web app or command-line
- **Debug mode** - Inspect optimization iterations
- **Cross-platform** - Works on macOS, Linux, and Windows

## How It Works

1. Upload resume in any text format (content source only)
2. Provide job posting URL or text description
3. LLM extracts content and generates optimized HTML resume
4. System runs internal filters (ATS simulation, keyword matching, hallucination detection)
5. If filters reject, regenerates using feedback
6. When all checks pass, renders HTML→PDF via WeasyPrint

## Quick Start

```bash
# Install
uv sync

# Configure
cp .env.example .env
# Edit .env and add your GEMINI_API_KEY (or configure another LLM provider)

# Run web UI (auto-opens browser at http://localhost:8899)
uv run hr-breaker serve
```

## Usage

### Web UI

Launch with `uv run hr-breaker serve` (default: http://localhost:8899)

1. Drop a file or paste text to create a resume profile (or click an existing one)
2. Enter job URL or paste description
3. Configure settings in sidebar (models, API keys, filter thresholds)
4. Click optimize — real-time progress via SSE
5. Preview and download PDF

Profiles group your resume documents — drop a file to auto-create a profile, or create one manually and add multiple documents. Click a profile to synthesize it into a resume, or click the edit icon to manage documents.

### CLI

```bash
# From URL
uv run hr-breaker optimize resume.txt https://example.com/job

# From job description file
uv run hr-breaker optimize resume.txt job.txt

# Debug mode (saves iterations) — on by default, -D to disable
uv run hr-breaker optimize resume.txt job.txt -D

# User instructions - guide the optimizer
uv run hr-breaker optimize resume.txt job.txt -i "Focus on Python, add K8s cert"

# Language modes: from_job (default), from_resume, en, ru, etc.
uv run hr-breaker optimize resume.txt https://example.com/job -l ru
uv run hr-breaker optimize resume.txt https://example.com/job -l from_job

# Lenient mode - relaxes content constraints but still prevents fabricating experience. Use with caution!
uv run hr-breaker optimize resume.txt job.txt --no-shame

# List generated PDFs
uv run hr-breaker list
```

## Output

- Final PDFs: `output/<MMDD_HHMM>_<name>_<company>_<role>_<lang>.pdf`
- Debug iterations: `output/<MMDD_HHMM>_debug_<company>_<role>/`
- Records: `output/index.json`

## Configuration

Copy `.env.example` to `.env` and set `GEMINI_API_KEY` (or `GOOGLE_API_KEY`). Models are configurable via LiteLLM — any provider (OpenAI, Anthropic, Moonshot, etc.) works by setting `PRO_MODEL`, `FLASH_MODEL`, and the corresponding API key. See `.env.example` for all options.

---

## Architecture

```
src/hr_breaker/
├── agents/          # Pydantic-AI agents (optimizer, reviewer, etc.)
├── filters/         # Validation plugins (ATS, keywords, hallucination)
├── services/        # Rendering, scraping, caching, profiles
│   └── scrapers/    # Job scraper implementations
├── models/          # Pydantic data models
├── static/          # Frontend (Alpine.js + CSS + JS)
├── orchestration.py # Core optimization loop
├── server.py        # FastAPI app (API + SSE streaming)
└── cli.py           # Click CLI
```

**Filters** (run by priority):

- 0: ContentLengthChecker - Size check
- 1: DataValidator - HTML structure validation
- 3: HallucinationChecker - Detect fabricated claims not supported by original resume
- 4: KeywordMatcher - TF-IDF matching
- 5: LLMChecker - Visual formatting check and LLM-based ATS simulation
- 6: VectorSimilarityMatcher - Semantic similarity
- 7: AIGeneratedChecker - Detect AI-sounding text
- 8: TranslationQualityChecker - Translation quality (skipped when source == target language)

## Development

```bash
# Run tests
uv run pytest tests/

# Install dev dependencies
uv sync --group dev
```
