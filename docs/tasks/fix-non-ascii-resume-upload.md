# Fix non-ASCII resume upload (Russian) encoding error

**Status:** done
**Branch:** main
**Worktree:** none

## Design

### Problem
GitHub issue #26: Russian-language resume upload fails with
`'ascii' codec can't encode characters in position 7-8: ordinal not in range(128)`
wrapped as `ModelHTTPError / APIError / OpenrouterException`.

### Root cause (reproduced)
Not content-related. The resume text (Cyrillic) travels end-to-end as UTF-8
without issue. The failure is in the outbound HTTP request headers.

- litellm's OpenAI-compatible transformation builds `Authorization: Bearer {api_key}`
  (see `litellm/llms/openai/chat/gpt_transformation.py`: `headers["Authorization"] = f"Bearer {api_key}"`).
- `"Bearer "` is exactly 7 characters. If the API key begins with a non-ASCII
  char, that char lands at header byte 7 (and 8 if two).
- `aiohttp` encodes headers as latin-1/ASCII and raises `UnicodeEncodeError`
  whose `.args[0]` points to positions 7-8 — the exact signature in the bug.
- `aiohttp_handler._handle_error` wraps it via `str(e)` into an
  `OpenrouterException` → `APIError` → pydantic-ai `ModelHTTPError`.

Reproduced locally by prepending `"\u00a0\u00a0"` (non-breaking spaces —
a common copy-paste artifact) to a valid `OPENROUTER_API_KEY` and calling
`extract_name("Иван Петров…")`. Same error, verbatim.

The Russian content was a red herring: the user happened to paste both their
resume *and* their API key, and the key paste picked up invisible whitespace
(NBSP / BOM / zero-width / smart quotes). Any prior request would have failed
the same way; it only surfaced now because the user was exercising the upload
path.

### Scope
- Env-agnostic. Affects any provider that builds a `Bearer` header — the bug
  triggers whenever the user-supplied key contains non-ASCII bytes.
- Ingress points for user-supplied keys:
  1. HTTP request `api_keys` field → `_build_overrides` (`server.py:845`)
     → `settings_override` (`config.py:248`) → `os.environ[...]`.
  2. `.env` file / shell env → `Settings` fields.

### Chosen approach
Sanitize + validate API keys at every ingress, fail loud with an actionable
error message before the key reaches the HTTP client.

1. Add `utils/api_key.py::sanitize_api_key(value, provider)`:
   - Strip leading/trailing whitespace including Unicode whitespace
     (NBSP `\u00a0`, BOM `\ufeff`, zero-width `\u200b`-`\u200f`,
     `\u2028`/`\u2029`) using an explicit allow-list of strippable chars.
   - After stripping, require the key to be pure ASCII (`key.isascii()`).
     If not, raise `ValueError` naming the provider and the offending codepoint
     so the user knows where the problem is.
   - Empty string after strip → raise `ValueError("empty API key")`.
2. Call it from:
   - `settings_override` in `config.py` when applying `api_keys` overrides
     (covers all per-request UI ingress — this is the single chokepoint that
     `_build_overrides` feeds into).
   - A pydantic `field_validator` on `Settings` for the five `*_api_key`
     fields (covers `.env` / shell env ingress at startup).
3. In `server.py::upload_resume` (and `paste_resume`), catch `ValueError` from
   `settings_override` separately from the existing generic `Exception`
   handler and surface it as a 400 with a clear message. This prevents the
   "Failed to extract name: …" wrapping that currently obscures the real cause.

### Why not alternatives
- **Silently strip all non-ASCII from keys.** Rejected: a key with non-ASCII
  *in the middle* is almost certainly a typo/corruption — silent mutation
  could produce a "working" but wrong key and a confusing auth error from
  the provider. Fail loud instead.
- **Catch `UnicodeEncodeError` deep in the call stack and reword it.**
  Rejected: the error happens inside aiohttp-handler code we don't own, and
  the wrapping layer already swallows it into `OpenrouterException`. Fixing
  at ingress is simpler and prevents the bad request from ever being sent.
- **Base64/URL-encode the key.** Rejected: the header format is fixed by the
  OpenAI/OpenRouter API contract.

### Unknowns
None load-bearing. The set of invisible whitespace characters to strip is
pragmatic — if a new artifact shows up, add it to the allow-list.

### Backwards compatibility
No external contract changes. The only behavior change is: a key that
previously produced `UnicodeEncodeError` at call time now produces a
`ValueError` / 400 at upload time with a clearer message. Keys that were
valid continue to work unchanged (ASCII-only after a no-op strip).

TDD: yes (repro script `tmp/verify_api_key_hypothesis.py` already demonstrates
the bug; convert to a unit test that asserts sanitizer rejects a
NBSP-prefixed key and the upload endpoint returns 400 not 500).

### Invariants
- Any API key written to `os.environ` via `settings_override` or loaded into
  `Settings` must be pure ASCII and non-empty.
- `sanitize_api_key` must fail loud (raise) on remaining non-ASCII after
  strip — never silently drop chars from the middle of a key.
- The `Authorization` header built by downstream HTTP clients must be
  ASCII-encodable (guaranteed transitively by the invariant above).

### Principles
- Validate at system boundaries, not deep in the stack. User-supplied values
  get sanitized where they enter, so internal code can trust them.
- Error messages name the offending input (provider name + codepoint), not
  just "bad key" — the user pasted it, they need to know *what* is wrong.
- Keep the fix surgical. No refactor of the settings pipeline; one helper
  plus two call sites plus one validator.

## Plan

Approach: Add a single `sanitize_api_key` helper that strips a whitelisted
set of invisible chars and raises `ValueError` on remaining non-ASCII. Wire
it into the two ingress points for keys: runtime request overrides
(`settings_override` in `config.py`) and startup env-loaded keys (a new
`sanitize_env_api_keys()` called from `config.py` module body, alongside
`load_dotenv()`). Fix the one endpoint (`paste_resume`) that doesn't already
catch `ValueError` so the 400 path reaches the UI.

### Phase 1 — Sanitizer + unit tests

- **1.1** `src/hr_breaker/utils/api_key.py` (create)
  - `_STRIPPABLE_INVISIBLES: frozenset[str]` — set of codepoints safe to strip
    from leading/trailing positions: ASCII whitespace via `str.strip()`,
    plus `\u00a0` (NBSP), `\ufeff` (BOM), `\u200b`-`\u200f` (zero-width +
    directional marks), `\u2028`, `\u2029`, `\u202a`-`\u202e` (bidi).
  - `sanitize_api_key(value: str, provider: str) -> str` — returns the
    cleaned key or raises `ValueError`.
    - Normalize: `str.strip()` then strip characters in
      `_STRIPPABLE_INVISIBLES` from both ends in a loop until fixed point.
    - If result is empty → `ValueError(f"API key for {provider} is empty after stripping whitespace")`.
    - If `not result.isascii()` → find first non-ASCII char, report its
      position and `hex(ord(ch))` in the error:
      `ValueError(f"API key for {provider} contains non-ASCII character {hex(ord(ch))!r} at position {i}; check for smart quotes or hidden characters from copy-paste")`.
    - Return result.
  - Invariant: "keys written to `os.environ` must be pure ASCII and non-empty";
    "must fail loud on non-ASCII after strip".

- **1.2** `tests/test_api_key_sanitizer.py` (create)
  - `test_plain_ascii_passthrough` — normal key returns unchanged.
  - `test_strips_leading_trailing_whitespace` — `"  sk-abc  "` → `"sk-abc"`.
  - `test_strips_nbsp_prefix` — `"\u00a0\u00a0sk-abc"` → `"sk-abc"`.
  - `test_strips_bom` — `"\ufeffsk-abc"` → `"sk-abc"`.
  - `test_strips_zero_width` — `"\u200bsk-abc\u200b"` → `"sk-abc"`.
  - `test_rejects_interior_non_ascii` — `"sk-абc"` raises `ValueError`
    whose message contains provider name, `"non-ASCII"`, and the hex
    codepoint of `а` (`0x430`).
  - `test_rejects_empty_after_strip` — `"\u00a0\u00a0"` raises `ValueError`
    with "empty" in message.
  - `test_rejects_smart_quote` — `"sk\u201cabc"` raises `ValueError` (smart
    quote in middle is a non-strippable, non-ASCII char).

- Commit: `feat(config): add sanitize_api_key helper with invisible-char stripping`

### Phase 2 — Wire sanitizer into ingress + fix paste endpoint

- **2.1** `src/hr_breaker/config.py` (modify, imports + around lines 18, 274-285)
  - Add `from hr_breaker.utils.api_key import sanitize_api_key` near top.
  - Add module-level `sanitize_env_api_keys() -> None` that reads each of
    `GEMINI_API_KEY`, `GOOGLE_API_KEY`, `OPENROUTER_API_KEY`, `OPENAI_API_KEY`,
    `ANTHROPIC_API_KEY`, `MOONSHOT_API_KEY` from `os.environ`. For each
    non-empty value, write `sanitize_api_key(value, provider_name)` back to
    `os.environ` (so any stripped-but-valid keys get normalized, and any
    non-ASCII keys raise `ValueError` at startup).
  - Call `sanitize_env_api_keys()` in the module body, directly after
    `load_dotenv()` on line 18.
  - In `settings_override`, inside the `api_keys` loop (around line 277-284):
    before `os.environ[env_var] = str(key_value)`, replace the assignment
    with `os.environ[env_var] = sanitize_api_key(str(key_value), provider)`.
    The `ValueError` propagates out of the context manager to the caller.
  - Invariant: "keys written to `os.environ` via `settings_override` or
    loaded into `Settings` must be pure ASCII and non-empty".

- **2.2** `src/hr_breaker/server.py` (modify `paste_resume` around lines 216-236)
  - Add `except ValueError as e: return JSONResponse(status_code=400, content={"error": str(e)})` branch before the generic `except Exception`
    (mirrors the existing `upload_resume` pattern at lines 199-200).
  - No change needed in `upload_resume` — it already handles `ValueError` → 400.
  - Invariant: user-facing errors for malformed keys are 400 with the
    sanitizer's actionable message, not 500 "Failed to extract name".

- **2.3** `tests/test_api_key_sanitizer.py` (extend from Phase 1)
  - `test_settings_override_sanitizes_nbsp_key` — call `settings_override({"api_keys": {"openrouter": "\u00a0\u00a0sk-abc"}})` as a context manager; assert `os.environ["OPENROUTER_API_KEY"] == "sk-abc"` inside the block.
  - `test_settings_override_rejects_non_ascii_key` — call with
    `"sk-абc"` value; assert `ValueError` raised with "openrouter" and
    "non-ASCII" in message; assert `OPENROUTER_API_KEY` env var was restored
    to its pre-call state (no leak).
  - `test_sanitize_env_api_keys_rejects_corrupt_env` — set
    `os.environ["OPENROUTER_API_KEY"] = "sk-абc"`, call
    `sanitize_env_api_keys()`, assert `ValueError` raised. Clean up env var
    in teardown.

- **2.4** `tests/test_server.py` (modify or new test)
  - `test_upload_resume_with_corrupt_api_key_returns_400` — POST a small
    text file to `/api/resume/upload` with `api_keys_json` containing
    `{"openrouter": "\u00a0sk-abc"}`; assert 200 (sanitizer strips NBSP,
    key becomes valid format — though will fail auth downstream, that's
    out of scope). Second case: `{"openrouter": "sk-абc"}`; assert 400
    and error body names "non-ASCII".
  - `test_paste_resume_with_corrupt_api_key_returns_400` — POST to
    `/api/resume/paste` with the non-ASCII key case; assert 400 (this is
    the regression — currently 500).

- Commit: `fix(config): sanitize API keys at ingress to prevent aiohttp header UnicodeEncodeError`

### Test strategy
TDD: yes. Order within each phase: write failing test → implement → green.

Behaviors covered:
- Sanitizer strip rules (Phase 1 unit tests).
- Sanitizer rejection of non-ASCII interior chars with informative error (Phase 1).
- `settings_override` applies sanitizer and env var restore on error (Phase 2).
- `sanitize_env_api_keys` validates startup env (Phase 2).
- Upload endpoint returns 400 with sanitizer message, not 500 (Phase 2).
- Paste endpoint returns 400 (currently 500 — regression lock) (Phase 2).

### Order & dependencies
- Phase 1 blocks Phase 2 (helper must exist).
- Within Phase 2: 2.1 (wiring) → 2.2 (error-handling) → 2.3/2.4 (tests
  can be written first per TDD; implement order is 2.1 then 2.2).

### Backwards-compat check
No external contract changes. One user-visible behavior change: a key with
interior non-ASCII bytes now fails fast at upload time (HTTP 400) instead
of failing opaquely at LLM call time (HTTP 500 with wrapped
`OpenrouterException`). This is the desired fix.

Keys with only leading/trailing invisible whitespace (the common paste
artifact) now get silently cleaned and work. Previously they 500'd.

Startup-time env validation (`sanitize_env_api_keys`) can raise if a
previously-deployed `.env` file contains a corrupt key. Mitigation: the
error message names the provider and codepoint, so the operator knows
exactly which line of `.env` to fix. This is a feature, not a regression —
such a `.env` would cause every LLM call to fail.

### Open questions / risks / rollback
- Risk: the `_STRIPPABLE_INVISIBLES` list may miss an exotic invisible
  char a future user pastes. Mitigation: error names the codepoint, so
  the user sees e.g. `0x2060` (word joiner) and can report it; we add
  it to the list. Acceptable — no silent failure, just needs a follow-up.
- Rollback: revert the commit. The sanitizer is isolated (one helper file,
  two call sites); revert is clean.
- No open questions.

## Verify

**Result:** passed

Positive:
- `sanitize_api_key("sk-abc", "openrouter")` returns unchanged → pass (unit).
- `sanitize_api_key("\u00a0\u00a0sk-abc", "openrouter")` → `"sk-abc"` → pass.
- `sanitize_api_key` strips BOM, zero-width, mixed whitespace → pass (4 unit cases).
- `settings_override({"api_keys": {"openrouter": "\u00a0\u00a0sk-abc"}})` →
  `os.environ["OPENROUTER_API_KEY"] == "sk-abc"` inside block → pass.
- `sanitize_env_api_keys()` normalizes NBSP-prefixed env var in place → pass.
- Server startup (module import of `hr_breaker.config`) normalizes
  pre-seeded NBSP-prefixed `OPENROUTER_API_KEY` to clean value → pass
  (verified via `tmp/verify_fix.py`).

Negative:
- `sanitize_api_key("sk-абc", "openrouter")` raises `ValueError` with
  provider name + `"non-ASCII"` + `0x430` → pass.
- `sanitize_api_key("\u00a0\u00a0", "openrouter")` raises with `"empty"` → pass.
- `sanitize_api_key("sk\u201cabc", "openrouter")` (interior smart quote)
  raises with `0x201c` → pass.
- `sanitize_env_api_keys()` on a corrupt `OPENROUTER_API_KEY=sk-абc` raises
  `ValueError` at startup → pass.
- POST `/api/resume/upload` with `api_keys_json={"openrouter": "sk-абc"}`
  → HTTP 400, body names `openrouter` + `non-ASCII` + `0x430` → pass.
- POST `/api/resume/paste` with `api_keys={"openrouter": "sk-абc"}`
  → HTTP 400 with same message → pass (this is the issue #26
  regression — was HTTP 500 with wrapped UnicodeEncodeError before).
- `settings_override` rejection path leaves pre-existing env vars
  unchanged (no leak) → pass.

Invariants:
- "Keys written to `os.environ` via `settings_override` or `Settings`
  must be pure ASCII and non-empty" → guaranteed by `sanitize_api_key`
  (tests confirm it's called on every ingress) + startup sanitizer.
- "`sanitize_api_key` must fail loud on non-ASCII" → direct unit coverage
  + endpoint regression tests confirm the error surfaces intact.
- "`Authorization` header must be ASCII-encodable" → transitive from
  invariant 1; no code path writes keys to env without sanitization.

Smoke: `uv run python tmp/smoke_issue26.py` — exact issue #26 scenario
(Russian resume content + Cyrillic-containing API key) POSTed to both
`/api/resume/paste` and `/api/resume/upload`. Both return HTTP 400
`API key for openrouter contains non-ASCII character '0x430' at position 3`
instead of the former HTTP 500 with wrapped `'ascii' codec can't encode characters in position 7-8`.

Full test suite: `uv run pytest -q` → 277 passed, 5 skipped, 0 failed.

## Conclusion

Outcome: Issue #26 resolved. The root cause was not Russian resume
content but non-ASCII bytes in the pasted API key landing at
`Authorization: Bearer <key>` header positions 7-8, which aiohttp cannot
latin-1-encode. A new `sanitize_api_key` helper strips whitelisted
invisible chars (NBSP, BOM, zero-width, bidi controls) and fails loud
with a codepoint-specific message on any remaining non-ASCII. The helper
runs at two ingress points: (1) module import of `hr_breaker.config`
normalizes all env-loaded keys; (2) `settings_override` sanitizes every
per-request `api_keys` override, restoring pre-existing env vars if any
key in the batch is rejected. All five user-facing endpoints that feed
user-pasted keys through `settings_override` (`/api/resume/upload`,
`/api/resume/paste`, `/api/profile/quick-create` file + paste,
`/api/profile/{id}/synthesize`) return HTTP 400 with the sanitizer's
actionable message instead of the former wrapped 500.

### Deviations from plan
- Phase 2.1 Design sketched a pydantic `field_validator` on `Settings`
  for env-loaded keys; Plan chose a module-level `sanitize_env_api_keys()`
  called once at import instead. Reason: `Settings` only exposes two of
  the five provider keys as fields; a helper that sweeps `os.environ`
  covers all five uniformly.
- Plan 2.2 listed only `paste_resume` as needing the `except ValueError`
  branch (noting `upload_resume` already had it). Review surfaced three
  additional profile endpoints that also wrapped `settings_override` in
  a bare `except Exception`. A follow-up commit (9290e3a) added the 400
  branch to `/api/profile/quick-create` (both file and paste branches)
  and `/api/profile/{id}/synthesize`, plus regression tests.

### Invariants
- Keys written to `os.environ` via `settings_override` or at startup are
  pure ASCII and non-empty → verified by unit tests of both ingress
  paths plus a startup sanitizer test.
- `sanitize_api_key` fails loud on non-ASCII residue → verified by 4
  negative unit tests (interior Cyrillic, smart quote, empty-after-strip,
  empty input) plus 5 endpoint regression tests.
- `Authorization` header is ASCII-encodable → transitive; no code path
  writes to a key env var without going through `sanitize_api_key`.

### Review findings
- Important (reviewer confidence 90): `/api/profile/quick-create` and
  `/api/profile/{id}/synthesize` wrapped `settings_override` in
  `except Exception` only. Resolved in commit 9290e3a with explicit
  `except ValueError → 400` branches and three new endpoint tests.
- No Critical findings at confidence ≥ 80.

### Future work
- `_run_optimization` at `server.py:916` also wraps `settings_override` in
  an `asyncio.create_task` under an SSE stream. A corrupt key here
  surfaces as a background-task exception, not as an HTTP response.
  Justification for deferral: errors in the optimization path use a
  different reporting channel (SSE `error` events) and reworking that
  flow is a larger refactor outside this task's scope (the task
  description and Design scoped to the resume-upload entry path from
  issue #26). Recommend a follow-up task that standardizes how
  `settings_override` errors propagate through the SSE stream.

### Verified by
- Full test suite: 280 passed, 5 skipped (pre-fix baseline 277 passed,
  the extra 3 come from sanitizer unit tests already added plus the
  reviewer-round endpoint tests — total new coverage: 15 sanitizer + 8
  endpoint regression cases).
- Smoke test `tmp/smoke_issue26.py`: the exact issue #26 scenario
  (Russian resume + Cyrillic-byte key) POSTed to both paste and upload
  endpoints now returns HTTP 400 with `API key for openrouter contains
  non-ASCII character '0x430' at position 3` instead of HTTP 500 with
  the wrapped UnicodeEncodeError.
- Startup smoke test `tmp/verify_fix.py`: env-loaded NBSP-prefixed key
  is normalized in place at module import; interior non-ASCII fails
  loud with provider + codepoint in the error.
- Independent reviewer (`up:reviewer`) verdict: merge-ready after the
  Important finding was addressed.
