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
- **User instructions** - Guide the optimizer with extra context ("Focus on Python", "Add K8s cert")
- **Multi-language output** - Optimize in English, then translate (e.g. `-l ru` for Russian)
- **Web UI + CLI** - Streamlit dashboard or command-line
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
# Edit .env and add your GOOGLE_API_KEY

# Run web UI
uv run streamlit run src/hr_breaker/main.py
```

## Usage

### Web UI

Launch with `uv run streamlit run src/hr_breaker/main.py`

1. Paste or upload resume
2. Enter job URL or description
3. Click optimize
4. Download PDF

### CLI

```bash
# From URL
uv run hr-breaker optimize resume.txt https://example.com/job

# From job description file
uv run hr-breaker optimize resume.txt job.txt

# Debug mode (saves iterations)
uv run hr-breaker optimize resume.txt job.txt -d

# User instructions - guide the optimizer
uv run hr-breaker optimize resume.txt job.txt -i "Focus on Python, add K8s cert"

# Translate output to another language
uv run hr-breaker optimize resume.txt https://example.com/job -l ru

# Lenient mode - relaxes content constraints but still prevents fabricating experience. Use with caution!
uv run hr-breaker optimize resume.txt job.txt --no-shame

# List generated PDFs
uv run hr-breaker list
```

### IDE (no API keys)

If you don’t have API access, you can still use HR-Breaker **inside your IDE** with only your IDE AI plugin (Copilot Chat / JetBrains AI Assistant / Continue / etc.).

1. Keep your resume anywhere and pass an explicit path (supports `.txt/.md/.tex/.pdf`)
2. Provide the job description in your IDE chat as **URL / Upload / Paste**
3. Run `optimize.md` (IDE prompt-pack entrypoint). It will:
   - Run local ingest (URL scrape + PDF→text): `uv run hr-breaker ide-ingest …` → `output/ide/resume_source.txt` + `output/ide/job_text.txt`
   - Use your IDE model to generate: `output/ide/job_posting.json` + `output/ide/resume_body.html`
   - Run local build + local-only validation: `uv run hr-breaker ide-sync …` → `output/ide/resume.pdf` + `output/ide/validation.md`
   - Run IDE LLM checks (`llm_checker`, `hallucination_checker`, `ai_generated_checker`, `vector_similarity_checker`) and auto-iterate until PASS or max iterations (agent mode)
4. If your IDE plugin can’t run shell commands, run the local commands once from terminal:
   - `uv sync && uv run hr-breaker ide-ingest --resume <resume-path-or-> --job <URL-or-output/ide/job_input.txt>`
   - `uv sync && uv run hr-breaker ide-sync --resume output/ide/resume_source.txt --min-keyword-score 0.55`

For strict IDE autopilot orchestration (state file + iteration logs), use:
- `ide/agents/full_stack_runner.md`

Full guide: `ide/README.md`

### Workflow Comparison Matrix

Use this matrix as the source of truth for feature parity across entrypoints.

| Step | Web UI (API) | CLI (API) | IDE plugin workflow (no HR-Breaker API keys required) |
|---|---|---|---|
| 0. Setup | `uv sync` + `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) → Streamlit | `uv sync` + `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) | `uv sync` only (for `ide-ingest`, `ide-sync`)<br>Models/API live in your IDE plugin |
| 1. Resume input | Upload/Paste (`.tex/.md/.txt/.pdf`) | Resume file path (`.tex/.md/.txt/.pdf`, etc.) | Pass explicit `--resume <path>` (or `--resume -` to paste text)<br>`ide-ingest` extracts PDF → `output/ide/resume_source.txt` |
| 2. Job input | URL or Paste | URL / file path / raw text (Cloudflare → paste fallback) | URL / file / Paste (`ide-ingest --job ...`, supports `--job -`)<br>URL scrape has copy/paste fallback when blocked |
| 3. Normalize inputs | In-app URL fetch + cleanup | In-command URL/file normalization (`optimize`) | `ide-ingest` writes normalized artifacts:<br>`output/ide/resume_source.txt`, `output/ide/job_text.txt` |
| 4. Job → JSON | Auto LLM parsing (`parse_job_posting`) | Auto LLM parsing (`parse_job_posting`) | Agent mode: automatic (`optimize.md`)<br>Manual: `ide/agents/job_parser.md` → `output/ide/job_posting.json` (must include `raw_text`; keep `keywords` tech/tools/methods only) |
| 5. Resume → HTML | LLM optimization + iterative feedback | LLM optimization + iterative feedback | Agent mode: automatic (`optimize.md`)<br>Manual: `ide/agents/optimizer.md` → `output/ide/resume_body.html` (HTML `<body>` only; drop Languages/Location/Hobbies by default) |
| 6. HTML → PDF + extracted text | Auto render each iteration | Auto render each iteration | `ide-sync` renders `output/ide/resume.pdf` + `output/ide/resume.txt` (per iteration or one-shot) |
| 7. Validation filters | Full filter stack (incl. LLM-based) | Full filter stack (incl. LLM-based) | Local-only filters in `ide-sync` + strict `IDEKeywordGate` (`--min-keyword-score`, default `0.55`)<br>LLM-based checks run via IDE prompts |
| 8. LLM semantic/style checks | Included in main run | Included in main run | Agent mode: automatic; otherwise run prompt-pack checkers:<br>`llm_checker`, `hallucination_checker`, `ai_generated_checker`, `vector_similarity_checker` |
| 9. Iterate until PASS | Auto reruns until PASS / max iterations | Auto reruns until PASS / max iterations | Agent mode auto-iterates; manual: edit `resume_body.html` → rerun `ide-sync` |
| 10. Artifacts | Final PDF download (+ debug if enabled) | Final PDF + optional `output/debug_*` + `output/index.json` | Explicit IDE artifacts in `output/ide/` + (agent mode) `output/ide/llm_checks/*` |
| 11. User instructions | Optional instructions field | `-i/--instructions` | Provide instruction text in IDE chat (used when generating HTML) |
| 12. Output language | Choose language (EN optimize → translate) | `-l/--lang` (EN optimize → translate) | Translation via IDE prompts (no built-in local command) |

## Output

- Final PDFs: `output/<name>_<company>_<role>.pdf`
- Debug iterations: `output/debug_<company>_<role>/`
- Records: `output/index.json`

## Configuration

Copy `.env.example` to `.env` and set `GOOGLE_API_KEY` (required). See `.env.example` for all available options.

---

## Architecture

```
src/hr_breaker/
├── agents/          # Pydantic-AI agents (optimizer, reviewer, etc.)
├── filters/         # Validation plugins (ATS, keywords, hallucination)
├── services/        # Rendering, scraping, caching
│   └── scrapers/    # Job scraper implementations
├── models/          # Pydantic data models
├── orchestration.py # Core optimization loop
├── main.py          # Streamlit UI
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

## Development

```bash
# Run tests
uv run pytest tests/

# Install dev dependencies
uv sync --group dev
```
