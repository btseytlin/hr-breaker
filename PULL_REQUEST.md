# Add resume language switcher (EN/RU) with translation and quality review

## Summary

This PR adds optional **multi-language output** for generated resumes. The optimization pipeline continues to run entirely in **English** (unchanged); translation is a **post-processing step** after all filters pass. Users can choose the output language (English or Russian initially; the design is extensible for more languages) and optionally **translate an existing result** without re-running optimization.

## Motivation

- Applicants often need resumes in the local language (e.g. Russian) while job descriptions and ATS tools may be in English.
- Running optimization in English keeps all existing filters (keywords, ATS, hallucination, etc.) valid and avoids duplicating filter logic per language.
- A single optimized English resume can be translated to multiple languages on demand.

## Design decisions

- **Optimize in English, translate after**: The main loop produces an English HTML resume and runs all filters on it. Only when the user selects a non-English language do we run a separate translate → review → re-render step.
- **Quality gate**: A dedicated translation reviewer agent checks terminology, naturalness, completeness, and consistency; failed reviews trigger a retry with feedback (configurable max iterations).
- **Extensible language list**: A small `Language` model (`code`, `english_name`, `native_name`) and `SUPPORTED_LANGUAGES` list; adding a language is one new entry.
- **Filenames include language**: Generated PDFs are named with a language suffix (e.g. `first_last_company_role_en.pdf`, `first_last_company_role_ru.pdf`) so multiple languages for the same job do not overwrite each other.

## Changes

### New files

| File | Description |
|------|-------------|
| `src/hr_breaker/models/language.py` | `Language` dataclass, `SUPPORTED_LANGUAGES`, `DEFAULT_LANGUAGE`, `get_language()` helper. |
| `src/hr_breaker/agents/translator.py` | Translator agent: English HTML → target language, preserves HTML structure and technical terms. Uses `gemini_flash_model`. |
| `src/hr_breaker/agents/translation_reviewer.py` | Translation reviewer agent: checks terminology, naturalness, completeness, consistency, grammar; outputs pass/fail and suggestions. Uses `gemini_flash_model`. |
| `tests/test_translation.py` | 41 tests for language model, translator/reviewer (mocked), orchestration translation flow, PDF naming, and config. |

### Modified files

| File | Changes |
|------|---------|
| `src/hr_breaker/models/__init__.py` | Export `Language`, `SUPPORTED_LANGUAGES`, `DEFAULT_LANGUAGE`, `get_language`. |
| `src/hr_breaker/agents/__init__.py` | Export `translate_resume`, `review_translation`. |
| `src/hr_breaker/config.py` | Add `default_language` (default `"en"`), `translation_max_iterations` (default `2`). Env: `DEFAULT_LANGUAGE`, `TRANSLATION_MAX_ITERATIONS`. |
| `src/hr_breaker/orchestration.py` | Add `language` and `on_translation_status` to `optimize_for_job()`. After filters pass, if `language` is not English, call `translate_and_rerender()`. New public `translate_and_rerender()` for translating an already-optimized resume. |
| `src/hr_breaker/services/pdf_storage.py` | `generate_path()` gains optional `lang_code`; filename always ends with `_{lang}.pdf` (default `_en`). |
| `src/hr_breaker/main.py` | Sidebar: "Resume language" selectbox (native names: English, Русский). Pass selected language into `optimize_for_job()`. Result section: "Translate to {language}" button to translate current result without re-optimizing. Save PDF with `lang_code` in path. |
| `src/hr_breaker/cli.py` | `--lang` / `-l` option (choices from `SUPPORTED_LANGUAGES`). Pass language to `optimize_for_job()` and use `lang_code` in generated PDF path. |
| `.env.example` | Document `DEFAULT_LANGUAGE`, `TRANSLATION_MAX_ITERATIONS`. |

### Unchanged

- All existing filters (they still run on English content).
- Optimizer agent and prompts (still English-only).
- Resume templates and styling (same layout; only text content is translated).
- Job parser, name extractor, hallucination detector, AI-generated detector (unchanged).

## Usage

### Streamlit

1. In the sidebar, choose **Resume language** (e.g. "Русский" for Russian).
2. Run **Optimize** as usual. After optimization passes, the resume is translated (if not English) and the PDF is saved with the language suffix (e.g. `…_ru.pdf`).
3. If a result is already shown and you change the language, use **Translate to {language}** to translate that result without re-running optimization; the new PDF is saved with the selected language suffix.

### CLI

```bash
# English (default)
uv run hr-breaker optimize resume.txt job.txt

# Russian output (optimize in English, then translate)
uv run hr-breaker optimize resume.txt job.txt --lang ru

# Explicit English
uv run hr-breaker optimize resume.txt job.txt -l en
```

### Config

- `DEFAULT_LANGUAGE` — default output language (e.g. `en`).
- `TRANSLATION_MAX_ITERATIONS` — max translate-review retries when the reviewer does not pass (default `2`).

## Testing

- **Language model**: `get_language`, supported list, defaults, uniqueness.
- **Translation models**: `TranslationResult`, `TranslationReview` (including score bounds).
- **Prompts**: Placeholders and formatting for translator and reviewer.
- **Translator/Reviewer agents**: Mocked LLM; `translate_resume` (with/without feedback), `review_translation` pass/fail.
- **Orchestration**: `translate_and_rerender` happy path, retry on failed review, status callback, exhaustion behavior; `optimize_for_job` skips translation for English/`None`, runs translation for Russian.
- **PDF storage**: Filename includes `_en` / `_ru` etc.; different languages yield different files.
- **Config**: Default values for new settings.

Run:

```bash
uv run pytest tests/test_translation.py -v
```

All 41 new tests pass; existing tests (excluding known network-dependent ones) pass.

## Backward compatibility

- Default language is English; no translation runs unless a non-English language is selected.
- CLI and Streamlit default behavior (no `--lang` / English selected) is unchanged.
- PDF filenames now always include a language suffix (`_en` when not specified), so existing scripts that parse filenames may need to account for the new `_en` segment; the pattern remains `*_en.pdf` / `*_ru.pdf` and is documented.

## Checklist

- [x] New code is covered by tests.
- [x] Config and env example updated.
- [x] No breaking changes to existing optimization or filter behavior.
- [x] Translation is optional and off by default (English).
- [x] PR description documents design, usage, and testing.
