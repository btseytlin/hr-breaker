"""API key sanitization — strip common paste artifacts, reject non-ASCII.

Rationale: HTTP clients (aiohttp, httpx) encode the `Authorization: Bearer <key>`
header as latin-1/ASCII. Non-ASCII bytes in a pasted key — most commonly
leading non-breaking spaces (U+00A0), BOM (U+FEFF), or zero-width marks —
cause UnicodeEncodeError at request time, wrapped by higher layers into an
opaque provider exception. Sanitize at ingress so the bad byte surfaces
with an actionable error instead of a wrapped 500.
"""

_STRIPPABLE_INVISIBLES: frozenset[str] = frozenset(
    {
        "\u00a0",  # NBSP
        "\ufeff",  # BOM / zero-width no-break space
        "\u200b",  # zero-width space
        "\u200c",  # zero-width non-joiner
        "\u200d",  # zero-width joiner
        "\u200e",  # LTR mark
        "\u200f",  # RTL mark
        "\u2028",  # line separator
        "\u2029",  # paragraph separator
        "\u202a",  # LTR embedding
        "\u202b",  # RTL embedding
        "\u202c",  # pop directional formatting
        "\u202d",  # LTR override
        "\u202e",  # RTL override
    }
)


def sanitize_api_key(value: str, provider: str) -> str:
    """Strip paste artifacts from an API key; raise ValueError on non-ASCII residue.

    Steps:
      1. Standard whitespace strip.
      2. Strip leading/trailing chars in `_STRIPPABLE_INVISIBLES` to fixed point.
      3. Reject empty result.
      4. Reject any remaining non-ASCII char (name offending codepoint in the error).
    """
    cleaned = value.strip()
    while cleaned and (cleaned[0] in _STRIPPABLE_INVISIBLES or cleaned[-1] in _STRIPPABLE_INVISIBLES):
        if cleaned[0] in _STRIPPABLE_INVISIBLES:
            cleaned = cleaned[1:]
            continue
        if cleaned[-1] in _STRIPPABLE_INVISIBLES:
            cleaned = cleaned[:-1]

    if not cleaned:
        raise ValueError(f"API key for {provider} is empty after stripping whitespace")

    if not cleaned.isascii():
        for i, ch in enumerate(cleaned):
            if ord(ch) > 127:
                raise ValueError(
                    f"API key for {provider} contains non-ASCII character "
                    f"{hex(ord(ch))!r} at position {i}; check for smart quotes "
                    f"or hidden characters from copy-paste"
                )

    return cleaned
