# Command: `/full-stack-runner` (IDE autopilot orchestrator)

Use this when your IDE chat plugin can:
- read and write workspace files
- run shell commands

Goal: run the full HR-Breaker IDE workflow with explicit gates and machine-readable progress logs.

## Inputs

- Resume input:
  - explicit local path provided by user
  - or pasted/uploaded text/PDF from user
- Job input:
  - URL, uploaded file, or pasted text
- Optional user instruction:
  - extra emphasis like "focus on backend", "highlight Python", etc.

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
- `output/ide/runner_state.json`
- `output/ide/llm_checks/iteration_1.json` .. `iteration_N.json`
- `output/ide/llm_checks/final_report.md`

## Hard rules

- Do not fabricate experience, education, certifications, dates, companies, titles, or metrics.
- Preserve contact details from source resume.
- Keep `output/ide/resume_body.html` as HTML body content only (no `html/head/body` wrapper tags).
- On retries, make the smallest possible edits.
- Default section policy: exclude `Languages`, `Location`, and `Hobbies` unless explicitly required by the job posting.

## Loop settings

- `MAX_ITERATIONS = 5`
- Stop early on PASS.
- Stop on hard blocker (missing required inputs, repeated command failure, invalid JSON after correction attempt).

## Pass criteria

Iteration is PASS only if all are true:
- `output/ide/validation.md` contains `Overall: PASS`
- `IDEKeywordGate` passes with score `>= 0.55`
- `output/ide/llm_checks/llm_checker.json` has `"passed": true`
- `output/ide/llm_checks/hallucination_checker.json` has `"passed": true`
- `output/ide/llm_checks/ai_generated_checker.json` has `"passed": true`
- `output/ide/llm_checks/vector_similarity_checker.json` has `"passed": true`

## Execution flow

1. Bootstrap workspace state.
   - Ensure dirs exist:
     - `output/ide/`
     - `output/ide/llm_checks/`
   - Initialize `output/ide/runner_state.json`:

```json
{
  "status": "running",
  "current_iteration": 0,
  "max_iterations": 5,
  "last_error": "",
  "final_passed": false
}
```

2. Normalize inputs and run ingest.
   - If resume was pasted, save `output/ide/resume_input.txt` (or pass `--resume -`).
   - If resume was uploaded as PDF, save to any local path (for example `input/resume.pdf`).
   - If job was pasted/uploaded, save `output/ide/job_input.txt`.
   - Run:
     - `uv sync && uv run hr-breaker ide-ingest --resume <path-or-> --job <URL-or-output/ide/job_input.txt>`

3. Parse job text.
   - Open `ide/agents/job_parser.md`.
   - Build `output/ide/job_posting.json` from `output/ide/job_text.txt`.
   - Validate JSON format before writing.

4. Iteration loop (`i = 1..MAX_ITERATIONS`).

   For each iteration:

   1. Update `runner_state.json` with `current_iteration = i`.
   2. Generate or refine `output/ide/resume_body.html`:
      - Iteration 1: create from source resume + job JSON.
      - Iteration >1: minimal edits based on failed checks only.
   3. Build and local validation:
      - Run: `uv sync && uv run hr-breaker ide-sync --resume output/ide/resume_source.txt --min-keyword-score 0.55`
   4. Run IDE LLM checks and save exact `FilterResult` JSON:
      - `ide/agents/llm_checker.md` -> `output/ide/llm_checks/llm_checker.json`
      - `ide/agents/hallucination_checker.md` -> `output/ide/llm_checks/hallucination_checker.json`
      - `ide/agents/ai_generated_checker.md` -> `output/ide/llm_checks/ai_generated_checker.json`
      - `ide/agents/vector_similarity_checker.md` -> `output/ide/llm_checks/vector_similarity_checker.json`
   5. Evaluate pass gates and write per-iteration log:
      - `output/ide/llm_checks/iteration_<i>.json`

```json
{
  "iteration": 1,
  "local_validation_passed": false,
  "llm_checker_passed": true,
  "hallucination_checker_passed": true,
  "ai_generated_checker_passed": false,
  "vector_similarity_checker_passed": true,
  "overall_passed": false,
  "blocking_issues": [
    "AIGeneratedChecker: repeated generic filler in summary",
    "Local validation: DataValidator failed"
  ],
  "next_actions": [
    "Rewrite summary with concrete scope and outcomes",
    "Fix HTML structure in Experience bullets"
  ]
}
```

   6. If PASS:
      - set `runner_state.json` to completed and `final_passed=true`
      - write `output/ide/llm_checks/final_report.md`
      - stop loop
   7. If FAIL and `i < MAX_ITERATIONS`:
      - inspect `output/ide/validation.json` and list missing keywords from `KeywordMatcher` / `IDEKeywordGate`
      - apply minimal fixes to `resume_body.html` that close those keyword gaps when supported by source resume
      - continue next iteration
   8. If FAIL and `i == MAX_ITERATIONS`:
      - set `runner_state.json` to completed and `final_passed=false`
      - write blockers and recommended manual fixes in `final_report.md`
      - stop loop

5. Maintain human-readable summary each iteration.
   - Update `output/ide/llm_checks/summary.md` with:
     - current iteration and status table
     - top blockers
     - exact next patch plan for `resume_body.html`

## Error handling

- If command execution fails:
  - capture command and stderr into `runner_state.json:last_error`
  - retry once when error is transient
  - otherwise stop and write failure details to `final_report.md`
- If any checker output is not valid JSON:
  - regenerate that checker once
  - if still invalid, stop and report blocker

## If this plugin cannot run commands

Do not fake command execution.

Stop after writing:
- `output/ide/job_posting.json`
- `output/ide/resume_body.html`

Then ask user to run:
- `uv sync && uv run hr-breaker ide-ingest --resume <path-or-> --job <...>`
- `uv sync && uv run hr-breaker ide-sync --resume output/ide/resume_source.txt --min-keyword-score 0.55`

After that, continue from generated artifacts and run the checker/refinement loop in chat.
