from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import textwrap
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, TypeAdapter

from hr_breaker.agents.combined_reviewer import CombinedReviewResult
from hr_breaker.agents.name_extractor import ExtractedName
from hr_breaker.agents.optimizer import OptimizerResult
from hr_breaker.models import JobPosting
from hr_breaker.models.profile import DocumentExtraction
from hr_breaker.services import codex_cli
from hr_breaker.utils.optimization_telemetry import telemetry_reporter


_FAKE_CODEX = r"""
#!/usr/bin/env python3
import json
import os
import signal
import sys
import time


def log(payload):
    path = os.environ.get("FAKE_CODEX_LOG")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload) + "\n")


args = sys.argv[1:]
if args == ["--version"]:
    log({"action": "version", "argv": args})
    print("codex-cli 9.8.7")
    raise SystemExit(0)

if args == ["login", "status"]:
    log({
        "action": "login",
        "argv": args,
        "credential_env": sorted(
            key for key in os.environ
            if key.upper() in {"API_KEY", "ACCESS_TOKEN"}
            or key.upper().endswith("_API_KEY")
            or key.upper().endswith("_ACCESS_TOKEN")
        ),
    })
    print(os.environ.get("FAKE_AUTH_STATUS", "Logged in using ChatGPT"))
    raise SystemExit(int(os.environ.get("FAKE_AUTH_EXIT", "0")))

if args == ["app-server"]:
    log({
        "action": "app-server",
        "credential_env": sorted(
            key for key in os.environ
            if key.upper() in {"API_KEY", "ACCESS_TOKEN"}
            or key.upper().endswith("_API_KEY")
            or key.upper().endswith("_ACCESS_TOKEN")
        ),
    })
    for line in sys.stdin:
        message = json.loads(line)
        method = message.get("method")
        if method == "initialize":
            print(json.dumps({"id": message["id"], "result": {"userAgent": "fake"}}), flush=True)
        elif method == "model/list":
            cursor = message.get("params", {}).get("cursor")
            if cursor is None:
                data = [{
                    "id": "gpt-pro",
                    "model": "gpt-pro",
                    "displayName": "GPT Pro",
                    "description": "Power model.",
                    "hidden": False,
                    "isDefault": True,
                    "defaultReasoningEffort": "medium",
                    "supportedReasoningEfforts": [
                        {"reasoningEffort": "low", "description": "Fast"},
                        {"reasoningEffort": "medium", "description": "Balanced"},
                    ],
                }]
                next_cursor = "page-2"
            else:
                data = [{
                    "id": "gpt-flash",
                    "model": "gpt-flash",
                    "displayName": "GPT Flash",
                    "description": "Fast model.",
                    "hidden": False,
                    "isDefault": False,
                    "defaultReasoningEffort": "low",
                    "supportedReasoningEfforts": [
                        {"reasoningEffort": "low", "description": "Fast"},
                    ],
                }]
                next_cursor = None
            print(json.dumps({
                "id": message["id"],
                "result": {"data": data, "nextCursor": next_cursor},
            }), flush=True)
    raise SystemExit(0)

if "exec" not in args:
    print("unsupported fake command", file=sys.stderr)
    raise SystemExit(64)

prompt = sys.stdin.read()
schema_path = args[args.index("--output-schema") + 1]
image_path = args[args.index("--image") + 1] if "--image" in args else None
entry = {
    "action": "exec",
    "argv": args,
    "prompt": prompt,
    "cwd": args[args.index("-C") + 1],
    "schema": json.loads(open(schema_path, encoding="utf-8").read()),
    "developer_instructions": open(
        os.path.join(args[args.index("-C") + 1], "AGENTS.md"),
        encoding="utf-8",
    ).read(),
    "image_hex": open(image_path, "rb").read().hex() if image_path else None,
    "image_suffix": os.path.splitext(image_path)[1] if image_path else None,
    "credential_env": sorted(
        key for key in os.environ
        if key.upper() in {"API_KEY", "ACCESS_TOKEN"}
        or key.upper().endswith("_API_KEY")
        or key.upper().endswith("_ACCESS_TOKEN")
    ),
    "pid": os.getpid(),
}
log(entry)

mode = os.environ.get("FAKE_CODEX_MODE", "success")
if mode == "nonzero":
    print(prompt + " :: sk-secret-token-123456 :: " + ("x" * 1000), file=sys.stderr)
    raise SystemExit(7)
if mode == "turn_failed":
    print(json.dumps({"type": "turn.failed", "error": {"message": "quota exhausted"}}))
    raise SystemExit(0)
if mode == "malformed":
    print("this is not json")
    raise SystemExit(0)
if mode == "invalid_output":
    print(json.dumps({
        "type": "item.completed",
        "item": {"type": "agent_message", "text": '{"value": 123}'},
    }))
    print(json.dumps({"type": "turn.completed", "usage": {}}))
    raise SystemExit(0)
if mode in {"sleep", "hard_timeout"}:
    if mode == "hard_timeout":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    time.sleep(30)

print(json.dumps({"type": "thread.started", "thread_id": "fake"}))
print(json.dumps({
    "type": "item.completed",
    "item": {"type": "agent_message", "text": '{"value":"ok","count":2}'},
}))
print(json.dumps({
    "type": "turn.completed",
    "usage": {
        "input_tokens": 101,
        "cached_input_tokens": 33,
        "output_tokens": 17,
    },
}))
"""


class Answer(BaseModel):
    value: str
    count: int = 0


@pytest.fixture(autouse=True)
def isolated_limiter(monkeypatch):
    monkeypatch.setattr(codex_cli, "_LIMITER", codex_cli._CrossThreadLimiter())


@pytest.fixture
def fake_codex(tmp_path: Path, monkeypatch):
    executable = tmp_path / "codex"
    executable.write_text(textwrap.dedent(_FAKE_CODEX).lstrip(), encoding="utf-8")
    executable.chmod(0o755)
    log_path = tmp_path / "codex-log.jsonl"
    monkeypatch.setenv("FAKE_CODEX_LOG", str(log_path))
    return executable, log_path


def _settings(executable: Path, **overrides):
    values = {
        "llm_backend": "codex_cli",
        "codex_bin": str(executable),
        "codex_model": "gpt-test",
        "codex_timeout_seconds": 2.0,
        "codex_max_concurrency": 1,
        "reasoning_effort": "high",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _log_entries(log_path: Path) -> list[dict]:
    if not log_path.exists():
        return []
    return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]


@pytest.mark.asyncio
async def test_codex_model_catalog_uses_authenticated_app_server_and_paginates(
    fake_codex,
    monkeypatch,
):
    executable, log_path = fake_codex
    monkeypatch.setattr(codex_cli, "get_settings", lambda: _settings(executable))
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-leak")

    catalog = await codex_cli.get_codex_model_catalog()

    assert catalog == {
        "models": [
            {
                "value": "gpt-pro",
                "label": "GPT Pro",
                "description": "Power model.",
                "is_default": True,
                "default_reasoning_effort": "medium",
                "supported_reasoning_efforts": ["low", "medium"],
            },
            {
                "value": "gpt-flash",
                "label": "GPT Flash",
                "description": "Fast model.",
                "is_default": False,
                "default_reasoning_effort": "low",
                "supported_reasoning_efforts": ["low"],
            },
        ],
        "default_model": "gpt-pro",
    }
    entries = _log_entries(log_path)
    assert [entry["action"] for entry in entries] == ["login", "app-server"]
    assert entries[1]["credential_env"] == []


@pytest.mark.asyncio
async def test_run_codex_structured_uses_stdin_schema_image_clean_env_and_telemetry(
    fake_codex,
    monkeypatch,
):
    executable, log_path = fake_codex
    monkeypatch.setattr(codex_cli, "get_settings", lambda: _settings(executable))
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-leak")
    monkeypatch.setenv("CODEX_ACCESS_TOKEN", "must-not-leak")
    monkeypatch.setenv("OTHER_API_KEY", "must-not-leak")
    monkeypatch.setenv("OTHER_ACCESS_TOKEN", "must-not-leak")
    captured_usage = []

    with telemetry_reporter(captured_usage.append):
        answer = await codex_cli.run_codex_structured(
            output_type=Answer,
            model="gpt-test",
            reasoning_effort="high",
            system_prompt="Never reveal PRIVATE_SYSTEM.",
            prompt="Process PRIVATE_RESUME.",
            component="TestAgent",
            image_bytes=b"\x89PNG\r\n",
        )

    assert answer == Answer(value="ok", count=2)
    entries = _log_entries(log_path)
    assert [entry["action"] for entry in entries] == ["login", "exec"]
    execution = entries[1]
    argv = execution["argv"]
    assert argv[:4] == ["-a", "never", "exec", "--json"]
    assert argv[-5:] == [
        "-c",
        'model_reasoning_effort="high"',
        "-m",
        "gpt-test",
        "-",
    ]
    assert "PRIVATE_RESUME" not in " ".join(argv)
    assert "PRIVATE_SYSTEM" not in " ".join(argv)
    assert "PRIVATE_RESUME" in execution["prompt"]
    assert "PRIVATE_SYSTEM" not in execution["prompt"]
    assert "PRIVATE_SYSTEM" in execution["developer_instructions"]
    assert "PRIVATE_RESUME" not in execution["developer_instructions"]
    assert "Do not use Codex runtime tools" in execution["developer_instructions"]
    assert "select one only by returning" in execution["developer_instructions"]
    for feature in ("shell_tool", "multi_agent", "apps", "remote_plugin"):
        assert ["--disable", feature] == argv[
            argv.index(feature) - 1 : argv.index(feature) + 1
        ]
    assert 'web_search="disabled"' in argv
    assert execution["schema"]["properties"]["value"]["type"] == "string"
    assert execution["schema"]["additionalProperties"] is False
    assert execution["schema"]["required"] == ["value", "count"]
    assert execution["image_hex"] == b"\x89PNG\r\n".hex()
    assert execution["image_suffix"] == ".png"
    assert execution["credential_env"] == []
    assert not Path(execution["cwd"]).exists()

    assert len(captured_usage) == 1
    assert captured_usage[0]["component"] == "TestAgent"
    assert captured_usage[0]["model"] == "codex/gpt-test"
    assert captured_usage[0]["provider"] == "codex"
    assert captured_usage[0]["input_tokens"] == 101
    assert captured_usage[0]["output_tokens"] == 17
    assert captured_usage[0]["cache_read_tokens"] == 33


@pytest.mark.asyncio
async def test_run_codex_schema_returns_usage_without_reporting_or_mutating_schema(
    fake_codex,
    monkeypatch,
):
    executable, _log_path = fake_codex
    monkeypatch.setattr(
        codex_cli,
        "get_settings",
        lambda: _settings(executable, codex_model="wrong-global-model"),
    )
    schema = TypeAdapter(Answer).json_schema()
    original_schema = json.loads(json.dumps(schema))
    captured_usage = []

    with telemetry_reporter(captured_usage.append):
        result = await codex_cli.run_codex_schema(
            model="gpt-test",
            output_schema=schema,
            system_prompt="system",
            prompt="request",
        )

    assert result.output == {"value": "ok", "count": 2}
    assert result.output_json == '{"value":"ok","count":2}'
    assert result.model_name == "codex/gpt-test"
    assert result.usage == codex_cli.CodexUsage(
        input_tokens=101,
        output_tokens=17,
        cache_read_tokens=33,
    )
    assert captured_usage == []
    assert schema == original_schema


@pytest.mark.asyncio
async def test_run_codex_schema_omits_model_flag_when_explicit_model_is_none(
    fake_codex,
    monkeypatch,
):
    executable, log_path = fake_codex
    monkeypatch.setattr(
        codex_cli,
        "get_settings",
        lambda: _settings(executable, codex_model="wrong-global-model"),
    )

    result = await codex_cli.run_codex_schema(
        model=None,
        output_schema=TypeAdapter(Answer).json_schema(),
        system_prompt="system",
        prompt="request",
    )

    execution = next(entry for entry in _log_entries(log_path) if entry["action"] == "exec")
    assert "-m" not in execution["argv"]
    assert result.model_name == "codex/default"


@pytest.mark.asyncio
async def test_run_rejects_non_chatgpt_login_before_exec(fake_codex, monkeypatch):
    executable, log_path = fake_codex
    monkeypatch.setattr(codex_cli, "get_settings", lambda: _settings(executable))
    monkeypatch.setenv("FAKE_AUTH_STATUS", "Logged in using an API key")

    with pytest.raises(codex_cli.CodexAuthError, match="API-key login"):
        await codex_cli.run_codex_structured(
            output_type=Answer,
            system_prompt="system",
            prompt="private request",
            component="TestAgent",
        )

    assert [entry["action"] for entry in _log_entries(log_path)] == ["login"]


@pytest.mark.asyncio
async def test_nonzero_exit_has_bounded_redacted_diagnostic(fake_codex, monkeypatch):
    executable, _log_path = fake_codex
    monkeypatch.setattr(codex_cli, "get_settings", lambda: _settings(executable))
    monkeypatch.setenv("FAKE_CODEX_MODE", "nonzero")

    with pytest.raises(codex_cli.CodexCLIError) as caught:
        await codex_cli.run_codex_structured(
            output_type=Answer,
            system_prompt="PRIVATE_SYSTEM",
            prompt="PRIVATE_RESUME",
            component="TestAgent",
        )

    message = str(caught.value)
    assert "status 7" in message
    assert "PRIVATE_SYSTEM" not in message
    assert "PRIVATE_RESUME" not in message
    assert "sk-secret" not in message
    assert len(message) < 450


@pytest.mark.asyncio
async def test_jsonl_failure_event_and_invalid_output_are_errors(fake_codex, monkeypatch):
    executable, _log_path = fake_codex
    monkeypatch.setattr(codex_cli, "get_settings", lambda: _settings(executable))
    monkeypatch.setenv("FAKE_CODEX_MODE", "turn_failed")

    with pytest.raises(codex_cli.CodexCLIError, match="turn.failed.*quota exhausted"):
        await codex_cli.run_codex_structured(
            output_type=Answer,
            system_prompt="system",
            prompt="request",
            component="TestAgent",
        )

    monkeypatch.setenv("FAKE_CODEX_MODE", "invalid_output")
    with pytest.raises(codex_cli.CodexCLIError, match="invalid structured output"):
        await codex_cli.run_codex_structured(
            output_type=Answer,
            system_prompt="system",
            prompt="request",
            component="TestAgent",
        )


@pytest.mark.asyncio
async def test_timeout_escalates_and_reaps_process(fake_codex, monkeypatch):
    executable, log_path = fake_codex
    monkeypatch.setattr(
        codex_cli,
        "get_settings",
        lambda: _settings(executable, codex_timeout_seconds=1.0),
    )

    async def authenticated(*_args):
        return None

    monkeypatch.setattr(codex_cli, "_require_chatgpt_auth", authenticated)
    monkeypatch.setattr(codex_cli, "_TERMINATION_GRACE_SECONDS", 0.05)
    monkeypatch.setenv("FAKE_CODEX_MODE", "hard_timeout")

    with pytest.raises(codex_cli.CodexTimeoutError):
        await codex_cli.run_codex_structured(
            output_type=Answer,
            system_prompt="system",
            prompt="request",
            component="TestAgent",
        )

    pid = next(entry["pid"] for entry in _log_entries(log_path) if entry["action"] == "exec")
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.asyncio
async def test_cancellation_kills_process_and_releases_limiter(fake_codex, monkeypatch):
    executable, log_path = fake_codex
    monkeypatch.setattr(
        codex_cli,
        "get_settings",
        lambda: _settings(executable, codex_timeout_seconds=5),
    )
    monkeypatch.setenv("FAKE_CODEX_MODE", "sleep")
    task = asyncio.create_task(
        codex_cli.run_codex_structured(
            output_type=Answer,
            system_prompt="system",
            prompt="request",
            component="TestAgent",
        )
    )
    for _ in range(100):
        if any(entry["action"] == "exec" for entry in _log_entries(log_path)):
            break
        await asyncio.sleep(0.01)
    else:  # pragma: no cover - protects against a hanging test
        pytest.fail("fake Codex exec never started")

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    monkeypatch.setenv("FAKE_CODEX_MODE", "success")
    answer = await codex_cli.run_codex_structured(
        output_type=Answer,
        system_prompt="system",
        prompt="request",
        component="TestAgent",
    )
    assert answer.value == "ok"


@pytest.mark.asyncio
async def test_get_codex_status_reports_version_and_auth_mode(fake_codex, monkeypatch):
    executable, _log_path = fake_codex
    monkeypatch.setattr(codex_cli, "get_settings", lambda: _settings(executable))

    status = await codex_cli.get_codex_status()

    assert status == {
        "installed": True,
        "version": "codex-cli 9.8.7",
        "authenticated": True,
        "auth_type": "chatgpt",
        "auth_mode": "chatgpt",
        "ready": True,
        "error": None,
        "message": None,
    }


@pytest.mark.asyncio
async def test_get_codex_status_handles_missing_executable(tmp_path, monkeypatch):
    missing = tmp_path / "missing-codex"
    monkeypatch.setattr(codex_cli, "get_settings", lambda: _settings(missing))

    status = await codex_cli.get_codex_status()

    assert status["installed"] is False
    assert status["ready"] is False
    assert status["message"] == "Codex CLI is not installed or is not on PATH."


def test_limiter_is_shared_safely_across_event_loops():
    limiter = codex_cli._CrossThreadLimiter()
    barrier = threading.Barrier(4)
    state_lock = threading.Lock()
    active = 0
    peak = 0

    async def worker():
        nonlocal active, peak
        barrier.wait()
        await limiter.acquire(2)
        try:
            with state_lock:
                active += 1
                peak = max(peak, active)
            await asyncio.sleep(0.05)
            with state_lock:
                active -= 1
        finally:
            limiter.release()

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(asyncio.run, worker()) for _ in range(4)]
        for future in futures:
            future.result(timeout=2)

    assert peak == 2


def _assert_strict_schema_node(node):
    if isinstance(node, list):
        for item in node:
            _assert_strict_schema_node(item)
        return
    if not isinstance(node, dict):
        return

    assert "default" not in node
    if node.get("type") == "object" or isinstance(node.get("properties"), dict):
        properties = node.get("properties", {})
        assert node.get("additionalProperties") is False
        assert node.get("required") == list(properties)
    for value in node.values():
        _assert_strict_schema_node(value)


@pytest.mark.parametrize(
    "output_type",
    [
        JobPosting,
        DocumentExtraction,
        ExtractedName,
        OptimizerResult,
        CombinedReviewResult,
    ],
)
def test_real_output_models_are_normalized_for_codex_strict_schema(output_type):
    schema = codex_cli._strict_output_schema(TypeAdapter(output_type).json_schema())
    _assert_strict_schema_node(schema)


def test_arbitrary_mapping_output_is_rejected():
    class MappingOutput(BaseModel):
        values: dict[str, str]

    with pytest.raises(codex_cli.CodexCLIError, match="arbitrary mapping"):
        codex_cli._strict_output_schema(TypeAdapter(MappingOutput).json_schema())
