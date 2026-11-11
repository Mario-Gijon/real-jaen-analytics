from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import sqlite3
import time

import pytest
from video_service.catalog import VideoCatalog
from video_service.main import create_app
from video_service.state import VideoStateStore
from video_service.youtube import InvalidYouTubeUrl, youtube_video_id
from http_client import async_asgi_client


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "tests" / "fixtures" / "catalog"
SAMPLE_MATCH_ID = "fixture-match-full"
OTHER_MATCH_ID = "fixture-match-other"
SAMPLE_VIDEO_ID = "a1B2c3D4e5F"
OTHER_VIDEO_ID = "f6G7h8I9j0K"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (f"https://www.youtube.com/watch?v={SAMPLE_VIDEO_ID}", SAMPLE_VIDEO_ID),
        (f"https://youtu.be/{SAMPLE_VIDEO_ID}?si=tracking", SAMPLE_VIDEO_ID),
        (f"https://youtube.com/live/{SAMPLE_VIDEO_ID}", SAMPLE_VIDEO_ID),
    ],
)
def test_youtube_urls_are_validated_and_canonical_id_is_extracted(url, expected):
    assert youtube_video_id(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        f"http://youtube.com/watch?v={SAMPLE_VIDEO_ID}",
        f"https://youtube.com.evil.invalid/watch?v={SAMPLE_VIDEO_ID}",
        f"https://user:pass@youtube.com/watch?v={SAMPLE_VIDEO_ID}",
        "https://127.0.0.1/watch?v=79Rngk2hcC0",
        "https://youtube.com/watch?v=short",
    ],
)
def test_youtube_url_rejects_non_public_or_malformed_targets(url):
    with pytest.raises(InvalidYouTubeUrl):
        youtube_video_id(url)


def _fake_processing(calibration: dict):
    calls = {"downloads": 0, "calibrations": 0, "sports_events": None, "sports_events_source": None}

    def downloader(source_url: str, video_id: str, job_dir: Path, *, max_bytes: int) -> Path:
        calls["downloads"] += 1
        job_dir.mkdir(parents=True)
        target = job_dir / "broadcast.mp4"
        target.write_bytes(b"isolated mock mp4")
        return target

    def probe(path: Path) -> dict:
        return {"duration_seconds": 7200.0, "width": 1920, "height": 1080, "codec": "h264"}

    def calibrator(video_path: Path, *, output_path: Path, report_dir: Path,
                   sports_events, sports_events_source):
        calls["calibrations"] += 1
        calls["sports_events"] = sports_events
        calls["sports_events_source"] = sports_events_source
        report_dir.mkdir(parents=True, exist_ok=True)
        (report_dir / "temporary-report.txt").write_text("temporary", encoding="utf-8")
        document = json.loads(json.dumps(calibration))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(document), encoding="utf-8")
        return {"status": document["status"]}

    return downloader, probe, calibrator, calls


async def _wait_for_status(client, job_id: str, expected: str) -> dict:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        response = await client.get(f"/api/v1/video-jobs/{job_id}")
        assert response.status_code == 200
        job = response.json()
        if job["status"] == expected:
            return job
        if job["status"] == "failed":
            pytest.fail(f"El trabajo de prueba falló: {job['message']}")
        await asyncio.sleep(0.01)
    pytest.fail(f"El trabajo no llegó al estado {expected}.")


def test_http_job_saves_verified_calibration_and_cleans_temp_files(tmp_path):
    source_catalog = VideoCatalog.load(DATA)
    source_calibration = source_catalog.get("sample-full-match").calibration
    assert source_calibration is not None
    downloader, probe, calibrator, calls = _fake_processing(source_calibration)
    state_dir = tmp_path / "state"
    temp_dir = tmp_path / "temporary"
    app = create_app(
        VideoCatalog([]), state_dir=state_dir, temp_dir=temp_dir,
        downloader=downloader, probe=probe, calibrator=calibrator,
    )
    sports_events = {
        "contract": "video-sync.sports-events",
        "version": 1,
        "events": [{
            "source_order": 0, "source_id": "begin", "original_type": "matchStarted",
            "normalized_type": "match_start", "period": "first", "source_minute": 1,
            "game_clock_seconds": None, "source_period_name": "1ST", "source_text": "Inicio.",
            "source_position": 1,
        }],
    }
    async def exercise_job():
        async with async_asgi_client(app) as client:
            response = await client.post("/api/v1/video-jobs", json={
                "match_ref": "fixture.match:one",
                "youtube_url": f"https://youtu.be/{OTHER_VIDEO_ID}?si=synthetic-tracker",
                "sports_events": sports_events,
            })
            assert response.status_code == 202
            initial_job = response.json()
            assert initial_job["status"] in {"pending", "downloading", "calibrating", "saving", "completed"}
            assert initial_job["match_ref"] == "fixture.match:one"
            assert app.state.video_state.processing_input(initial_job["job_id"])["sports_events"] == sports_events

            job = await _wait_for_status(client, initial_job["job_id"], "completed")
            assert job["message"].endswith("El MP4 temporal se eliminó.")
            assert "eventos deportivos recibidos" in job["message"]
            records = (await client.get("/api/v1/videos")).json()["items"]
            assert len(records) == 1
            record = records[0]
            assert record["match_ref"] == "fixture.match:one"
            assert record["source_url"] == f"https://www.youtube.com/watch?v={OTHER_VIDEO_ID}"
            assert "synthetic-tracker" not in json.dumps(records)
            sync = await client.get(f"/api/v1/videos/{record['video_id']}/synchronization")
            assert sync.status_code == 200
            assert sync.json()["status"] == "calibrated"
            assert "/tmp/" not in sync.text and "/home/" not in sync.text
            assert not list(temp_dir.iterdir())
            assert calls == {
                "downloads": 1, "calibrations": 1,
                "sports_events": sports_events,
                "sports_events_source": "dashboard-bff:video-sync.sports-events/v1",
            }

            duplicate = await client.post("/api/v1/video-jobs", json={
                "match_ref": "fixture.match:one",
                "youtube_url": f"https://www.youtube.com/watch?v={OTHER_VIDEO_ID}",
                "sports_events": sports_events,
            })
            assert duplicate.status_code == 409
            assert calls["downloads"] == 1

            removed = await client.delete("/api/v1/matches/fixture.match:one/video")
            assert removed.status_code == 200
            assert (await client.get("/api/v1/videos")).json()["items"] == []
            historical_calibration = state_dir / "calibrations" / f"{record['video_id']}.json"
            assert historical_calibration.is_file()

            retry = await client.post("/api/v1/video-jobs", json={
                "match_ref": "fixture.match:one",
                "youtube_url": f"https://www.youtube.com/watch?v={OTHER_VIDEO_ID}",
                "sports_events": sports_events,
            })
            assert retry.status_code == 202
            await _wait_for_status(client, retry.json()["job_id"], "completed")
            replacement = (await client.get("/api/v1/videos")).json()["items"]
            assert len(replacement) == 1
            assert replacement[0]["video_id"] != record["video_id"]
            assert historical_calibration.is_file()
            assert len(list((state_dir / "calibrations").glob("*.json"))) == 2
            assert not list(temp_dir.iterdir())
            assert calls["downloads"] == 2
            assert calls["calibrations"] == 2

    asyncio.run(exercise_job())


@pytest.mark.anyio
async def test_invalid_sports_events_are_rejected_before_enqueue(tmp_path):
    downloader, probe, calibrator, calls = _fake_processing({"version": 1, "status": "calibrated", "segments": []})
    app = create_app(
        VideoCatalog([]), state_dir=tmp_path / "state", temp_dir=tmp_path / "temporary",
        downloader=downloader, probe=probe, calibrator=calibrator,
    )
    async with async_asgi_client(app) as client:
        response = await client.post("/api/v1/video-jobs", json={
            "match_ref": OTHER_MATCH_ID,
            "youtube_url": f"https://www.youtube.com/watch?v={OTHER_VIDEO_ID}",
            "sports_events": {"contract": "video-sync.sports-events", "version": 1, "events": [
                {"source_order": 0, "original_type": "matchStarted", "normalized_type": "match_start",
                 "period": "first", "game_clock_seconds": 3, "video_second": 12},
            ]},
        })
        assert response.status_code == 422
        assert "documento de eventos" in response.json()["detail"]
        assert calls["downloads"] == 0
        assert calls["calibrations"] == 0
        assert (await client.get("/api/v1/videos")).json()["items"] == []


@pytest.mark.anyio
async def test_delete_tombstone_survives_service_restart_without_touching_image_data(tmp_path):
    manifest_hash_before = hashlib.sha256((DATA / "videos.json").read_bytes()).hexdigest()
    source_catalog = VideoCatalog.load(DATA)
    state_dir = tmp_path / "state"
    temp_dir = tmp_path / "temporary"

    async with async_asgi_client(create_app(source_catalog, state_dir=state_dir, temp_dir=temp_dir)) as client:
        before = (await client.get("/api/v1/videos")).json()["items"]
        assert {item["match_ref"] for item in before} == {"fixture-match-full", "fixture-match-limited"}
        response = await client.delete(f"/api/v1/matches/{SAMPLE_MATCH_ID}/video")
        assert response.status_code == 200
        assert response.json()["available"] is False
        remaining = (await client.get("/api/v1/videos")).json()["items"]
        assert [item["match_ref"] for item in remaining] == ["fixture-match-limited"]

    async with async_asgi_client(create_app(VideoCatalog.load(DATA), state_dir=state_dir, temp_dir=temp_dir)) as restarted:
        after_restart = (await restarted.get("/api/v1/videos")).json()["items"]
        assert [item["match_ref"] for item in after_restart] == ["fixture-match-limited"]
        assert (await restarted.get(f"/api/v1/matches/{SAMPLE_MATCH_ID}/video-job")).json() is None

    assert (DATA / "calibrations/full-match.json").is_file()
    assert hashlib.sha256((DATA / "videos.json").read_bytes()).hexdigest() == manifest_hash_before


@pytest.mark.anyio
async def test_delete_is_rejected_while_a_match_job_is_active(tmp_path):
    state_dir = tmp_path / "state"
    temp_dir = tmp_path / "temporary"
    app = create_app(VideoCatalog.load(DATA), state_dir=state_dir, temp_dir=temp_dir)

    async with async_asgi_client(app) as client:
        app.state.video_state.create_job(
            "22222222-2222-4222-8222-222222222222",
            SAMPLE_MATCH_ID,
            OTHER_VIDEO_ID,
            f"https://www.youtube.com/watch?v={OTHER_VIDEO_ID}",
            None,
        )
        response = await client.delete(f"/api/v1/matches/{SAMPLE_MATCH_ID}/video")

        assert response.status_code == 409
        assert "mientras hay un procesamiento activo" in response.json()["detail"]
        items = (await client.get("/api/v1/videos")).json()["items"]
        assert {item["match_ref"] for item in items} == {"fixture-match-full", "fixture-match-limited"}


@pytest.mark.anyio
async def test_interrupted_jobs_and_abandoned_calibration_temps_are_cleaned_on_start(tmp_path):
    state_dir = tmp_path / "state"
    temp_dir = tmp_path / "temporary"
    app = create_app(VideoCatalog([]), state_dir=state_dir, temp_dir=temp_dir)
    store = VideoStateStore(state_dir)
    store.initialize()
    store.create_job("11111111-1111-4111-8111-111111111111", OTHER_MATCH_ID, OTHER_VIDEO_ID,
                     f"https://www.youtube.com/watch?v={OTHER_VIDEO_ID}", None)
    orphan = temp_dir / "job-11111111-1111-4111-8111-111111111111"
    orphan.mkdir(parents=True)
    (orphan / "large.mp4").write_bytes(b"temporary")
    calibration_temp = state_dir / "calibrations" / "orphan.job.tmp"
    calibration_temp.write_text("incomplete", encoding="utf-8")

    async with async_asgi_client(create_app(VideoCatalog([]), state_dir=state_dir, temp_dir=temp_dir)) as restarted:
        job = (await restarted.get("/api/v1/video-jobs/11111111-1111-4111-8111-111111111111")).json()
        assert job["status"] == "failed"
        assert job["error_code"] == "service_restarted"
        assert not orphan.exists()
        assert not calibration_temp.exists()


def test_existing_state_database_gets_additive_sports_events_column(tmp_path):
    store = VideoStateStore(tmp_path / "state")
    store.state_dir.mkdir(parents=True)
    with sqlite3.connect(store.db_path) as connection:
        connection.execute("""
            CREATE TABLE video_job (
                job_id TEXT PRIMARY KEY, match_id TEXT NOT NULL, youtube_video_id TEXT NOT NULL,
                source_url TEXT NOT NULL, status TEXT NOT NULL, message TEXT NOT NULL, error_code TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, processed_at TEXT, calibration_file TEXT
            )
        """)

    store.initialize()
    with sqlite3.connect(store.db_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(video_job)")}
    assert "sports_events_json" in columns
