"""Integration tests for the FastAPI routes (mocked DB and Celery)."""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Return a test client with DB and Celery mocked."""
    with (
        patch("app.main.async_engine"),
        patch("app.main.AsyncSessionLocal"),
        patch("app.main.Base.metadata.create_all"),
        patch("app.tasks.process_job"),
    ):
        from app.main import app
        with TestClient(app) as c:
            yield c


def _make_mock_job(status="QUEUED"):
    job = MagicMock()
    job.id = uuid.uuid4()
    job.status = MagicMock(value=status)
    job.stage = status
    job.preset = "transcode_h264_mp4"
    job.output_url = None
    job.created_at = datetime.now(timezone.utc)
    job.started_at = None
    job.finished_at = None
    job.duration_seconds = None
    job.error_message = None
    return job


def test_health_ok(client):
    with (
        patch("app.main.aioredis.from_url") as mock_redis,
        patch("app.main.AsyncSessionLocal") as mock_session,
    ):
        mock_redis_inst = AsyncMock()
        mock_redis.return_value = mock_redis_inst
        mock_session.return_value.__aenter__ = AsyncMock(return_value=AsyncMock())
        mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

        resp = client.get("/health")
    # Just check it returns JSON with expected keys
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data
    assert "api" in data


def test_create_job_invalid_preset(client):
    with patch("app.security.validate_input_url", return_value="https://cdn.example.com/v.mp4"):
        resp = client.post("/jobs", json={
            "preset": "nonexistent",
            "input_url": "https://cdn.example.com/v.mp4",
            "output_filename": "out.mp4",
        })
    assert resp.status_code == 400
    assert "Unknown preset" in resp.json()["detail"]


def test_create_job_http_url_rejected(client):
    resp = client.post("/jobs", json={
        "preset": "transcode_h264_mp4",
        "input_url": "http://cdn.example.com/v.mp4",
        "output_filename": "out.mp4",
    })
    assert resp.status_code == 422  # pydantic validation


def test_get_job_not_found(client):
    fake_id = uuid.uuid4()
    with patch("app.main.AsyncSessionLocal") as mock_session:
        mock_db = AsyncMock()
        mock_db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=None))
        mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

        resp = client.get(f"/jobs/{fake_id}")
    assert resp.status_code == 404
