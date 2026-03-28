"""LLM provider model discovery via async HTTP."""

from __future__ import annotations

import os
from typing import Any

import httpx

GEMINI_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"
REQUEST_TIMEOUT = 10.0

CHAT_METHODS = {"generateContent", "streamGenerateContent"}
EMBEDDING_METHODS = {"embedContent", "batchEmbedContents"}

# Maps provider short name -> (env var for API key, default base URL or None)
_PROVIDER_CONFIG: dict[str, tuple[str, str | None]] = {
    "gemini": ("GEMINI_API_KEY", None),
    "openrouter": ("OPENROUTER_API_KEY", "https://openrouter.ai/api/v1"),
    "openai": ("OPENAI_API_KEY", "https://api.openai.com/v1"),
    "anthropic": ("ANTHROPIC_API_KEY", "https://api.anthropic.com"),
    "moonshot": ("MOONSHOT_API_KEY", "https://api.moonshot.ai/v1"),
    "custom": ("OPENAI_API_KEY", "https://api.openai.com/v1"),
}


async def fetch_provider_catalog(
    provider: str,
    api_key: str | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    """Discover available models for a provider.

    Returns dict with keys: status, chat_models, embedding_models.
    """
    env_var, default_base = _PROVIDER_CONFIG.get(provider, ("", None))
    resolved_key = (api_key or "").strip() or os.environ.get(env_var, "").strip()

    if not resolved_key:
        # Also check GOOGLE_API_KEY fallback for gemini
        if provider == "gemini":
            resolved_key = os.environ.get("GOOGLE_API_KEY", "").strip()

    if not resolved_key:
        return {
            "status": {"state": "unknown", "message": "Enter API key to load models"},
            "chat_models": [],
            "embedding_models": [],
        }

    try:
        if provider == "gemini":
            chat, embed = await _fetch_gemini_models(resolved_key)
        elif provider == "anthropic":
            resolved_base = (base_url or "").strip() or default_base or "https://api.anthropic.com"
            chat, embed = await _fetch_anthropic_models(resolved_key, resolved_base)
        else:
            resolved_base = (base_url or "").strip() or default_base or "https://api.openai.com/v1"
            resolved_base = resolved_base.rstrip("/")
            chat, embed = await _fetch_openai_models(
                resolved_key, resolved_base, litellm_prefix=_litellm_prefix(provider)
            )
    except httpx.HTTPStatusError as exc:
        detail = _extract_error_detail(exc.response)
        return {
            "status": {
                "state": "warning",
                "message": f"Connection failed ({exc.response.status_code})",
                "detail": detail,
            },
            "chat_models": [],
            "embedding_models": [],
        }
    except (httpx.RequestError, ValueError) as exc:
        return {
            "status": {"state": "warning", "message": "Connection failed", "detail": str(exc)},
            "chat_models": [],
            "embedding_models": [],
        }

    return {
        "status": {
            "state": "connected",
            "message": f"Connected \u00b7 {len(chat)} chat / {len(embed)} embedding",
        },
        "chat_models": chat,
        "embedding_models": embed,
    }


def _litellm_prefix(provider: str) -> str:
    return {
        "gemini": "gemini/",
        "openrouter": "openrouter/",
        "openai": "openai/",
        "moonshot": "moonshot/",
    }.get(provider, "openai/")


async def _fetch_gemini_models(api_key: str) -> tuple[list[dict], list[dict]]:
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        resp = await client.get(
            GEMINI_MODELS_URL,
            params={"key": api_key},
        )
        resp.raise_for_status()
        models = resp.json().get("models", [])

    chat: list[dict] = []
    embed: list[dict] = []
    for m in models:
        name = m.get("name", "")
        if not name:
            continue
        short = name.removeprefix("models/")
        label = m.get("displayName") or short
        methods = set(m.get("supportedGenerationMethods", []))
        option = {"value": f"gemini/{short}", "label": str(label)}
        if methods & CHAT_METHODS:
            chat.append(option)
        if methods & EMBEDDING_METHODS:
            embed.append(option)

    return _dedup_sort(chat), _dedup_sort(embed)

def _anthropic_models_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/v1"):
        return f"{normalized}/models"
    return f"{normalized}/v1/models"


async def _fetch_anthropic_models(api_key: str, base_url: str) -> tuple[list[dict], list[dict]]:
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        resp = await client.get(
            _anthropic_models_url(base_url),
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])

    chat: list[dict] = []
    for model in data:
        model_id = model.get("id", "")
        if not model_id:
            continue
        label = model.get("display_name") or model_id
        chat.append({"value": f"anthropic/{model_id}", "label": str(label)})
    return _dedup_sort(chat), []


async def _fetch_openai_models(
    api_key: str, base_url: str, litellm_prefix: str
) -> tuple[list[dict], list[dict]]:
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        resp = await client.get(
            f"{base_url}/models",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])

    chat: list[dict] = []
    embed: list[dict] = []
    for m in data:
        model_id = m.get("id", "")
        if not model_id:
            continue
        if _looks_like_embedding(model_id):
            embed.append({"value": model_id, "label": model_id})
        else:
            chat.append({"value": f"{litellm_prefix}{model_id}", "label": model_id})

    # Fallback: if no chat models found, treat all as chat
    if not chat and embed:
        chat = [{"value": f"{litellm_prefix}{e['label']}", "label": e["label"]} for e in embed]
    # Fallback: if no embedding found, offer all raw
    if not embed and data:
        embed = [{"value": m.get("id", ""), "label": m.get("id", "")} for m in data if m.get("id")]

    return _dedup_sort(chat), _dedup_sort(embed)


def _looks_like_embedding(model_id: str) -> bool:
    return "embed" in model_id.lower()


def _dedup_sort(options: list[dict]) -> list[dict]:
    seen: dict[str, dict] = {}
    for o in options:
        seen.setdefault(o["value"], o)
    return sorted(seen.values(), key=lambda o: o["label"].lower())


def _extract_error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:200]
    if isinstance(payload, dict):
        err = payload.get("error")
        if isinstance(err, dict):
            msg = err.get("message")
            if msg:
                return str(msg)
        msg = payload.get("message")
        if msg:
            return str(msg)
    return response.text[:200]
