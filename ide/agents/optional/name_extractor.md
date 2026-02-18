# Agent: `name_extractor` (optional)

Goal: extract `first_name` / `last_name` from the source resume so output files can be named consistently.

## Input

- Preferred file: `output/ide/resume_source.txt`
- Fallback: any explicit resume file path provided by user

## Task

Extract the person’s name from the resume content.

## Output (JSON only)

Return **only** valid JSON:

```json
{
  "first_name": "John",
  "last_name": "Doe"
}
```

If you cannot find a name, use `null` for both fields.
