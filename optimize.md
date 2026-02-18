# `/optimize` (IDE full workflow entrypoint)

Use this prompt when your IDE chat plugin can act as an agent (read/write files and run shell commands).
It executes the full no-API-key workflow end to end:

1. ingest inputs
2. parse job text to structured JSON
3. generate optimized HTML
4. render PDF and run local filters
5. run LLM-based checks inside IDE chat
6. iterate automatically until PASS or max iterations

If your plugin cannot run commands, use manual fallback at the end of this file.

## Inputs

- Resume source: explicit file path or upload/paste (including PDF)
- Job source: URL, upload, or pasted text
- HTML guide: `templates/resume_guide.md`

## Required outputs

- `output/ide/resume_source.txt`
- `output/ide/job_text.txt`
- `output/ide/job_posting.json`
- `output/ide/resume_body.html`
- `output/ide/resume.pdf`
- `output/ide/resume.txt`
- `output/ide/validation.md`
- `output/ide/validation.json`
- `output/ide/llm_checks/llm_checker.json`
- `output/ide/llm_checks/hallucination_checker.json`
- `output/ide/llm_checks/ai_generated_checker.json`
- `output/ide/llm_checks/vector_similarity_checker.json`
- `output/ide/llm_checks/summary.md`

## Non-negotiable rules

- Do not fabricate: no new companies, titles, dates, degrees, certifications, metrics.
- Preserve contact links and emails from source resume.
- `resume_body.html` must contain only HTML body content (no `<html>`, `<head>`, `<body>` tags).
- Follow `templates/resume_guide.md` classes and structure.
- Avoid generic filler and repeated buzzword text.
- Default section policy: exclude `Languages`, `Location`, and `Hobbies` unless the job explicitly requires them.

## Pass criteria

Treat iteration as PASS only if all conditions are true:

- Local validation is PASS (`output/ide/validation.md` contains `Overall: PASS`)
- Strict keyword parity gate passes (`IDEKeywordGate`, score `>= 0.55`)
- `LLMChecker` result has `"passed": true`
- `HallucinationChecker` result has `"passed": true`
- `AIGeneratedChecker` result has `"passed": true`
- `VectorSimilarityMatcher` result has `"passed": true`

## Workflow

1. Prepare inputs and run ingest.
   - If resume was pasted, save as `output/ide/resume_input.txt` (or keep in memory and pass `--resume -`).
   - If resume was uploaded as PDF, save to any local path (for example `input/resume.pdf`).
   - If job text was pasted/uploaded, save as `output/ide/job_input.txt`.
   - Run:
     - `uv sync && uv run hr-breaker ide-ingest --resume <path-or-> --job <URL-or-output/ide/job_input.txt>`

2. Parse `output/ide/job_text.txt` into `output/ide/job_posting.json`.
   - Use schema from `ide/agents/job_parser.md`.
   - Include full source text in `raw_text`.

3. Run iterative optimization loop with max 5 iterations.
   - Iteration 1:
     - Generate `output/ide/resume_body.html` from:
       - `output/ide/resume_source.txt`
       - `output/ide/job_posting.json`
       - `templates/resume_guide.md`
   - Iteration >1:
     - Edit existing `output/ide/resume_body.html` with smallest possible changes based on failed checks only.

4. Build local artifacts each iteration.
   - Run:
     - `uv sync && uv run hr-breaker ide-sync --resume output/ide/resume_source.txt --min-keyword-score 0.55`
   - This must refresh:
     - `output/ide/resume.pdf`
     - `output/ide/resume.txt`
     - `output/ide/validation.md`
     - `output/ide/validation.json`

5. Run LLM checks each iteration (inside IDE chat model).
   - Produce `FilterResult` JSON files:
     - `output/ide/llm_checks/llm_checker.json` using `ide/agents/llm_checker.md`
     - `output/ide/llm_checks/hallucination_checker.json` using `ide/agents/hallucination_checker.md`
     - `output/ide/llm_checks/ai_generated_checker.json` using `ide/agents/ai_generated_checker.md`
     - `output/ide/llm_checks/vector_similarity_checker.json` using `ide/agents/vector_similarity_checker.md`
   - Write `output/ide/llm_checks/summary.md` with:
     - current iteration number
     - PASS/FAIL for local validation + each LLM check
     - top blocking issues and concrete fix plan

6. Gate and rerun.
   - If pass criteria are met, stop and report final PASS.
   - If not met and iteration <5:
     - inspect `output/ide/validation.json`
     - prioritize unresolved missing keywords from `KeywordMatcher` / `IDEKeywordGate`
     - make minimal edits to `output/ide/resume_body.html` and rerun from step 4.
   - If iteration reaches 5 without PASS, stop and report blockers.

## If this plugin cannot run commands

Stop after generating `output/ide/job_posting.json` and `output/ide/resume_body.html`.
Ask user to run:

- `uv sync && uv run hr-breaker ide-ingest --resume <path-or-> --job <...>`
- `uv sync && uv run hr-breaker ide-sync --resume output/ide/resume_source.txt --min-keyword-score 0.55`

Then continue from `validation.md` + `resume.txt` and perform LLM checks/refinement in chat.
