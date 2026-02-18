# Agent: `vector_similarity_checker` (semantic alignment, IDE-friendly)

Goal: approximate `VectorSimilarityMatcher` without external embedding APIs.

## Inputs

- Job posting JSON: `output/ide/job_posting.json`
- Extracted resume text: `output/ide/resume.txt`

## Task

Estimate semantic alignment between resume and target job in range `0.0..1.0`.

Guidelines:
- prioritize alignment of responsibilities, domain, and core required skills
- do not reward keyword stuffing without supporting experience evidence
- penalize major requirement gaps and irrelevant experience focus
- be conservative if alignment is unclear

## Output (JSON only)

Return only a `FilterResult` JSON for `VectorSimilarityMatcher`.

- `threshold` must be `0.4` (project default)
- `passed` is `true` if `score >= 0.4`
- put key mismatch reasons into `issues`
- put specific repair hints into `suggestions`

```json
{
  "filter_name": "VectorSimilarityMatcher",
  "passed": true,
  "score": 0.71,
  "threshold": 0.4,
  "issues": [],
  "suggestions": ["Increase evidence for cloud operations ownership in experience bullets"],
  "feedback": "Semantic alignment is good overall, but distributed systems depth could be clearer."
}
```
