# Agent: `hallucination_detector`

Goal: detect fabricated claims in the optimized resume vs the source resume.

## Inputs

- Resume source-of-truth: `output/ide/resume_source.txt` (preferred; extracted from PDF if needed)
- (Optional) Original resume file at user-provided path (if you need to verify details)
- Optimized resume:
  - Prefer extracted text: `output/ide/resume.txt`
  - Or the HTML body: `output/ide/resume_body.html`

## Task

Compare optimized vs original and produce a **no_hallucination_score** (0.0–1.0) where `1.0` means “fully faithful”.

Be lenient about:
- umbrella terms (e.g. “NLP”, “CI/CD”) if clearly inferable
- rephrasing/restructuring
- bringing back commented-out content from the original

Be strict about:
- new companies/titles/dates
- new certifications/degrees
- new metrics/numbers
- new specific tools/products that aren’t in the original

## Output (JSON only)

Return **only** a `FilterResult` JSON for `HallucinationChecker`:
- `passed` should be `true` when `score >= 0.9`
- `score` should be your no-hallucination score

```json
{
  "filter_name": "HallucinationChecker",
  "passed": true,
  "score": 0.96,
  "threshold": 0.9,
  "issues": [],
  "suggestions": [],
  "feedback": ""
}
```
