"""FastAPI entry point for saved calibrations and bounded video jobs."""
from __future__ import annotations

from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import re
from typing import Literal
import uuid

from fastapi import FastAPI, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, StrictStr

from . import __version__
from .catalog import CatalogError, VideoCatalog, public_video
from .jobs import VideoJobManager
from .registry import PersistentVideoRegistry
from .state import JobCapacityError, JobConflictError, VideoStateStore
from .synchronization import DEFAULT_LEAD_SECONDS, locate_video, synchronization_summary
from .youtube import InvalidYouTubeUrl, youtube_video_id
from video_sync.sports_events import SportsEventsValidationError, validate_sports_events


SERVICE_ROOT = Path(__file__).resolve().parents[2]
MATCH_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def configured_data_dir() -> Path:
    value = os.environ.get("VIDEO_SERVICE_DATA_DIR")
    return Path(value).expanduser().resolve() if value else SERVICE_ROOT / "data"


def configured_state_dir() -> Path:
    value = os.environ.get("VIDEO_SERVICE_STATE_DIR")
    return Path(value).expanduser().resolve() if value else SERVICE_ROOT / "storage" / "state"


def configured_temp_dir() -> Path:
    value = os.environ.get("VIDEO_SERVICE_TEMP_DIR")
    return Path(value).expanduser().resolve() if value else SERVICE_ROOT / "storage" / "tmp"


class StartVideoJob(BaseModel):
    model_config = ConfigDict(extra="forbid")
    match_ref: StrictStr
    youtube_url: StrictStr
    sports_events: dict | None = None


def match_ref_text(value: str) -> str:
    if not isinstance(value, str) or MATCH_REF_RE.fullmatch(value) is None:
        raise HTTPException(status_code=422, detail="La referencia de partido no es válida.")
    return value


def create_app(catalog: VideoCatalog | None = None, *, state_dir: Path | None = None,
               temp_dir: Path | None = None,
               downloader=None, calibrator=None, probe=None) -> FastAPI:
    if catalog is None:
        manifest_name = os.environ.get("VIDEO_SERVICE_MANIFEST", "videos.json")
        try:
            catalog = VideoCatalog.load(configured_data_dir(), manifest_name)
        except CatalogError as exc:
            raise RuntimeError("No se pudo inicializar el catálogo explícito de vídeos.") from exc

    store = VideoStateStore(state_dir or configured_state_dir())
    service = VideoJobManager(
        store,
        temp_dir or configured_temp_dir(),
        downloader=downloader,
        calibrator=calibrator,
        probe=probe,
    )
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store.initialize()
        service.prepare()
        app.state.video_state = store
        app.state.video_catalog = PersistentVideoRegistry(catalog, store)
        app.state.video_job_manager = service
        try:
            yield
        finally:
            service.close()

    app = FastAPI(title="Real Jaén Video Service", version=__version__, lifespan=lifespan)

    @app.get("/health", tags=["health"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "real-jaen-video-service", "version": __version__}

    @app.get("/api/v1/videos", tags=["videos"])
    async def list_videos() -> dict:
        return {"items": [public_video(record) for record in app.state.video_catalog.list()]}

    @app.post("/api/v1/video-jobs", status_code=status.HTTP_202_ACCEPTED, tags=["video-jobs"])
    async def create_video_job(body: StartVideoJob) -> dict:
        try:
            match_ref = match_ref_text(body.match_ref)
            youtube_id = youtube_video_id(body.youtube_url)
        except InvalidYouTubeUrl as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        try:
            sports_events = validate_sports_events(body.sports_events) if body.sports_events is not None else None
            if sports_events is not None:
                serialized_events = json.dumps(sports_events, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
                if len(serialized_events.encode("utf-8")) > 2 * 1024 * 1024:
                    raise SportsEventsValidationError("El documento de eventos supera el tamaño admitido.")
        except (SportsEventsValidationError, ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail="El documento de eventos deportivos no es válido.") from exc
        registry: PersistentVideoRegistry = app.state.video_catalog
        if registry.for_match(match_ref) is not None:
            raise HTTPException(status_code=409, detail="El partido ya tiene un vídeo asociado. Elimínalo explícitamente antes de sustituirlo.")
        duplicate = registry.for_youtube_id(youtube_id)
        if duplicate is not None:
            raise HTTPException(status_code=409, detail="Ese vídeo ya está asociado a otro partido.")
        try:
            job = service.enqueue(match_ref, body.youtube_url.strip(), sports_events, max_active_jobs=2)
        except InvalidYouTubeUrl as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except JobCapacityError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        except JobConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return job

    @app.get("/api/v1/video-jobs/{job_id}", tags=["video-jobs"])
    async def get_video_job(job_id: str) -> dict:
        try:
            normalized = str(uuid.UUID(job_id))
        except (ValueError, AttributeError) as exc:
            raise HTTPException(status_code=404, detail="Trabajo no encontrado.") from exc
        job = store.get_job(normalized)
        if job is None:
            raise HTTPException(status_code=404, detail="Trabajo no encontrado.")
        return job

    @app.get("/api/v1/matches/{match_ref}/video-job", tags=["video-jobs"])
    async def current_video_job(match_ref: str) -> dict | None:
        checked_ref = match_ref_text(match_ref)
        return store.current_job(checked_ref)

    @app.delete("/api/v1/matches/{match_ref}/video", tags=["videos"])
    async def deactivate_match_video(match_ref: str) -> dict:
        checked_ref = match_ref_text(match_ref)
        registry: PersistentVideoRegistry = app.state.video_catalog
        record = registry.for_match(checked_ref)
        if record is None:
            raise HTTPException(status_code=404, detail="El partido no tiene un vídeo activo.")
        overrides = store.overrides()
        static_video_id = None if checked_ref in overrides else record.video_id
        try:
            store.deactivate(match_id=checked_ref, expected_video_id=record.video_id,
                             static_video_id=static_video_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"available": False, "match_ref": checked_ref}

    @app.get("/api/v1/videos/{video_id}/synchronization", tags=["videos"])
    async def get_synchronization(video_id: str) -> dict:
        record = app.state.video_catalog.get(video_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Vídeo no registrado.")
        return synchronization_summary(record)

    @app.get("/api/v1/videos/{video_id}/locate", tags=["videos"])
    async def locate_event(
        video_id: str,
        period: Literal["first", "second"],
        minute: str | None = Query(default=None, min_length=1, max_length=8),
        lead_seconds: int = Query(DEFAULT_LEAD_SECONDS, ge=0, le=120),
        event_kind: Literal["other", "match_start", "period_start"] = "other",
        event_second: int | None = Query(default=None, ge=0, le=59),
    ) -> dict:
        record = app.state.video_catalog.get(video_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Vídeo no registrado.")
        try:
            return locate_video(record, period=period, minute=minute, lead_seconds=lead_seconds,
                                event_kind=event_kind, event_second=event_second)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return app


app = create_app()
