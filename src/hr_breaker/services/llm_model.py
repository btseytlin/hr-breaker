"""PydanticAI model adapters for dynamic LiteLLM/Codex CLI routing."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Literal
from uuid import uuid4

from pydantic_ai.messages import (
    BinaryContent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    SystemPromptPart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import (
    Model,
    ModelRequestParameters,
    StreamedResponse,
    check_allow_model_requests,
)
from pydantic_ai.profiles import ModelProfile
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.usage import RequestUsage

from hr_breaker.config import (
    _litellm_model,
    get_model_settings,
    get_settings,
    is_codex_cli_backend,
)
from hr_breaker.services.codex_cli import (
    CodexCLIError,
    CodexImage,
    CodexSchemaResult,
    CodexUsage,
    run_codex_schema,
)


ModelScope = Literal["pro", "flash"]
_RUNTIME_MODEL_SETTING_KEYS = ("reasoning_effort", "max_tokens")
_TEXT_RESULT_NAME = "__text_response__"
_TEXT_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class _Conversation:
    system_prompt: str
    transcript: str
    images: tuple[CodexImage, ...]


def _add_image(
    content: BinaryContent,
    images: list[CodexImage],
    image_ids: set[str],
) -> dict[str, str]:
    media_type = str(content.media_type)
    if not media_type.startswith("image/"):
        raise CodexCLIError(
            f"Codex CLI model does not support {media_type!r} attachments."
        )
    identifier = content.identifier
    if identifier not in image_ids:
        image_ids.add(identifier)
        images.append(CodexImage(data=content.data, media_type=media_type))
    return {
        "type": "attached_image",
        "identifier": identifier,
        "media_type": media_type,
    }


def _user_content_events(
    content: str | Sequence[Any],
    images: list[CodexImage],
    image_ids: set[str],
) -> list[dict[str, Any]]:
    values: Sequence[Any] = [content] if isinstance(content, str) else content
    events: list[dict[str, Any]] = []
    for value in values:
        if isinstance(value, str):
            events.append({"type": "text", "content": value})
        elif isinstance(value, BinaryContent):
            events.append(_add_image(value, images, image_ids))
        else:
            raise CodexCLIError(
                "Codex CLI model received an unsupported user attachment "
                f"({type(value).__name__})."
            )
    return events


def _serialize_messages(messages: list[ModelMessage]) -> _Conversation:
    system_parts: list[str] = []
    seen_system_parts: set[str] = set()
    events: list[dict[str, Any]] = []
    images: list[CodexImage] = []
    image_ids: set[str] = set()

    def add_system(value: str | None) -> None:
        if value and value not in seen_system_parts:
            seen_system_parts.add(value)
            system_parts.append(value)

    for message in messages:
        if isinstance(message, ModelRequest):
            add_system(message.instructions)
            for part in message.parts:
                if isinstance(part, SystemPromptPart):
                    add_system(part.content)
                elif isinstance(part, UserPromptPart):
                    events.append(
                        {
                            "role": "user",
                            "content": _user_content_events(
                                part.content, images, image_ids
                            ),
                        }
                    )
                elif isinstance(part, ToolReturnPart):
                    content, trailing_content = (
                        part.model_response_str_and_user_content()
                    )
                    attachments = _user_content_events(
                        trailing_content, images, image_ids
                    )
                    events.append(
                        {
                            "role": "application_tool",
                            "type": "result",
                            "name": part.tool_name,
                            "tool_call_id": part.tool_call_id,
                            "content": content,
                            "attachments": attachments,
                        }
                    )
                elif isinstance(part, RetryPromptPart):
                    events.append(
                        {
                            "role": "user",
                            "type": "validation_feedback",
                            "tool_name": part.tool_name,
                            "tool_call_id": part.tool_call_id,
                            "content": part.model_response(),
                        }
                    )
                else:  # pragma: no cover - guarded by PydanticAI's message union
                    raise CodexCLIError(
                        "Codex CLI model received an unsupported request part "
                        f"({type(part).__name__})."
                    )
        elif isinstance(message, ModelResponse):
            for part in message.parts:
                if isinstance(part, ToolCallPart):
                    events.append(
                        {
                            "role": "assistant",
                            "type": "application_tool_call",
                            "name": part.tool_name,
                            "tool_call_id": part.tool_call_id,
                            "arguments": part.args_as_json_str(),
                        }
                    )
                elif isinstance(part, TextPart):
                    events.append(
                        {"role": "assistant", "type": "text", "content": part.content}
                    )
                elif isinstance(part, ThinkingPart):
                    continue
                else:
                    raise CodexCLIError(
                        "Codex CLI model received an unsupported response part "
                        f"({type(part).__name__})."
                    )
        else:  # pragma: no cover - guarded by PydanticAI's message union
            raise CodexCLIError(
                f"Codex CLI model received an unsupported message ({type(message).__name__})."
            )

    system_prompt = "\n\n".join(system_parts) or (
        "Follow the application tool protocol and return a valid structured response."
    )
    transcript = json.dumps(events, ensure_ascii=False, separators=(",", ":"))
    return _Conversation(
        system_prompt=system_prompt,
        transcript=transcript,
        images=tuple(images),
    )


def _has_defs(schema: Any) -> bool:
    if isinstance(schema, dict):
        if "$defs" in schema:
            return True
        return any(_has_defs(value) for value in schema.values())
    if isinstance(schema, list):
        return any(_has_defs(value) for value in schema)
    return False


def _tool_schema(tool: ToolDefinition) -> dict[str, Any]:
    return deepcopy(tool.parameters_json_schema)


def _tool_protocol(
    tools: Sequence[ToolDefinition],
    *,
    allow_text: bool,
    fixed_tool: ToolDefinition | None,
) -> str:
    definitions = [
        {
            "name": tool.name,
            "kind": tool.kind,
            "description": tool.description,
            "arguments_schema": tool.parameters_json_schema,
        }
        for tool in tools
    ]
    if allow_text:
        definitions.append(
            {
                "name": _TEXT_RESULT_NAME,
                "kind": "text",
                "description": "Return an ordinary text response.",
                "arguments_schema": _TEXT_OUTPUT_SCHEMA,
            }
        )
    response_shape = (
        f"The only available action is {fixed_tool.name!r}; return its arguments object "
        "directly without a call wrapper."
        if fixed_tool is not None
        else "Return the call envelope required by the supplied output schema."
    )
    return (
        "\n\nAPPLICATION TOOL PROTOCOL\n"
        "Select exactly one listed application action. Runtime tools remain disabled. "
        "An output action ends the agent run; a function action is executed by the "
        "application and its result will be supplied in a later conversation turn. "
        f"{response_shape}\n"
        f"{json.dumps(definitions, ensure_ascii=False, separators=(',', ':'))}"
    )


def _selection_schema(
    tools: Sequence[ToolDefinition],
    *,
    allow_text: bool,
) -> tuple[dict[str, Any], ToolDefinition | None]:
    if len(tools) == 1 and not allow_text:
        return _tool_schema(tools[0]), tools[0]

    candidates: list[tuple[str, dict[str, Any]]] = [
        (tool.name, _tool_schema(tool)) for tool in tools
    ]
    if allow_text:
        candidates.append((_TEXT_RESULT_NAME, deepcopy(_TEXT_OUTPUT_SCHEMA)))
    if not candidates:
        raise CodexCLIError("Codex CLI model request has no valid output action.")
    if any(_has_defs(schema) for _name, schema in candidates):
        raise CodexCLIError(
            "Codex CLI model does not support $defs in a multi-tool request."
        )

    variants = [
        {
            "type": "object",
            "properties": {
                "name": {"type": "string", "enum": [name]},
                "arguments": schema,
            },
            "required": ["name", "arguments"],
            "additionalProperties": False,
        }
        for name, schema in candidates
    ]
    return (
        {
            "type": "object",
            "properties": {"call": {"anyOf": variants}},
            "required": ["call"],
            "additionalProperties": False,
        },
        None,
    )


def _selected_call(
    result: CodexSchemaResult,
    fixed_tool: ToolDefinition | None,
) -> tuple[str, dict[str, Any]]:
    if not isinstance(result.output, dict):
        raise CodexCLIError("Codex CLI model returned a non-object tool selection.")
    if fixed_tool is not None:
        return fixed_tool.name, result.output

    call = result.output.get("call")
    if not isinstance(call, dict):
        raise CodexCLIError("Codex CLI model returned an invalid tool selection.")
    name = call.get("name")
    arguments = call.get("arguments")
    if not isinstance(name, str) or not isinstance(arguments, dict):
        raise CodexCLIError("Codex CLI model returned invalid tool arguments.")
    return name, arguments


def _request_usage(usage: CodexUsage) -> RequestUsage:
    return RequestUsage(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=usage.cache_read_tokens,
        cache_write_tokens=usage.cache_write_tokens,
    )


def _runtime_model_settings(
    model_settings: ModelSettings | None,
) -> ModelSettings | None:
    """Replace cached application settings with the current per-request values."""
    effective = dict(model_settings or {})
    configured = get_model_settings() or {}
    for key in _RUNTIME_MODEL_SETTING_KEYS:
        effective.pop(key, None)
        if key in configured:
            effective[key] = configured[key]
    return effective or None


class CodexCLIModel(Model):
    """Expose ChatGPT-authenticated ``codex exec`` as a PydanticAI model."""

    def __init__(self, configured_model: str | None) -> None:
        self._configured_model = str(configured_model or "").strip() or None
        super().__init__(
            profile=ModelProfile(
                supports_tools=True,
                supports_json_schema_output=False,
                default_structured_output_mode="tool",
                supported_builtin_tools=frozenset(),
            )
        )

    @property
    def model_name(self) -> str:
        return f"codex/{self._configured_model or 'default'}"

    @property
    def system(self) -> str:
        return "codex-cli"

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        check_allow_model_requests()
        prepared_settings, parameters = self.prepare_request(
            model_settings, model_request_parameters
        )
        tools = [*parameters.function_tools, *parameters.output_tools]
        output_schema, fixed_tool = _selection_schema(
            tools,
            allow_text=parameters.allow_text_output,
        )
        conversation = _serialize_messages(messages)
        result = await run_codex_schema(
            model=self._configured_model,
            reasoning_effort=(prepared_settings or {}).get("reasoning_effort"),
            output_schema=output_schema,
            system_prompt=(
                conversation.system_prompt
                + _tool_protocol(
                    tools,
                    allow_text=parameters.allow_text_output,
                    fixed_tool=fixed_tool,
                )
            ),
            prompt=(
                "Continue this typed conversation and return the next application action.\n"
                f"<conversation-json>{conversation.transcript}</conversation-json>"
            ),
            images=conversation.images,
        )
        tool_name, arguments = _selected_call(result, fixed_tool)
        if tool_name == _TEXT_RESULT_NAME:
            text = arguments.get("text")
            if not isinstance(text, str):
                raise CodexCLIError("Codex CLI model returned invalid text output.")
            parts = [TextPart(text)]
            finish_reason = "stop"
        else:
            known_names = {tool.name for tool in tools}
            if tool_name not in known_names:
                raise CodexCLIError(
                    f"Codex CLI model selected unknown application tool {tool_name!r}."
                )
            parts = [
                ToolCallPart(
                    tool_name,
                    arguments,
                    tool_call_id=f"codex_{uuid4().hex}",
                )
            ]
            finish_reason = "tool_call"
        return ModelResponse(
            parts=parts,
            usage=_request_usage(result.usage),
            model_name=result.model_name,
            provider_name="codex-cli",
            finish_reason=finish_reason,
        )


def _resolve_backend_model(scope: ModelScope) -> Model:
    settings = get_settings()
    if is_codex_cli_backend():
        pro_model = str(settings.codex_model or "").strip() or None
        flash_model = str(settings.codex_flash_model or "").strip() or pro_model
        configured_model = pro_model if scope == "pro" else flash_model
        return CodexCLIModel(configured_model)
    model_name = settings.pro_model if scope == "pro" else settings.flash_model
    return _litellm_model(scope, model_name)


class BackendModel(Model):
    """Route each request using the current per-request application settings."""

    def __init__(self, scope: ModelScope) -> None:
        super().__init__()
        self.scope = scope

    def _resolve(self) -> Model:
        return _resolve_backend_model(self.scope)

    @property
    def settings(self) -> ModelSettings | None:
        return self._resolve().settings

    @property
    def profile(self) -> ModelProfile:
        return self._resolve().profile

    @property
    def model_name(self) -> str:
        return self._resolve().model_name

    @property
    def system(self) -> str:
        return self._resolve().system

    @property
    def base_url(self) -> str | None:
        return self._resolve().base_url

    def prepare_request(
        self,
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> tuple[ModelSettings | None, ModelRequestParameters]:
        return self._resolve().prepare_request(
            _runtime_model_settings(model_settings), model_request_parameters
        )

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        target = self._resolve()
        return await target.request(
            messages,
            _runtime_model_settings(model_settings),
            model_request_parameters,
        )

    async def count_tokens(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> RequestUsage:
        target = self._resolve()
        return await target.count_tokens(
            messages,
            _runtime_model_settings(model_settings),
            model_request_parameters,
        )

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: Any | None = None,
    ) -> AsyncIterator[StreamedResponse]:
        target = self._resolve()
        async with target.request_stream(
            messages,
            _runtime_model_settings(model_settings),
            model_request_parameters,
            run_context,
        ) as response:
            yield response


def runtime_model_summary_lines() -> list[str]:
    """Describe the models selected by the active backend for this request."""
    pro_model = _resolve_backend_model("pro")
    flash_model = _resolve_backend_model("flash")

    def describe(label: str, model: Model) -> str:
        provider = model.system
        if provider != "codex-cli" and "/" in model.model_name:
            provider = model.model_name.split("/", 1)[0]
        return f"{label} model: {model.model_name} / {provider}"

    lines = [
        describe("Pro", pro_model),
        describe("Flash", flash_model),
    ]
    if is_codex_cli_backend():
        lines.append("Embedding model: disabled for Codex CLI")
    else:
        embedding_model = get_settings().embedding_model
        provider = (
            embedding_model.split("/", 1)[0]
            if "/" in embedding_model
            else "unknown"
        )
        lines.append(f"Embedding model: {embedding_model} / {provider}")
    return lines


def runtime_reasoning_effort() -> str:
    """Return the reasoning level selected for the active backend."""
    return str((get_model_settings() or {}).get("reasoning_effort") or "default")
