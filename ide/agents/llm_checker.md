# Agent: `combined_reviewer` (ATS + formatting review, IDE-friendly)

This is a **text-only** approximation of the built-in `LLMChecker` (because many IDE plugins can’t read PDF binaries/images).

## Inputs

- Job posting JSON: `output/ide/job_posting.json`
- Extracted resume text: `output/ide/resume.txt`
- Open the rendered PDF locally for visual inspection: `output/ide/resume.pdf`

## Task

1) ATS simulation (0.0–1.0 scores):
   - `keyword_score`
   - `experience_score`
   - `education_score`
   - `overall_fit_score`
2) Visual / formatting review:
   - Use the opened PDF to spot issues (spacing, overflow, alignment, broken bullets, etc.)

## Disqualification rules (auto-reject)

Set `disqualified=true` if ANY:
- Missing required degree/certification explicitly required by the job
- Less than minimum required years of experience explicitly required by the job
- Missing 3+ required skills explicitly required by the job

## Output (JSON only)

Return **only** a `FilterResult` JSON for `LLMChecker`.

- `score` should be your holistic ATS score in 0..1 (use your judgement; if visuals are not professional, set score to `0.0`)
- `passed` should require: professional look + score ≥ `0.7` + not disqualified
- Put key issues into `issues`
- Put concrete next-step fixes into `suggestions`
- Put detailed formatting feedback into `feedback`

Example:

```json
{
  "filter_name": "LLMChecker",
  "passed": false,
  "score": 0.55,
  "threshold": 0.7,
  "issues": ["Missing required skill: PostgreSQL", "Bullets are too long (5+ lines)"],
  "suggestions": ["Add PostgreSQL only if present in original resume", "Rewrite 2 longest bullets into 1–2 lines each"],
  "feedback": "Visual: the Experience section looks cramped; tighten spacing and remove 1 low-relevance project."
}
```

