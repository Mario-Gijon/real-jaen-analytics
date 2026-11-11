from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

pytest.importorskip("fastapi", reason="instala las dependencias del servicio para pruebas HTTP")
pytest.importorskip("httpx", reason="instala el extra test para pruebas HTTP")
from http_client import async_asgi_client

from video_service.catalog import CatalogError, VideoCatalog, VideoRecord
from video_service.main import create_app


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "tests" / "fixtures" / "catalog"
VIDEO_ID = "sample-full-match"


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="module")
def catalog() -> VideoCatalog:
    return VideoCatalog.load(DATA)


@pytest.fixture
def test_app(catalog, tmp_path):
    return create_app(catalog, state_dir=tmp_path / "state", temp_dir=tmp_path / "temporary")


def _app_for_record(record: VideoRecord, tmp_path):
    return create_app(VideoCatalog([record]), state_dir=tmp_path / "state", temp_dir=tmp_path / "temporary")


def _record_with_calibration(catalog, transform) -> VideoRecord:
    record = catalog.get(VIDEO_ID)
    assert record is not None and record.calibration is not None
    calibration = json.loads(json.dumps(record.calibration))
    transform(calibration)
    return replace(record, calibration=calibration)


def _assert_no_unique_position(body: dict) -> None:
    assert "result" not in body
    assert "video_second" not in body


@pytest.mark.anyio
async def test_health_and_video_list(test_app):
    async with async_asgi_client(test_app) as client:
        health = await client.get("/health")
        assert health.status_code == 200
        assert health.json() == {
            "status": "ok", "service": "real-jaen-video-service", "version": "0.1.0"
        }
        response = await client.get("/api/v1/videos")
        assert response.status_code == 200
        items = response.json()["items"]
        assert len(items) == 2
        assert {item["match_ref"] for item in items} == {"fixture-match-full", "fixture-match-limited"}
        assert all("calibration_file" not in item for item in items)
        assert "/home/" not in response.text


@pytest.mark.anyio
async def test_synchronization_returns_real_segments_without_server_paths(test_app):
    async with async_asgi_client(test_app) as client:
        response = await client.get(f"/api/v1/videos/{VIDEO_ID}/synchronization")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "calibrated"
        assert body["coverage"]["percent"] == pytest.approx(72.5)
        assert [segment["segment_id"] for segment in body["segments"]] == ["S01", "S02"]
        assert "/home/" not in response.text
        assert "calibration_file" not in response.text
        assert "video.path" not in response.text


@pytest.mark.parametrize(
    ("period", "minute", "expected_segment", "expected_second"),
    [
        ("first", "38", "S01", 3260),
        ("second", "58", "S02", 5460),
        ("second", "90+4", "S02", 7620),
    ],
)
@pytest.mark.anyio
async def test_locate_returns_synthetic_resolved_first_second_and_added_time(
    test_app, period, minute, expected_segment, expected_second
):
    async with async_asgi_client(test_app) as client:
        response = await client.get(
            f"/api/v1/videos/{VIDEO_ID}/locate",
            params={"period": period, "minute": minute},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "resolved"
        assert body["result"]["video_second"] == pytest.approx(expected_second)
        assert body["result"]["event_video_second"] == pytest.approx(expected_second)
        assert body["result"]["event_clock_second_estimate"] == body["result"]["event_clock_second_start"] + 40
        assert body["result"]["lead_seconds"] == 0
        assert body["result"]["estimated"] is True
        assert body["result"]["method"] == "minute_tail"
        assert body["result"]["segment_id"] == expected_segment
        assert body["result"]["extrapolated_seconds"] <= 20


@pytest.mark.anyio
async def test_first_half_added_time_outside_synthetic_coverage_has_no_position(catalog, tmp_path):
    record = catalog.get("sample-limited-match")
    assert record is not None
    async with async_asgi_client(_app_for_record(record, tmp_path)) as client:
        response = await client.get(
            f"/api/v1/videos/{record.video_id}/locate",
            params={"period": "first", "minute": "45+2"},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "out_of_coverage"
    _assert_no_unique_position(body)


@pytest.mark.parametrize(
    ("period", "kind", "expected_second", "gap"),
    [("first", "match_start", 1000, 30), ("second", "period_start", 4700, 40)],
)
@pytest.mark.anyio
async def test_period_start_endpoint_allows_safe_boundary_estimates(test_app, period, kind, expected_second, gap):
    async with async_asgi_client(test_app) as client:
        response = await client.get(
            f"/api/v1/videos/{VIDEO_ID}/locate",
            params={"period": period, "event_kind": kind},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "resolved"
    assert body["result"]["video_second"] == pytest.approx(expected_second)
    assert body["result"]["extrapolated_seconds"] == gap
    assert body["result"]["method"] == "boundary_extrapolation"


@pytest.mark.anyio
async def test_period_start_with_insufficient_references_is_uncertain(catalog, tmp_path):
    def degrade(calibration):
        segment = next(item for item in calibration["segments"] if item["period"] == "first")
        segment["reference_count"] = 2
    async with async_asgi_client(_app_for_record(_record_with_calibration(catalog, degrade), tmp_path)) as client:
        response = await client.get(
            f"/api/v1/videos/{VIDEO_ID}/locate",
            params={"period": "first", "event_kind": "match_start"},
        )
    assert response.status_code == 200
    assert response.json()["status"] == "uncertain"
    _assert_no_unique_position(response.json())


@pytest.mark.anyio
async def test_ambiguous_candidates_do_not_expose_one_unique_position(catalog, tmp_path):
    def add_replay(calibration):
        replay = dict(calibration["segments"][1])
        replay["segment_id"] = "S02-replay-test"
        replay["offset_seconds"] += 25
        calibration["segments"].append(replay)

    async with async_asgi_client(_app_for_record(_record_with_calibration(catalog, add_replay), tmp_path)) as ambiguous_client:
        response = await ambiguous_client.get(
            f"/api/v1/videos/{VIDEO_ID}/locate",
            params={"period": "second", "minute": "58"},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ambiguous"
    assert len(body["candidates"]) == 2
    _assert_no_unique_position(body)


@pytest.mark.anyio
async def test_uncertain_period_does_not_expose_a_position(catalog, tmp_path):
    def remove_second_period(calibration):
        calibration["periods"] = [item for item in calibration["periods"] if item["period"] == "first"]
        calibration["segments"] = [item for item in calibration["segments"] if item["period"] == "first"]

    async with async_asgi_client(_app_for_record(_record_with_calibration(catalog, remove_second_period), tmp_path)) as uncertain_client:
        response = await uncertain_client.get(
            f"/api/v1/videos/{VIDEO_ID}/locate",
            params={"period": "second", "minute": "58"},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "uncertain"
    _assert_no_unique_position(body)


@pytest.mark.parametrize("video_id", ["sample-full-match", "sample-limited-match"])
@pytest.mark.anyio
async def test_out_of_coverage_does_not_expose_a_position(test_app, video_id):
    async with async_asgi_client(test_app) as client:
        response = await client.get(
            f"/api/v1/videos/{video_id}/locate",
            params={"period": "second", "minute": "96"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "out_of_coverage"
        _assert_no_unique_position(body)


@pytest.mark.anyio
async def test_unknown_video_is_404_for_video_endpoints(test_app):
    async with async_asgi_client(test_app) as client:
        assert (await client.get("/api/v1/videos/no-existe/synchronization")).status_code == 404
        assert (await client.get(
            "/api/v1/videos/no-existe/locate", params={"period": "second", "minute": "58"}
        )).status_code == 404


@pytest.mark.parametrize(
    "params",
    [
        {"period": "extra", "minute": "58"},
        {"period": "first", "minute": "58"},
        {"period": "second", "minute": "no-es-minuto"},
        {"period": "first", "minute": "45+0"},
        {"period": "second", "minute": "58", "lead_seconds": -1},
        {"minute": "58"},
    ],
)
@pytest.mark.anyio
async def test_invalid_locate_parameters_are_422(test_app, params):
    async with async_asgi_client(test_app) as client:
        response = await client.get(f"/api/v1/videos/{VIDEO_ID}/locate", params=params)
        assert response.status_code == 422
        assert "/home/" not in response.text


@pytest.mark.anyio
async def test_relative_data_directory_loads_from_environment(monkeypatch, tmp_path):
    monkeypatch.chdir(ROOT)
    monkeypatch.setenv("VIDEO_SERVICE_DATA_DIR", "./tests/fixtures/catalog")
    monkeypatch.setenv("VIDEO_SERVICE_MANIFEST", "videos.json")
    monkeypatch.setenv("VIDEO_SERVICE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("VIDEO_SERVICE_TEMP_DIR", str(tmp_path / "temporary"))
    async with async_asgi_client(create_app()) as configured_client:
        response = await configured_client.get("/api/v1/videos")
        assert response.status_code == 200
        assert len(response.json()["items"]) == 2


@pytest.mark.parametrize("failure", ["missing", "invalid_json", "inconsistent_status", "inconsistent_segment"])
def test_catalog_rejects_missing_invalid_or_inconsistent_calibrations(tmp_path, failure):
    data_dir = tmp_path / failure
    shutil.copytree(DATA, data_dir)
    calibration_path = data_dir / "calibrations/full-match.json"
    if failure == "missing":
        calibration_path.unlink()
    elif failure == "invalid_json":
        calibration_path.write_text("{not-json", encoding="utf-8")
    else:
        calibration = json.loads(calibration_path.read_text(encoding="utf-8"))
        if failure == "inconsistent_status":
            calibration["status"] = "failed"
        else:
            del calibration["segments"][0]["offset_seconds"]
        calibration_path.write_text(json.dumps(calibration), encoding="utf-8")
    with pytest.raises(CatalogError):
        VideoCatalog.load(data_dir)


@pytest.mark.anyio
async def test_http_reads_do_not_run_ocr_or_change_imported_data(test_app, monkeypatch):
    paths = [DATA / "videos.json", *sorted((DATA / "calibrations").glob("*.json"))]
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}

    def forbidden_subprocess(*args, **kwargs):
        raise AssertionError("Una petición HTTP no debe invocar procesos externos.")

    monkeypatch.setattr(subprocess, "run", forbidden_subprocess)
    async with async_asgi_client(test_app) as client:
        assert (await client.get("/health")).status_code == 200
        assert (await client.get("/api/v1/videos")).status_code == 200
        assert (await client.get(f"/api/v1/videos/{VIDEO_ID}/synchronization")).status_code == 200
        assert (await client.get(
            f"/api/v1/videos/{VIDEO_ID}/locate", params={"period": "second", "minute": "58"}
        )).status_code == 200

    after = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    assert after == before
    assert "video_sync.auto_calibrate" not in sys.modules
    assert "video_sync.media" not in sys.modules
    assert "video_sync.detector" not in sys.modules
