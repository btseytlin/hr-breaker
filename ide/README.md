# IDE usage (no API keys)

This mode lets you use **HR-Breaker inside your IDE** with *only* your IDE AI plugin (Copilot Chat / JetBrains AI Assistant / Continue / etc.). No external API keys required.

You will:
1) provide a source resume (text or PDF) and a job description (URL/upload/paste)
2) use the prompt-pack to generate artifacts into `output/ide/`
3) run local rendering + local-only validation (no API)

## 0) One-time setup

- Keep your resume file in any local path (`.txt/.md/.tex/.pdf` supported).
- Create a workspace for job-specific artifacts:
  - `output/ide/` (already git-ignored because `output/` is ignored)

## 1) Ingest inputs (local, no API)

This step gives the IDE chat plugin clean text inputs (and enables URL scraping + PDF extraction).

Run:

```bash
uv run hr-breaker ide-ingest --resume <path-to-resume-file-or-> --job <URL-or-path-to-output/ide/job_input.txt>
# required: --resume <path-to-resume-file>
# optional: --resume -   (paste resume text in terminal)
# optional: --job -      (paste job text in terminal)
```

Outputs:
- `output/ide/resume_source.txt` (plain text resume)
- `output/ide/job_text.txt` (plain text job description)

Notes:
- If `--job` is a URL, HR-Breaker will auto-scrape it. If Cloudflare blocks scraping, it will ask you to copy/paste the job text (fallback).
- If your resume is a PDF, pass `--resume /path/to/resume.pdf`; text will be extracted automatically.

## 2) Parse job description (IDE chat)

Open `ide/agents/job_parser.md` and follow it to create (use `output/ide/job_text.txt` as input):
- `output/ide/job_posting.json`

## 3) Generate optimized HTML resume (IDE chat)

Open `ide/agents/optimizer.md` and follow it to create (use `output/ide/resume_source.txt` as source-of-truth):
- `output/ide/resume_body.html` (HTML for `<body>` only)

## Optional: single command `/optimize` (recommended)

If your IDE plugin supports agent mode (can read/write files and run shell commands), run:
- `optimize.md` (repo root)

This now runs the full chained workflow:
- ingest (`ide-ingest`)
- parse job to JSON
- generate HTML
- build (`ide-sync --min-keyword-score 0.55`)
- run IDE LLM checks (`llm_checker`, `hallucination_checker`, `ai_generated_checker`)
- auto-iterate until PASS (or max iterations)

For stricter orchestration with machine-readable loop state, use:
- `ide/agents/full_stack_runner.md`

Optional helper prompts:
- `ide/agents/optional/name_extractor.md`
- `ide/agents/optional/filter_result_schema.md`

Artifacts added by autopilot mode:
- `output/ide/llm_checks/llm_checker.json`
- `output/ide/llm_checks/hallucination_checker.json`
- `output/ide/llm_checks/ai_generated_checker.json`
- `output/ide/llm_checks/vector_similarity_checker.json`
- `output/ide/llm_checks/summary.md`

## 4) Build locally (no API)

Run from terminal:

```bash
uv run hr-breaker ide-sync --resume output/ide/resume_source.txt --min-keyword-score 0.55
```

Artifacts are written to `output/ide/`:
- `resume.pdf` (open in IDE)
- `resume.txt` (ATS-friendly text for IDE chat)
- `validation.md` / `validation.json` (local filter feedback)

Strict parity gate:
- `ide-sync` adds a synthetic `IDEKeywordGate` result.
- Default target is `0.55`; override with `--min-keyword-score <0..1>`.
- Build fails if this gate fails.

## 5) LLM-based checks (IDE chat)

In `/optimize` agent mode these checks are executed automatically each iteration.
Manual fallback: run them yourself in IDE chat:
- `ide/agents/llm_checker.md` (ATS + formatting review; use `output/ide/resume.txt` and open `output/ide/resume.pdf`)
- `ide/agents/hallucination_checker.md` (fabrication check vs `output/ide/resume_source.txt`)
- `ide/agents/ai_generated_checker.md` (“AI-sounding” / contradictions check)
- `ide/agents/vector_similarity_checker.md` (semantic alignment to job posting; `VectorSimilarityMatcher` parity)

## Iteration loop

- In `/optimize` agent mode: automatic reruns until PASS or max iterations.
- In basic chat mode: repeat manually until satisfied:
  1) refine `output/ide/resume_body.html` using failed checks (smallest changes)
  2) run **IDE Sync**
  3) redo LLM-based checks in chat
