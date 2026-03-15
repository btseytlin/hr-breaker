import asyncio
from unittest.mock import AsyncMock, patch

from click.testing import CliRunner

from hr_breaker.cli import cli
import hr_breaker.config as config_module
from hr_breaker.models.profile import DocumentExtraction
from hr_breaker.services.profile_store import ProfileStore


def test_profile_show_reports_empty_extraction(monkeypatch, tmp_path):
    monkeypatch.setenv("PROFILE_DIR", str(tmp_path / "profiles"))
    config_module.clear_settings_cache()
    try:
        store = ProfileStore()
        profile = store.create_profile("Candidate")
        doc = store.add_note(profile.id, title="Resume", content_text="raw text")

        with patch(
            "hr_breaker.agents.extractor.extract_document",
            new=AsyncMock(return_value=DocumentExtraction()),
        ):
            asyncio.run(store.extract_document_content(profile.id, doc.id))

        result = CliRunner().invoke(cli, ["profile", "show", profile.id])

        assert result.exit_code == 0
        assert "empty extraction" in result.output
        assert "[note, extracted]" not in result.output
    finally:
        config_module.clear_settings_cache()


def test_backfill_reports_empty_extraction_separately(monkeypatch, tmp_path):
    monkeypatch.setenv("PROFILE_DIR", str(tmp_path / "profiles"))
    config_module.clear_settings_cache()
    try:
        store = ProfileStore()
        profile = store.create_profile("Candidate")
        store.add_note(profile.id, title="Resume", content_text="raw text")

        with patch(
            "hr_breaker.agents.extractor.extract_document",
            new=AsyncMock(return_value=DocumentExtraction()),
        ):
            result = CliRunner().invoke(cli, ["backfill", "--profile", profile.id])

        assert result.exit_code == 0
        assert "Resume... empty" in result.output
        assert "Done: 0 extracted, 1 empty, 0 failed, 1 total" in result.output
    finally:
        config_module.clear_settings_cache()
