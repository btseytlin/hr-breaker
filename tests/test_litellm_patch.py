"""Tests for litellm vision patch."""

import asyncio
import base64
import inspect

import litellm
import pytest
from litellm.litellm_core_utils.logging_worker import LoggingWorker
from pydantic_ai.messages import (
    BinaryContent,
    ImageUrl,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    UserPromptPart,
    )

from hr_breaker.litellm_patch import (
    _convert_user_content,
    _patched_map_messages,
    apply,
 )

class TestConvertUserContent:
    def test_plain_string(self):
        assert _convert_user_content("hello") == "hello"

    def test_string_items_become_text_parts(self):
        result = _convert_user_content(["hello", "world"])
        assert result == [
            {"type": "text", "text": "hello"},
            {"type": "text", "text": "world"},
        ]

    def test_binary_image_becomes_base64_image_url(self):
        png_bytes = b"\x89PNG fake image data"
        content = BinaryContent(data=png_bytes, media_type="image/png")
        result = _convert_user_content([content])

        assert len(result) == 1
        part = result[0]
        assert part["type"] == "image_url"
        url = part["image_url"]["url"]
        assert url.startswith("data:image/png;base64,")
        decoded = base64.b64decode(url.split(",", 1)[1])
        assert decoded == png_bytes

    def test_mixed_text_and_image(self):
        png_bytes = b"img"
        result = _convert_user_content([
            "Describe this image:",
            BinaryContent(data=png_bytes, media_type="image/png"),
        ])
        assert len(result) == 2
        assert result[0] == {"type": "text", "text": "Describe this image:"}
        assert result[1]["type"] == "image_url"

    def test_image_url_object(self):
        result = _convert_user_content([
            ImageUrl(url="https://example.com/img.png"),
        ])
        assert result == [
            {"type": "image_url", "image_url": {"url": "https://example.com/img.png"}},
        ]

    def test_non_image_binary_falls_back_to_text(self):
        result = _convert_user_content([
            BinaryContent(data=b"audio data", media_type="audio/mp3"),
        ])
        assert result == [
            {"type": "text", "text": "[audio/mp3 binary content]"},
        ]


class TestPatchedMapMessages:
    @pytest.mark.asyncio
    async def test_system_and_user_text(self):
        messages = [
            ModelRequest(parts=[
                SystemPromptPart(content="You are helpful"),
                UserPromptPart(content="Hello"),
            ]),
        ]
        # Call as unbound method (self=None since we don't need instance state)
        result = await _patched_map_messages(None, messages)
        assert result == [
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "Hello"},
        ]

    @pytest.mark.asyncio
    async def test_user_with_image(self):
        png_bytes = b"fake png"
        messages = [
            ModelRequest(parts=[
                UserPromptPart(content=[
                    "Check this resume:",
                    BinaryContent(data=png_bytes, media_type="image/png"),
                ]),
            ]),
        ]
        result = await _patched_map_messages(None, messages)
        assert len(result) == 1
        msg = result[0]
        assert msg["role"] == "user"
        assert isinstance(msg["content"], list)
        assert len(msg["content"]) == 2
        assert msg["content"][0] == {"type": "text", "text": "Check this resume:"}
        assert msg["content"][1]["type"] == "image_url"

    @pytest.mark.asyncio
    async def test_model_response_with_text(self):
        messages = [
            ModelResponse(parts=[TextPart(content="Sure, here's the answer")]),
        ]
        result = await _patched_map_messages(None, messages)
        assert result == [
            {"role": "assistant", "content": "Sure, here's the answer"},
        ]

    @pytest.mark.asyncio
    async def test_model_response_with_tool_call(self):
        messages = [
            ModelResponse(parts=[
                ToolCallPart(
                    tool_name="check_length",
                    args='{"html": "<p>test</p>"}',
                    tool_call_id="call_1",
                ),
            ]),
        ]
        result = await _patched_map_messages(None, messages)
        assert len(result) == 1
        msg = result[0]
        assert msg["role"] == "assistant"
        assert len(msg["tool_calls"]) == 1
        assert msg["tool_calls"][0]["function"]["name"] == "check_length"


    @pytest.mark.asyncio
    async def test_system_messages_are_hoisted_before_user_messages(self):
        messages = [
            ModelRequest(parts=[UserPromptPart(content="Hello first")]),
            ModelRequest(parts=[SystemPromptPart(content="Late system")]),
        ]

        result = await _patched_map_messages(None, messages)
        assert result == [
            {"role": "system", "content": "Late system"},
            {"role": "user", "content": "Hello first"},
        ]

@pytest.fixture(autouse=True)
def _reset_litellm_callbacks():
    original_success = list(litellm.success_callback)
    original_async_success = list(litellm._async_success_callback)
    original_failure = list(litellm.failure_callback)
    original_async_failure = list(litellm._async_failure_callback)
    apply()
    litellm.success_callback[:] = []
    litellm._async_success_callback[:] = []
    litellm.failure_callback[:] = []
    litellm._async_failure_callback[:] = []
    try:
        yield
    finally:
        litellm.success_callback[:] = original_success
        litellm._async_success_callback[:] = original_async_success
        litellm.failure_callback[:] = original_failure
        litellm._async_failure_callback[:] = original_async_failure


class TestLoggingWorkerPatch:
    @pytest.mark.asyncio
    async def test_skips_worker_when_no_async_callbacks_exist(self):
        worker = LoggingWorker()

        async def noop():
            return None

        coro = noop()
        worker.ensure_initialized_and_enqueue(coro)
        await asyncio.sleep(0)

        assert worker._worker_task is None
        assert inspect.getcoroutinestate(coro) == inspect.CORO_CLOSED

    @pytest.mark.asyncio
    async def test_starts_worker_when_async_callbacks_exist(self):
        worker = LoggingWorker()
        litellm._async_success_callback.append(object())
        processed = asyncio.Event()

        async def noop():
            processed.set()
            return None

        worker.ensure_initialized_and_enqueue(noop())
        await asyncio.wait_for(processed.wait(), timeout=1)

        assert worker._worker_task is not None
        await worker.stop()

    @pytest.mark.asyncio
    async def test_starts_worker_when_dynamic_async_callbacks_exist(self):
        worker = LoggingWorker()
        processed = asyncio.Event()

        class FakeLoggingObj:
            dynamic_async_success_callbacks = [object()]
            dynamic_async_failure_callbacks = []

            async def async_success_handler(self):
                processed.set()
                return None

        logging_obj = FakeLoggingObj()
        worker.ensure_initialized_and_enqueue(logging_obj.async_success_handler())
        await asyncio.wait_for(processed.wait(), timeout=1)

        assert worker._worker_task is not None
        await worker.stop()