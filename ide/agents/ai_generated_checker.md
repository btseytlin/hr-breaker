# Agent: `ai_generated_detector`

Goal: detect **genuine** “AI tells” (contradictions, impossible claims, generic filler), while ignoring normal resume conventions.

## Input

- Extracted resume text: `output/ide/resume.txt` (preferred)

## Task

Flag ONLY real issues:
- contradictions (dates/titles don’t match)
- impossible claims / timelines
- “buzzword soup” with no specifics
- repeated generic filler

Do NOT flag just because the writing is structured / uses action verbs / has metrics.

## Output (JSON only)

Return **only** a `FilterResult` JSON for `AIGeneratedChecker`:
- `score = 1 - ai_probability`
- `passed = (ai_probability <= 0.5)`
- `threshold = 0.4` (match project default)

```json
{
  "filter_name": "AIGeneratedChecker",
  "passed": true,
  "score": 0.85,
  "threshold": 0.4,
  "issues": [],
  "suggestions": [],
  "feedback": ""
}
```

