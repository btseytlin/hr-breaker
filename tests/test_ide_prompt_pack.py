from pathlib import Path


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_optimizer_prompt_enforces_role_focus_and_section_policy():
    text = _read("ide/agents/optimizer.md")
    assert "Role focus is mandatory" in text
    assert "exclude `Languages`, `Location`, and `Hobbies`" in text
    assert "KeywordMatcher" in text
    assert ">= 0.55" in text
    assert "<strong>Tech:</strong>" in text


def test_optimize_workflow_prompts_include_strict_keyword_gate():
    root_prompt = _read("optimize.md")
    runner_prompt = _read("ide/agents/full_stack_runner.md")

    assert "--min-keyword-score 0.55" in root_prompt
    assert "--min-keyword-score 0.55" in runner_prompt
    assert "prioritize unresolved missing keywords" in root_prompt
    assert "KeywordMatcher" in runner_prompt
    assert "IDEKeywordGate" in runner_prompt


def test_job_parser_prompt_enforces_signal_completeness():
    text = _read("ide/agents/job_parser.md")
    assert "production delivery and scalability" in text
    assert "NLP/LLM/RAG" in text
    assert "integration/interop" in text
    assert "scikit-learn" in text
    assert "spaCy" in text
    assert "raw_text" in text
