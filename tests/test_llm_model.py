from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import BaseModel
from pydantic_ai import Agent, BinaryContent, ModelRetry
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.tools import ToolDefinition

from hr_breaker.agents.extractor import extract_document
from hr_breaker.agents.optimizer import optimize_resume
from hr_breaker.config import (
    get_flash_model,
    has_api_key_for_model,
    missing_api_key_for_chat_model,
    settings_override,
)
from hr_breaker.filters.vector_similarity_matcher import VectorSimilarityMatcher
from hr_breaker.models import (
    IterationContext,
    JobPosting,
    OptimizedResume,
    ResumeSource,
)
from hr_breaker.services.codex_cli import CodexSchemaResult, CodexUsage
from hr_breaker.services.llm_model import (
    BackendModel,
    CodexCLIModel,
    _selection_schema,
    runtime_model_summary_lines,
    runtime_reasoning_effort,
)
from hr_breaker.utils.optimization_telemetry import (
    run_tracked_agent,
    telemetry_reporter,
)


class _Answer(BaseModel):
    value: str
    count: int = 0


def _result(output: dict, *, input_tokens: int = 0, output_tokens: int = 0):
    return CodexSchemaResult(
        output_json="",
        output=output,
        usage=CodexUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        ),
        model_name="codex/test",
    )


@pytest.mark.asyncio
async def test_codex_model_returns_structured_output_through_agent():
    run_codex = AsyncMock(
        return_value=_result(
            {"value": "ok", "count": 2},
            input_tokens=11,
            output_tokens=3,
        )
    )
    agent = Agent(
        CodexCLIModel("gpt-pro"),
        output_type=_Answer,
        system_prompt="system",
        model_settings={"reasoning_effort": "high"},
    )

    with patch("hr_breaker.services.llm_model.run_codex_schema", new=run_codex):
        result = await agent.run("request")

    assert result.output == _Answer(value="ok", count=2)
    assert result.usage().requests == 1
    assert result.usage().input_tokens == 11
    assert run_codex.await_args.kwargs["model"] == "gpt-pro"
    assert run_codex.await_args.kwargs["reasoning_effort"] == "high"
    assert run_codex.await_args.kwargs["output_schema"]["title"] == "_Answer"
    assert "system" in run_codex.await_args.kwargs["system_prompt"]


@pytest.mark.asyncio
async def test_codex_model_preserves_tool_loop_retry_history_and_usage():
    state = {"called": False}
    agent = Agent(CodexCLIModel(None), output_type=_Answer, system_prompt="system")

    @agent.tool_plain
    def double(value: int) -> int:
        state["called"] = True
        return value * 2

    @agent.output_validator
    def require_tool(output: _Answer) -> _Answer:
        if not state["called"]:
            raise ModelRetry("Call double before returning.")
        return output

    run_codex = AsyncMock(
        side_effect=[
            _result(
                {
                    "call": {
                        "name": "final_result",
                        "arguments": {"value": "too early", "count": 0},
                    }
                },
                input_tokens=10,
            ),
            _result(
                {"call": {"name": "double", "arguments": {"value": 2}}},
                input_tokens=20,
            ),
            _result(
                {
                    "call": {
                        "name": "final_result",
                        "arguments": {"value": "done", "count": 4},
                    }
                },
                output_tokens=7,
            ),
        ]
    )
    telemetry = []

    with (
        patch("hr_breaker.services.llm_model.run_codex_schema", new=run_codex),
        telemetry_reporter(telemetry.append),
    ):
        result = await run_tracked_agent(agent, "request", component="TestAgent")

    assert result.output == _Answer(value="done", count=4)
    assert result.usage().requests == 3
    assert result.usage().input_tokens == 30
    assert result.usage().output_tokens == 7
    assert run_codex.await_count == 3
    second_prompt = run_codex.await_args_list[1].kwargs["prompt"]
    third_prompt = run_codex.await_args_list[2].kwargs["prompt"]
    assert "validation_feedback" in second_prompt
    assert "Call double before returning" in second_prompt
    assert "application_tool" in third_prompt
    assert '"content":"4"' in third_prompt
    assert len(telemetry) == 1
    assert telemetry[0]["component"] == "TestAgent"
    assert telemetry[0]["requests"] == 3


@pytest.mark.asyncio
async def test_codex_model_forwards_user_and_tool_result_images():
    agent = Agent(CodexCLIModel(None), output_type=_Answer)

    @agent.tool_plain
    def preview() -> BinaryContent:
        return BinaryContent(data=b"tool-image", media_type="image/png")

    run_codex = AsyncMock(
        side_effect=[
            _result({"call": {"name": "preview", "arguments": {}}}),
            _result(
                {
                    "call": {
                        "name": "final_result",
                        "arguments": {"value": "seen", "count": 1},
                    }
                }
            ),
        ]
    )

    with patch("hr_breaker.services.llm_model.run_codex_schema", new=run_codex):
        result = await agent.run(
            [
                "Review this image",
                BinaryContent(data=b"user-image", media_type="image/png"),
            ]
        )

    assert result.output.value == "seen"
    first_images = run_codex.await_args_list[0].kwargs["images"]
    second_images = run_codex.await_args_list[1].kwargs["images"]
    assert [image.data for image in first_images] == [b"user-image"]
    assert [image.data for image in second_images] == [b"user-image", b"tool-image"]


@pytest.mark.asyncio
async def test_one_cached_backend_model_switches_delegates_per_request():
    selected_backend = {"codex": False}
    runtime_settings: dict = {
        "reasoning_effort": "low",
        "max_tokens": 100,
    }
    calls: list[str] = []
    seen_settings: list[dict] = []

    async def api_response(_messages, info):
        calls.append("api")
        seen_settings.append(dict(info.model_settings or {}))
        return ModelResponse(
            parts=[ToolCallPart("final_result", {"value": "api", "count": 1})]
        )

    async def codex_response(_messages, info):
        calls.append("codex")
        seen_settings.append(dict(info.model_settings or {}))
        return ModelResponse(
            parts=[ToolCallPart("final_result", {"value": "codex", "count": 2})]
        )

    api_model = FunctionModel(api_response, model_name="api")
    codex_model = FunctionModel(codex_response, model_name="codex")
    backend_model = BackendModel("flash")
    agent = Agent(
        backend_model,
        output_type=_Answer,
        model_settings={
            "reasoning_effort": "stale",
            "max_tokens": 1,
            "temperature": 0.25,
        },
    )

    with (
        patch(
            "hr_breaker.services.llm_model.is_codex_cli_backend",
            side_effect=lambda: selected_backend["codex"],
        ),
        patch(
            "hr_breaker.services.llm_model.get_model_settings",
            side_effect=lambda: dict(runtime_settings),
        ),
        patch("hr_breaker.services.llm_model._litellm_model", return_value=api_model),
        patch("hr_breaker.services.llm_model.CodexCLIModel", return_value=codex_model),
    ):
        first = await agent.run("first")
        runtime_settings.clear()
        runtime_settings["reasoning_effort"] = "high"
        selected_backend["codex"] = True
        second = await agent.run("second")
        runtime_settings.clear()
        runtime_settings["max_tokens"] = 300
        selected_backend["codex"] = False
        third = await agent.run("third")

    assert backend_model is agent.model
    assert [first.output.value, second.output.value, third.output.value] == [
        "api",
        "codex",
        "api",
    ]
    assert calls == ["api", "codex", "api"]
    assert seen_settings == [
        {
            "reasoning_effort": "low",
            "max_tokens": 100,
            "temperature": 0.25,
        },
        {"reasoning_effort": "high", "temperature": 0.25},
        {"max_tokens": 300, "temperature": 0.25},
    ]


@pytest.mark.asyncio
async def test_codex_backend_failure_never_falls_back_to_api():
    async def codex_failure(_messages, _info):
        raise RuntimeError("codex failed")

    failing_codex = FunctionModel(codex_failure, model_name="codex")
    agent = Agent(BackendModel("flash"), output_type=_Answer)

    with (
        patch(
            "hr_breaker.services.llm_model.is_codex_cli_backend",
            return_value=True,
        ),
        patch(
            "hr_breaker.services.llm_model.CodexCLIModel",
            return_value=failing_codex,
        ),
        patch("hr_breaker.services.llm_model._litellm_model") as api_model,
        pytest.raises(RuntimeError, match="codex failed"),
    ):
        await agent.run("request")

    api_model.assert_not_called()


def test_model_factory_is_backend_router_and_api_key_check_is_backend_independent():
    assert isinstance(get_flash_model(), BackendModel)

    with (
        patch("hr_breaker.config.is_codex_cli_backend", return_value=True),
        patch.dict("os.environ", {}, clear=True),
    ):
        assert not has_api_key_for_model("gemini/gemini-test")


def test_missing_api_key_check_uses_active_chat_backend():
    with patch.dict("os.environ", {}, clear=True):
        with settings_override(
            {
                "llm_backend": "litellm",
                "flash_model": "gemini/gemini-test",
            }
        ):
            assert missing_api_key_for_chat_model("flash") == (
                "gemini/gemini-test",
                "GEMINI_API_KEY",
            )

        with settings_override({"llm_backend": "codex_cli"}):
            assert missing_api_key_for_chat_model("flash") is None


def test_backend_model_selects_scoped_codex_models_with_flash_fallback():
    with (
        patch(
            "hr_breaker.services.llm_model.is_codex_cli_backend",
            return_value=True,
        ),
        patch(
            "hr_breaker.services.llm_model.get_settings",
            return_value=SimpleNamespace(
                codex_model=" gpt-pro ",
                codex_flash_model="gpt-flash",
            ),
        ),
    ):
        assert BackendModel("pro").model_name == "codex/gpt-pro"
        assert BackendModel("flash").model_name == "codex/gpt-flash"

    with (
        patch(
            "hr_breaker.services.llm_model.is_codex_cli_backend",
            return_value=True,
        ),
        patch(
            "hr_breaker.services.llm_model.get_settings",
            return_value=SimpleNamespace(
                codex_model="gpt-pro",
                codex_flash_model="   ",
            ),
        ),
    ):
        assert BackendModel("flash").model_name == "codex/gpt-pro"

    with (
        patch(
            "hr_breaker.services.llm_model.is_codex_cli_backend",
            return_value=True,
        ),
        patch(
            "hr_breaker.services.llm_model.get_settings",
            return_value=SimpleNamespace(
                codex_model=None,
                codex_flash_model=None,
            ),
        ),
    ):
        assert BackendModel("pro").model_name == "codex/default"
        assert BackendModel("flash").model_name == "codex/default"


def test_runtime_model_summary_uses_active_codex_models():
    with settings_override(
        {
            "llm_backend": "codex_cli",
            "codex_model": "gpt-pro",
            "codex_flash_model": "gpt-flash",
        }
    ):
        assert runtime_model_summary_lines() == [
            "Pro model: codex/gpt-pro / codex-cli",
            "Flash model: codex/gpt-flash / codex-cli",
            "Embedding model: disabled for Codex CLI",
        ]


def test_runtime_reasoning_uses_codex_specific_setting():
    with settings_override(
        {
            "llm_backend": "codex_cli",
            "codex_reasoning_effort": "ultra",
            "reasoning_effort": "medium",
        }
    ):
        assert runtime_reasoning_effort() == "ultra"


def test_multi_tool_schema_is_strict_and_does_not_mutate_tools():
    first = ToolDefinition(
        name="double",
        parameters_json_schema={
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
        },
    )
    second = ToolDefinition(
        name="final_result",
        parameters_json_schema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
        kind="output",
    )
    originals = [
        first.parameters_json_schema.copy(),
        second.parameters_json_schema.copy(),
    ]

    schema, fixed = _selection_schema([first, second], allow_text=False)

    assert fixed is None
    variants = schema["properties"]["call"]["anyOf"]
    assert [variant["properties"]["name"]["enum"] for variant in variants] == [
        ["double"],
        ["final_result"],
    ]
    assert first.parameters_json_schema == originals[0]
    assert second.parameters_json_schema == originals[1]


@pytest.mark.asyncio
async def test_vector_similarity_is_skipped_in_codex_mode():
    source = ResumeSource(content="Ada Python")
    optimized = OptimizedResume(
        html="<main>Ada</main>",
        pdf_text="Ada Python",
        source_checksum=source.checksum,
    )
    job = JobPosting(title="Engineer", company="Acme", requirements=["Python"])

    with patch(
        "hr_breaker.filters.vector_similarity_matcher.is_codex_cli_backend",
        return_value=True,
    ):
        result = await VectorSimilarityMatcher().evaluate(optimized, job, source)

    assert result.passed
    assert result.skipped


@pytest.mark.asyncio
async def test_unchanged_optimizer_uses_codex_application_tool_loop():
    html = "<main><h1>Ada Lovelace</h1><p>Python engineer</p></main>"
    source = ResumeSource(content="Ada Lovelace\nPython engineer")
    job = JobPosting(
        title="Engineer",
        company="Acme",
        requirements=["Python"],
        keywords=["python"],
    )
    context = IterationContext(iteration=0, original_resume=source.content)
    run_codex = AsyncMock(
        side_effect=[
            _result(
                {
                    "call": {
                        "name": "check_content_length",
                        "arguments": {"html": html},
                    }
                }
            ),
            _result(
                {
                    "call": {
                        "name": "final_result",
                        "arguments": {
                            "html": html,
                            "changes": ["Tailored summary"],
                        },
                    }
                }
            ),
        ]
    )

    with (
        settings_override({"llm_backend": "codex_cli"}),
        patch("hr_breaker.services.llm_model.run_codex_schema", new=run_codex),
        patch("hr_breaker.agents.optimizer.HTMLRenderer") as renderer_type,
    ):
        renderer_type.return_value.render.return_value = SimpleNamespace(page_count=1)
        optimized = await optimize_resume(source, job, context)

    assert optimized.html == html
    assert optimized.changes == ["Tailored summary"]
    assert run_codex.await_count == 2
    variants = run_codex.await_args_list[0].kwargs["output_schema"]["properties"][
        "call"
    ]["anyOf"]
    names = [variant["properties"]["name"]["enum"][0] for variant in variants]
    assert names == [
        "check_content_length",
        "preview_resume",
        "check_keywords_tool",
        "validate_structure",
        "final_result",
    ]
    assert "fits_one_page" in run_codex.await_args_list[1].kwargs["prompt"]


@pytest.mark.asyncio
async def test_unchanged_extractor_uses_codex_without_provider_api_key():
    run_codex = AsyncMock(
        side_effect=[
            _result(
                {
                    "name": "Ada Lovelace",
                    "email": "ada@example.test",
                    "other_links": [],
                }
            ),
            _result({"summary": ["Python engineer"]}),
            _result({"experience": []}),
            _result({"education": []}),
            _result(
                {
                    "technical": ["Python"],
                    "languages": [],
                    "certifications": [],
                    "awards": [],
                }
            ),
            _result({"projects": []}),
            _result({"publications": []}),
        ]
    )

    with (
        settings_override({"llm_backend": "codex_cli"}),
        patch("hr_breaker.services.llm_model.run_codex_schema", new=run_codex),
        patch.dict(
            "os.environ",
            {
                "LLM_BACKEND": "codex_cli",
                "GEMINI_API_KEY": "",
                "GOOGLE_API_KEY": "",
                "OPENROUTER_API_KEY": "",
                "OPENAI_API_KEY": "",
                "ANTHROPIC_API_KEY": "",
                "MOONSHOT_API_KEY": "",
            },
        ),
    ):
        extraction = await extract_document(
            "Ada Lovelace\nada@example.test\nPython engineer"
        )

    assert run_codex.await_count == 7
    assert extraction.personal_info.name == "Ada Lovelace"
    assert extraction.personal_info.email == "ada@example.test"
    assert extraction.summary == ["Python engineer"]
    assert extraction.skills.technical == ["Python"]
