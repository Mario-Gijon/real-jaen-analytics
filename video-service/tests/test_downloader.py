from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from video_service.catalog import VideoCatalog
from video_service.downloader import DownloadError, classify_ytdlp_failure
from video_service.main import create_app
from http_client import async_asgi_client


@pytest.mark.parametrize(
    ("detail", "expected"),
    [
        ("No supported JavaScript runtime could be found", "extractor_unavailable"),
        ("yt-dlp-ejs package is missing", "extractor_unavailable"),
        ("Unable to download API page: Failed to resolve host", "network_error"),
        ("Temporary failure in name resolution", "network_error"),
        ("This video is private", "video_unavailable"),
        ("This video is not available", "video_unavailable"),
        ("Requested format is not available", "format_unavailable"),
        ("PostProcessingError: ffmpeg failed", "ffmpeg_error"),
        ("[Errno 13] Permission denied", "storage_error"),
        ("Unrecognized extractor failure", "download_failed"),
    ],
)
def test_ytdlp_failure_categories(detail: str, expected: str):
    assert classify_ytdlp_failure(detail) == expected


@pytest.mark.anyio
async def test_job_persists_safe_download_failure_category_and_skips_ocr(tmp_path):
    def failed_download(_url: str, _video_id: str, _job_dir: Path, *, max_bytes: int) -> Path:
        assert max_bytes > 0
        raise DownloadError(
            "No se pudo conectar con YouTube por un problema de red o DNS. Inténtalo de nuevo más tarde.",
            error_code="network_error",
            technical_type="yt_dlp.utils.DownloadError",
            exit_code=1,
        )

    def forbidden_probe(_path: Path):
        pytest.fail("ffprobe no debe ejecutarse después de fallar la descarga")

    def forbidden_calibration(*_args, **_kwargs):
        pytest.fail("OCR no debe ejecutarse después de fallar la descarga")

    app = create_app(
        VideoCatalog([]),
        state_dir=tmp_path / "state",
        temp_dir=tmp_path / "temporary",
        downloader=failed_download,
        probe=forbidden_probe,
        calibrator=forbidden_calibration,
    )

    async with async_asgi_client(app) as client:
        response = await client.post("/api/v1/video-jobs", json={
            "match_ref": "fixture-match-download",
            "youtube_url": "https://www.youtube.com/watch?v=a1B2c3D4e5F",
            "sports_events": None,
        })
        assert response.status_code == 202
        job_id = response.json()["job_id"]
        for _ in range(100):
            result = await client.get(f"/api/v1/video-jobs/{job_id}")
            assert result.status_code == 200
            if result.json()["status"] == "failed":
                break
            await asyncio.sleep(0.01)
        job = result.json()
        assert job["status"] == "failed"
        assert job["error_code"] == "network_error"
        assert "DNS" in job["message"]
        assert "reproducción pública" not in job["message"]
        assert not list((tmp_path / "temporary").iterdir())
