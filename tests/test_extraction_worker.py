import os
from unittest.mock import patch

import hr_breaker.config as config_module
from hr_breaker.services.extraction_worker import ExtractionWorker
from hr_breaker.services.profile_store import ProfileStore


def test_extraction_worker_applies_overrides_during_background_run(tmp_path, monkeypatch):
    monkeypatch.setenv("PROFILE_DIR", str(tmp_path))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)
    config_module.clear_settings_cache()
    try:
        store = ProfileStore()
        profile = store.create_profile("Candidate")
        doc = store.add_note(profile.id, title="Resume", content_text="resume text")
        observed = {}

        async def fake_extract_document_content(self, profile_id, document_id):
            observed["profile_id"] = profile_id
            observed["document_id"] = document_id
            observed["api_key"] = os.environ.get("OPENAI_API_KEY")
            observed["api_base"] = os.environ.get("OPENAI_API_BASE")
            return self.get_document(profile_id, document_id)

        worker = ExtractionWorker(max_workers=1)
        with patch(
            "hr_breaker.services.profile_store.ProfileStore.extract_document_content",
            new=fake_extract_document_content,
        ):
            worker._run(
                profile.id,
                doc.id,
                overrides={
                    "api_keys": {"openai": "sk-test"},
                    "openai_api_base": "https://example.test/v1",
                },
            )

        assert observed == {
            "profile_id": profile.id,
            "document_id": doc.id,
            "api_key": "sk-test",
            "api_base": "https://example.test/v1",
        }
        assert worker.get_status(doc.id) == "done"
    finally:
        config_module.clear_settings_cache()
