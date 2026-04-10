from types import SimpleNamespace

from hr_breaker.orchestration import (
    _optimization_settings_summary_lines,
    _optimizer_changes_log_message,
)


def test_optimization_settings_summary_lines_are_concise_and_safe():
    settings = SimpleNamespace(
        pro_model="openai/gpt-5.4",
        flash_model="openai/gpt-5.3-codex",
        embedding_model="gemini/gemini-embedding-2-preview",
        reasoning_effort="medium",
        gemini_api_key="secret-gemini-key",
        openai_api_base="http://127.0.0.1:8317/v1",
        cache_dir=".cache/resumes",
    )

    lines = _optimization_settings_summary_lines(
        settings,
        max_iterations=1,
        parallel=True,
        no_shame=False,
    )

    assert lines == [
        "Pro model: openai/gpt-5.4 / openai",
        "Flash model: openai/gpt-5.3-codex / openai",
        "Embedding model: gemini/gemini-embedding-2-preview / gemini",
        "Optimization mode: parallel, reasoning: medium, max iterations: 1, no-shame: False",
    ]
    for line in lines:
        assert "secret-gemini-key" not in line
        assert "openai_api_base" not in line
        assert "cache_dir" not in line


def test_optimizer_changes_log_message_uses_bullets():
    assert _optimizer_changes_log_message(["First", "Second"]) == (
        "Optimizer changes:\n"
        "- First\n"
        "- Second"
    )


def test_optimizer_changes_log_message_handles_empty_changes():
    assert _optimizer_changes_log_message([]) == "Optimizer changes: none"