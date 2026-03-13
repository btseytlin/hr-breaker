"""Tests for FastAPI server endpoints."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from hr_breaker.server import app, _cleanup_active
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
async def test_paste_resume_empty(client):
    resp = await client.post("/api/resume/paste", json={"content": "  "})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_paste_resume(client):
    with patch("hr_breaker.server.extract_name", new_callable=AsyncMock, return_value=("John", "Doe", "en")):
        resp = await client.post("/api/resume/paste", json={"content": "John Doe\nSoftware Engineer"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["first_name"] == "John"
    assert data["last_name"] == "Doe"
    assert data["checksum"]


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
