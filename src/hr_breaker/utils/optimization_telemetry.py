from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Callable

from hr_breaker.utils.retry import run_with_retry


TelemetryReporter = Callable[[dict[str, Any]], None]


_reporter: ContextVar[TelemetryReporter | None] = ContextVar("optimization_telemetry_reporter", default=None)


@dataclass
class UsageTotals:
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@contextmanager
def telemetry_reporter(reporter: TelemetryReporter):
    token = _reporter.set(reporter)
    try:
        yield
    finally:
        _reporter.reset(token)


def provider_for_model(model_name: str) -> str:
    return model_name.split("/", 1)[0] if "/" in model_name else "unknown"


def _looks_like_embedding_model(model_name: str) -> bool:
    return "embed" in model_name.lower()


def zero_usage_totals() -> dict[str, int]:
    return asdict(UsageTotals())


def accumulate_usage_totals(totals: dict[str, int], usage_entry: dict[str, Any]) -> dict[str, int]:
    updated = dict(totals)
    if usage_entry.get("usage_available") is False:
        return updated
    for key in UsageTotals.__dataclass_fields__:
        updated[key] = int(updated.get(key, 0)) + int(usage_entry.get(key, 0) or 0)
    return updated


def _usage_value(usage: Any, *names: str) -> int:
    for name in names:
        value = getattr(usage, name, None)
        if value is not None:
            return int(value or 0)
    return 0


def _usage_payload(component: str, model_name: str, usage: Any) -> dict[str, Any]:
    input_tokens = _usage_value(usage, "input_tokens", "prompt_tokens")
    output_tokens = _usage_value(usage, "output_tokens", "completion_tokens")
    cache_read_tokens = _usage_value(usage, "cache_read_tokens")
    cache_write_tokens = _usage_value(usage, "cache_write_tokens")
    requests = _usage_value(usage, "requests") or (1 if any((input_tokens, output_tokens, cache_read_tokens, cache_write_tokens)) else 0)
    usage_available = any((input_tokens, output_tokens, cache_read_tokens, cache_write_tokens))
    return {
        "timestamp": datetime.now().strftime("%H:%M:%S.%f")[:-3],
        "component": component,
        "model": model_name,
        "provider": provider_for_model(model_name),
        "requests": requests,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_write_tokens": cache_write_tokens,
        "usage_available": usage_available,
    }


def report_usage(component: str, model_name: str, usage: Any) -> None:
    if usage is None:
        return
    reporter = _reporter.get()
    if reporter is None:
        return
    reporter(_usage_payload(component, model_name, usage))


async def run_tracked_agent(agent: Any, *args, component: str, **kwargs):
    result = await run_with_retry(agent.run, *args, **kwargs)
    model = getattr(agent, "model", None)
    model_name = getattr(model, "model_name", "unknown")
    usage_method = getattr(result, "usage", None)
    usage = usage_method() if callable(usage_method) else usage_method
    report_usage(component, model_name, usage)
    return result
