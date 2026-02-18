# Output schema (for IDE “agent” prompts)

If your IDE plugin can create/edit files, save results as JSON using these schemas.
If it can’t, just paste JSON into a file manually.

## `FilterResult` (single filter)

```json
{
  "filter_name": "KeywordMatcher",
  "passed": false,
  "score": 0.12,
  "threshold": 0.25,
  "issues": ["Missing important keywords: python, django, postgresql"],
  "suggestions": ["Add missing keywords if they match your actual experience"],
  "feedback": "Free-form text for the optimizer (optional)"
}
```

## `ValidationResult` (aggregate)

```json
{
  "results": [
    {
      "filter_name": "DataValidator",
      "passed": true,
      "score": 1.0,
      "threshold": 1.0,
      "issues": [],
      "suggestions": [],
      "feedback": ""
    }
  ]
}
```

