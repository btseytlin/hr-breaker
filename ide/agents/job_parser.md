# Agent: `job_parser`

Goal: turn the job description (pasted into chat) into a structured `JobPosting` JSON.

## Input

Use one of:
1) Preferred: open `output/ide/job_text.txt` and paste its contents below this prompt.
2) Or paste the full job description text directly (URL is optional).

## Task

Extract:
- `title`: job title
- `company`: company name
- `requirements`: specific requirements (skills/experience/education)
- `keywords`: technologies/tools/methodologies mentioned (be thorough)
- `description`: brief summary of the role

Also include:
- `raw_text`: **the full original job description text you were given**

Strict extraction rules:
- Prioritize must-have signals that affect ATS quality:
  - production delivery and scalability
  - NLP/LLM/RAG capabilities
  - integration/interop expectations (for example C#/Java handoff context)
  - monitoring/operational ownership
- Include both canonical and surface-form keywords from `raw_text`.
- Do not omit concrete tools/frameworks if explicitly present (for example: `scikit-learn`, `spaCy`, `LangChain`, `LlamaIndex`).
- Keep `requirements` focused on explicit hard requirements and constraints; put supporting terms into `keywords`.
- If a requirement is implicit in multiple bullets, include it once in normalized form.

## Output

Create (or output so the user can save) `output/ide/job_posting.json`.

Return **only** valid JSON matching:

```json
{
  "title": "Backend Engineer",
  "company": "Acme Corp",
  "requirements": ["Python", "Django", "PostgreSQL"],
  "keywords": ["python", "django", "postgresql", "rest", "api"],
  "description": "Brief summary…",
  "raw_text": "FULL job description text…"
}
```
