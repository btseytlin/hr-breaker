"""Tests for FastAPI server endpoints."""

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic_ai.exceptions import ModelHTTPError

import hr_breaker.config as config_module
from hr_breaker.models.resume import ResumeSource
from hr_breaker.server import app
from hr_breaker.services.cache import ResumeCache
from hr_breaker.services.profile_store import ProfileStore
import hr_breaker.server as server_module


@pytest.fixture(autouse=True)
def _clean_server_state():
    """Reset server state between tests."""
    server_module._active_optimization = None
    yield
    server_module._active_optimization = None


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_index(client):
    resp = await client.get("/")
    assert resp.status_code == 200
    assert "HR-Breaker" in resp.text


@pytest.mark.asyncio
async def test_settings(client):
    resp = await client.get("/api/settings")
    assert resp.status_code == 200
    data = resp.json()
    assert "language_modes" in data
    assert "pro_model" in data
    assert "flash_model" in data
    assert "max_iterations" in data


@pytest.mark.asyncio
async def test_cached_resumes_empty(client):
    resp = await client.get("/api/resume/cached")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_cached_resumes_include_profile_generated_entries_with_origin(client, tmp_path):
    visible = ResumeSource(content="Pasted resume", first_name="Jane", filename="pasted", source_type="paste")
    profile_resume = ResumeSource(
        content="Profile: Candidate\n\nProfile-generated resume",
        first_name="Jane",
        source_type="profile",
        source_profile_id="candidate",
        source_profile_name="Candidate",
    )
    with patch("hr_breaker.services.cache.get_settings", return_value=MagicMock(cache_dir=tmp_path)):
        ResumeCache().put(visible)
        ResumeCache().put(profile_resume)
        resp = await client.get("/api/resume/cached")
    assert resp.status_code == 200
    data = resp.json()
    assert [item["source_type"] for item in data] == ["profile", "paste"]
    assert data[0]["source_profile_id"] == "candidate"
    assert data[0]["source_profile_name"] == "Candidate"
    assert data[1]["filename"] == "pasted"

@pytest.mark.asyncio
async def test_resume_upload_applies_llm_overrides(client, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("FLASH_MODEL", raising=False)
    monkeypatch.delenv("FLASH_OPENAI_API_BASE", raising=False)
    config_module.clear_settings_cache()
    try:
        async def fake_extract_name(content):
            assert content == "John Doe\nSoftware Engineer"
            assert os.environ.get("OPENAI_API_KEY") == "sk-test"
            assert os.environ.get("FLASH_MODEL") == "openai/gpt-5.3-codex"
            assert os.environ.get("FLASH_OPENAI_API_BASE") == "https://example.test/v1"
            return ("John", "Doe", "en")

        with patch("hr_breaker.server.extract_name", new=AsyncMock(side_effect=fake_extract_name)):
            resp = await client.post(
                "/api/resume/upload",
                files={"file": ("resume.txt", b"John Doe\nSoftware Engineer", "text/plain")},
                data={
                    "flash_model": "openai/gpt-5.3-codex",
                    "reasoning_effort": "medium",
                    "api_keys_json": json.dumps({"openai": "sk-test"}),
                    "providers_json": json.dumps({
                        "flash": {"provider": "custom", "base_url": "https://example.test/v1"}
                    }),
                },
            )
        assert resp.status_code == 200
        assert resp.json()["first_name"] == "John"
    finally:
        config_module.clear_settings_cache()

@pytest.mark.asyncio
async def test_resume_upload_rejects_malformed_override_json(client):
    resp = await client.post(
        "/api/resume/upload",
        files={"file": ("resume.txt", b"John Doe\nSoftware Engineer", "text/plain")},
        data={
            "api_keys_json": "{not-json}",
        },
    )

    assert resp.status_code == 400
    assert "Invalid api_keys_json" in resp.json()["error"]


@pytest.mark.asyncio
async def test_paste_resume_empty(client):
    resp = await client.post("/api/resume/paste", json={"content": "  "})
    assert resp.status_code == 400

@pytest.mark.asyncio
async def test_paste_resume(client, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("FLASH_MODEL", raising=False)
    monkeypatch.delenv("FLASH_OPENAI_API_BASE", raising=False)
    config_module.clear_settings_cache()
    try:
        async def fake_extract_name(content):
            assert content == "John Doe\nSoftware Engineer"
            assert os.environ.get("OPENAI_API_KEY") == "sk-test"
            assert os.environ.get("FLASH_MODEL") == "openai/gpt-5.3-codex"
            assert os.environ.get("FLASH_OPENAI_API_BASE") == "https://example.test/v1"
            return ("John", "Doe", "en")

        with patch("hr_breaker.server.extract_name", new=AsyncMock(side_effect=fake_extract_name)):
            resp = await client.post(
                "/api/resume/paste",
                json={
                    "content": "John Doe\nSoftware Engineer",
                    "flash_model": "openai/gpt-5.3-codex",
                    "reasoning_effort": "medium",
                    "api_keys": {"openai": "sk-test"},
                    "providers": {
                        "flash": {"provider": "custom", "base_url": "https://example.test/v1"}
                    },
                },
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["first_name"] == "John"
        assert data["last_name"] == "Doe"
        assert data["checksum"]
    finally:
        config_module.clear_settings_cache()


@pytest.mark.asyncio
async def test_history_empty(client):
    resp = await client.get("/api/history")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_optimize_status_no_active(client):
    resp = await client.get("/api/optimize/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["active"] is False


@pytest.mark.asyncio
async def test_cancel_no_active(client):
    resp = await client.post("/api/optimize/cancel")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_optimize_missing_resume(client):
    resp = await client.post("/api/optimize", json={
        "resume_checksum": "nonexistent",
        "job_text": "Software engineer at Acme",
    })
    assert resp.status_code == 400
    assert "not found" in resp.json()["error"].lower()


@pytest.mark.asyncio
async def test_optimize_conflict(client):
    """Starting optimization while one is running returns 409."""
    # Create a fake active optimization
    fake_task = MagicMock()
    fake_task.done.return_value = False
    server_module._active_optimization = {
        "id": "fake-id",
        "task": fake_task,
        "events": [],
        "subscribers": [],
    }

    resp = await client.post("/api/optimize", json={
        "resume_checksum": "some-checksum",
        "job_text": "Software engineer",
    })
    assert resp.status_code == 409
    assert resp.json()["id"] == "fake-id"


@pytest.mark.asyncio
async def test_stream_nonexistent(client):
    resp = await client.get("/api/optimize/stream/nonexistent-id")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_pdf_not_found(client):
    resp = await client.get("/api/pdf/nonexistent.pdf")
    assert resp.status_code == 404

@pytest.mark.asyncio
async def test_profile_document_upload_rejects_malformed_override_json(client, monkeypatch, tmp_path):
    monkeypatch.setenv("PROFILE_DIR", str(tmp_path))
    config_module.clear_settings_cache()
    try:
        store = ProfileStore()
        profile = store.create_profile("Candidate")

        resp = await client.post(
            f"/api/profile/{profile.id}/document",
            files={"file": ("resume.txt", b"resume text", "text/plain")},
            data={"providers_json": "{not-json}"},
        )

        assert resp.status_code == 400
        assert "Invalid providers_json" in resp.json()["error"]
    finally:
        config_module.clear_settings_cache()


@pytest.mark.asyncio
async def test_profile_document_upload_forwards_llm_overrides(client, monkeypatch, tmp_path):
    monkeypatch.setenv("PROFILE_DIR", str(tmp_path))
    config_module.clear_settings_cache()
    try:
        store = ProfileStore()
        profile = store.create_profile("Candidate")

        with patch("hr_breaker.services.extraction_worker.extraction_worker.submit") as mock_submit:
            resp = await client.post(
                f"/api/profile/{profile.id}/document",
                files={"file": ("resume.txt", b"resume text", "text/plain")},
                data={
                    "flash_model": "openai/gpt-5.3-codex",
                    "reasoning_effort": "medium",
                    "api_keys_json": json.dumps({"openai": "sk-test"}),
                    "providers_json": json.dumps({
                        "flash": {"provider": "custom", "base_url": "https://example.test/v1"}
                    }),
                },
            )

        assert resp.status_code == 200
        doc_id = resp.json()["id"]
        mock_submit.assert_called_once_with(
            profile.id,
            [doc_id],
            overrides={
                "flash_model": "openai/gpt-5.3-codex",
                "reasoning_effort": "medium",
                "api_keys": {"openai": "sk-test"},
                "flash_openai_api_base": "https://example.test/v1",
            },
        )
    finally:
        config_module.clear_settings_cache()


@pytest.mark.asyncio
async def test_check_provider_forwards_request_and_returns_catalog(client):
    expected = {
        "status": {
            "state": "connected",
            "message": "Connected · 1 chat / 0 embedding",
            "detail": "catalog loaded",
        },
        "chat_models": [{"value": "openai/gpt-4.1-mini", "label": "gpt-4.1-mini"}],
        "embedding_models": [],
    }

    with patch(
        "hr_breaker.services.llm_providers.fetch_provider_catalog",
        new=AsyncMock(return_value=expected),
    ) as mock_fetch:
        resp = await client.post(
            "/api/providers/check",
            json={
                "provider": "custom",
                "api_key": "sk-test",
                "base_url": "https://example.test/v1",
            },
        )

    assert resp.status_code == 200
    assert resp.json() == expected
    mock_fetch.assert_awaited_once_with(
        provider="custom",
        api_key="sk-test",
        base_url="https://example.test/v1",
    )

@pytest.mark.asyncio
async def test_re_extract_profile_forwards_llm_overrides(client, monkeypatch, tmp_path):
    monkeypatch.setenv("PROFILE_DIR", str(tmp_path))
    config_module.clear_settings_cache()
    try:
        store = ProfileStore()
        profile = store.create_profile("Candidate")
        doc = store.add_note(profile.id, title="Resume", content_text="resume text")

        with patch("hr_breaker.services.extraction_worker.extraction_worker.resubmit") as mock_resubmit:
            resp = await client.post(
                f"/api/profile/{profile.id}/extract",
                json={
                    "flash_model": "openai/gpt-5.3-codex",
                    "reasoning_effort": "medium",
                    "api_keys": {"openai": "sk-test"},
                    "providers": {
                        "flash": {
                            "provider": "custom",
                            "base_url": "https://example.test/v1",
                        },
                    },
                },
            )

        assert resp.status_code == 200
        assert resp.json() == {"submitted": 1}
        mock_resubmit.assert_called_once_with(
            profile.id,
            [doc.id],
            overrides={
                "flash_model": "openai/gpt-5.3-codex",
                "reasoning_effort": "medium",
                "api_keys": {"openai": "sk-test"},
                "flash_openai_api_base": "https://example.test/v1",
            },
        )
    finally:
        config_module.clear_settings_cache()


@pytest.mark.asyncio
async def test_delete_profile_document_cancels_worker_before_removal(client, monkeypatch, tmp_path):
    monkeypatch.setenv("PROFILE_DIR", str(tmp_path))
    config_module.clear_settings_cache()
    try:
        store = ProfileStore()
        profile = store.create_profile("Candidate")
        doc = store.add_note(profile.id, title="Resume", content_text="resume text")

        with patch("hr_breaker.services.extraction_worker.extraction_worker.cancel") as mock_cancel:
            resp = await client.delete(f"/api/profile/{profile.id}/document/{doc.id}")

        assert resp.status_code == 200
        mock_cancel.assert_called_once_with([doc.id])
        assert store.get_document(profile.id, doc.id) is None
    finally:
        config_module.clear_settings_cache()



@pytest.mark.asyncio
async def test_synthesize_profile_applies_llm_overrides(client, monkeypatch, tmp_path):
    monkeypatch.setenv("PROFILE_DIR", str(tmp_path))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)
    config_module.clear_settings_cache()
    try:
        store = ProfileStore()
        profile = store.create_profile("Candidate")
        included_doc = store.add_note(profile.id, title="Resume", content_text="resume text")
        skipped_doc = store.add_note(
            profile.id,
            title="Archive",
            content_text="archived text",
            included_by_default=False,
        )

        async def fake_rank_profile_documents(documents, job):
            assert [document.id for document in documents] == [included_doc.id]
            assert os.environ.get("OPENAI_API_KEY") == "sk-test"
            assert os.environ.get("EMBEDDING_OPENAI_API_BASE") == "https://example.test/v1"
            assert os.environ.get("OPENAI_API_BASE") is None
            return []

        with patch(
            "hr_breaker.services.profile_retrieval.rank_profile_documents",
            new=AsyncMock(side_effect=fake_rank_profile_documents),
        ), patch(
            "hr_breaker.services.profile_retrieval.synthesize_profile_resume_source",
            return_value=ResumeSource(content="profile resume", first_name="Jane", last_name="Doe"),
        ):
            resp = await client.post(
                f"/api/profile/{profile.id}/synthesize",
                json={
                    "job_text": "Product manager role",
                    "selected_doc_ids": [included_doc.id],
                    "embedding_model": "openai/text-embedding-3-small",
                    "api_keys": {"openai": "sk-test"},
                    "providers": {
                        "embedding": {
                            "provider": "custom",
                            "base_url": "https://example.test/v1",
                        },
                    },
                },
            )

        assert resp.status_code == 200
        assert resp.json()["first_name"] == "Jane"
        assert resp.json()["last_name"] == "Doe"
        assert skipped_doc.id != included_doc.id
    finally:
        config_module.clear_settings_cache()



@pytest.mark.asyncio
async def test_synthesize_profile_rejects_unknown_selected_doc_ids(client, monkeypatch, tmp_path):
    monkeypatch.setenv("PROFILE_DIR", str(tmp_path))
    config_module.clear_settings_cache()
    try:
        store = ProfileStore()
        profile = store.create_profile("Candidate")
        store.add_note(profile.id, title="Resume", content_text="resume text")

        resp = await client.post(
            f"/api/profile/{profile.id}/synthesize",
            json={
                "job_text": "Product manager role",
                "selected_doc_ids": ["missing-doc"],
            },
        )

        assert resp.status_code == 400
        assert resp.json()["error"] == "Unknown profile documents in synthesis scope"
    finally:
        config_module.clear_settings_cache()


def test_build_overrides_maps_scoped_custom_base_urls():
    req = server_module.OptimizeRequest(
        resume_checksum="resume-checksum",
        job_text="Product manager role",
        flash_model="openai/gpt-5.3-codex",
        embedding_model="openai/text-embedding-3-small",
        api_keys={"openai": "sk-test"},
        providers={
            "flash": server_module.ProviderOverride(provider="custom", base_url="https://example.test/v1"),
            "embedding": server_module.ProviderOverride(provider="custom", base_url="https://embed.example.test/v1"),
        },
        filter_thresholds={"vector": 0.5},
    )

    overrides = server_module._build_overrides(req)

    assert overrides == {
        "flash_model": "openai/gpt-5.3-codex",
        "embedding_model": "openai/text-embedding-3-small",
        "api_keys": {"openai": "sk-test"},
        "flash_openai_api_base": "https://example.test/v1",
        "embedding_openai_api_base": "https://embed.example.test/v1",
        "filter_vector_threshold": 0.5,
    }


def test_normalize_optimization_error_for_custom_provider_none_type_failure():
    req = server_module.OptimizeRequest(
        resume_checksum="resume-checksum",
        job_text="Product manager role",
        pro_model="openai/gpt-5.4",
        flash_model="openai/gpt-5.3-codex",
        embedding_model="openai/text-embedding-3-small",
        providers={
            "flash": server_module.ProviderOverride(
                provider="custom",
                base_url="http://127.0.0.1:8317/v1",
            ),
        },
    )
    exc = ModelHTTPError(
        status_code=500,
        model_name="openai/gpt-5.3-codex",
        body="litellm.APIConnectionError: APIConnectionError: OpenAIException - argument of type 'NoneType' is not iterable",
    )

    message = server_module._normalize_optimization_error(exc, req)

    assert "Custom OpenAI-compatible provider request failed before the model returned a usable response" in message
    assert "flash: http://127.0.0.1:8317/v1" in message
    assert "openai/gpt-5.4, openai/gpt-5.3-codex, openai/text-embedding-3-small" in message