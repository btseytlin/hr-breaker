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
# Edit .env and add your GOOGLE_API_KEY (for Gemini) or MOONSHOT_API_KEY (for Moonshot AI)

# Run web UI
uv run streamlit run src/hr_breaker/main.py
```

### Using Moonshot AI (Kimi)

To use Moonshot AI instead of Gemini:

1. Get a Moonshot AI API key from https://platform.moonshot.ai/
2. Set `MOONSHOT_API_KEY` in your `.env` file
3. Configure models to use Moonshot:
   ```bash
   PRO_MODEL=moonshot/kimi-k2-5
   FLASH_MODEL=moonshot/kimi-k2-5
   ```

Moonshot AI models work via LiteLLM — see [LiteLLM Moonshot docs](https://docs.litellm.ai/docs/providers/moonshot) for available models.

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

## Output

- Final PDFs: `output/<name>_<company>_<role>.pdf`
- Debug iterations: `output/debug_<company>_<role>/`
- Records: `output/index.json`

## Configuration

Copy `.env.example` to `.env` and set `GOOGLE_API_KEY` (for Gemini) or `MOONSHOT_API_KEY` (for Moonshot AI). See `.env.example` for all available options.

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
