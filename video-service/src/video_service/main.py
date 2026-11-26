"""FastAPI entry point for saved calibrations and bounded video jobs."""
from __future__ import annotations

from contextlib import asynccontextmanager
import json
import logging
import os
from pathlib import Path
import re
from typing import Literal
import uuid

from fastapi import FastAPI, HTTPException, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, StrictStr
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool

from . import __version__
from .catalog import CatalogError, VideoCatalog, public_video
from .jobs import VideoJobManager
from .registry import PersistentVideoRegistry
from .postgresql import create_video_service_engine
from .postgresql_state import PostgreSQLVideoStateStore
from .state import JobCapacityError, JobConflictError, VideoStateStore
from .synchronization import DEFAULT_LEAD_SECONDS, locate_video, synchronization_summary
from .youtube import InvalidYouTubeUrl, youtube_video_id
from video_sync.sports_events import SportsEventsValidationError, validate_sports_events


SERVICE_ROOT = Path(__file__).resolve().parents[2]
MATCH_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
logger = logging.getLogger(__name__)


def configured_data_dir() -> Path:
    value = os.environ.get("VIDEO_SERVICE_DATA_DIR")
    return Path(value).expanduser().resolve() if value else SERVICE_ROOT / "data"


def configured_state_dir() -> Path:
    value = os.environ.get("VIDEO_SERVICE_STATE_DIR")
    return Path(value).expanduser().resolve() if value else SERVICE_ROOT / "storage" / "state"


def configured_temp_dir() -> Path:
    value = os.environ.get("VIDEO_SERVICE_TEMP_DIR")
    return Path(value).expanduser().resolve() if value else SERVICE_ROOT / "storage" / "tmp"


def configured_job_leases() -> tuple[int, int]:
    def positive_int(name: str, default: int) -> int:
        value = os.environ.get(name)
        try:
            result = int(value) if value is not None else default
        except ValueError:
            raise RuntimeError(f"{name} debe ser un número entero positivo.") from None
        if result <= 0:
            raise RuntimeError(f"{name} debe ser un número entero positivo.")
        return result

    lease = positive_int("VIDEO_SERVICE_JOB_LEASE_SECONDS", 60)
    heartbeat = positive_int("VIDEO_SERVICE_JOB_HEARTBEAT_SECONDS", 15)
    if heartbeat >= lease:
        raise RuntimeError("VIDEO_SERVICE_JOB_HEARTBEAT_SECONDS debe ser menor que el lease.")
    return lease, heartbeat


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
               downloader=None, calibrator=None, probe=None,
               storage: str | None = None) -> FastAPI:
    storage_mode = (storage or os.environ.get("VIDEO_SERVICE_STORAGE", "sqlite")).strip().lower()
    if storage_mode not in {"sqlite", "postgresql"}:
        raise RuntimeError("VIDEO_SERVICE_STORAGE debe ser sqlite o postgresql.")
    store = None
    service = None
    engine = None
    if storage_mode == "sqlite":
        if catalog is None:
            manifest_name = os.environ.get("VIDEO_SERVICE_MANIFEST", "videos.json")
            try:
                catalog = VideoCatalog.load(configured_data_dir(), manifest_name)
            except CatalogError as exc:
                raise RuntimeError("No se pudo inicializar el catálogo explícito de vídeos.") from exc
        store = VideoStateStore(state_dir or configured_state_dir())
        service = VideoJobManager(
            store, temp_dir or configured_temp_dir(), downloader=downloader, calibrator=calibrator, probe=probe,
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        current_store = store
        current_service = service
        current_engine = None
        if storage_mode == "sqlite":
            current_store.initialize()
            current_service.prepare()
            app.state.video_catalog = PersistentVideoRegistry(catalog, current_store)
        else:
            try:
                current_engine = create_video_service_engine("VIDEO_SERVICE_DATABASE_URL")
                lease_seconds, heartbeat_seconds = configured_job_leases()
                current_store = PostgreSQLVideoStateStore(
                    current_engine, owner_id=str(uuid.uuid4()), lease_seconds=lease_seconds,
                    heartbeat_seconds=heartbeat_seconds,
                )
                current_service = VideoJobManager(
                    current_store, temp_dir or configured_temp_dir(),
                    downloader=downloader, calibrator=calibrator, probe=probe,
                )
                current_store.initialize()
                current_service.prepare()
                app.state.video_catalog = current_store.reader
            except Exception as exc:
                logger.warning("Video PostgreSQL configuration unavailable error_type=%s", type(exc).__name__)
                if current_service is not None:
                    current_service.close()
                if current_engine is not None:
                    current_engine.dispose()
                current_store = None
                current_service = None
                current_engine = None
        app.state.video_state = current_store
        app.state.video_job_manager = current_service
        app.state.video_storage_mode = storage_mode
        try:
            yield
        finally:
            if current_service is not None:
                current_service.close()
            if current_store is not None and getattr(current_store, "is_postgresql", False):
                current_store.reader.close()
                current_store.close()
            if current_engine is not None:
                current_engine.dispose()

    app = FastAPI(title="Real Jaén Video Service", version=__version__, lifespan=lifespan)

    @app.exception_handler(SQLAlchemyError)
    async def postgres_unavailable(_, exc: SQLAlchemyError):
        logger.warning("Video PostgreSQL request failed error_type=%s", type(exc).__name__)
        return JSONResponse(status_code=503, content={
            "detail": "Video Service no puede acceder a su almacenamiento temporalmente. Inténtalo de nuevo."
        })

    def require_runtime() -> tuple[Any, Any]:
        runtime_store = getattr(app.state, "video_state", None)
        runtime_service = getattr(app.state, "video_job_manager", None)
        if runtime_store is None or runtime_service is None:
            raise HTTPException(status_code=503, detail="El almacenamiento de Video Service no está disponible.")
        return runtime_store, runtime_service

    @app.get("/health", tags=["health"])
    async def health() -> dict[str, str]:
        runtime_store, _ = require_runtime()
        if getattr(runtime_store, "is_postgresql", False):
            await run_in_threadpool(runtime_store.healthcheck)
        return {"status": "ok", "service": "real-jaen-video-service", "version": __version__}

    @app.get("/api/v1/videos", tags=["videos"])
    async def list_videos() -> dict:
        require_runtime()
        registry = app.state.video_catalog
        list_method = getattr(registry, "list_metadata", registry.list)
        records = await run_in_threadpool(list_method)
        return {"items": [public_video(record) for record in records]}

    @app.post("/api/v1/video-jobs", status_code=status.HTTP_202_ACCEPTED, tags=["video-jobs"])
    async def create_video_job(body: StartVideoJob) -> dict:
        _, runtime_service = require_runtime()
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
        if storage_mode == "sqlite":
            registry: PersistentVideoRegistry = app.state.video_catalog
            match_record = await run_in_threadpool(registry.for_match, match_ref)
            if match_record is not None:
                raise HTTPException(status_code=409, detail="El partido ya tiene un vídeo asociado. Elimínalo explícitamente antes de sustituirlo.")
            duplicate = await run_in_threadpool(registry.for_youtube_id, youtube_id)
            if duplicate is not None:
                raise HTTPException(status_code=409, detail="Ese vídeo ya está asociado a otro partido.")
        try:
            job = await run_in_threadpool(
                runtime_service.enqueue, match_ref, body.youtube_url.strip(), sports_events, max_active_jobs=2)
        except InvalidYouTubeUrl as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except JobCapacityError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        except JobConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return job

    @app.get("/api/v1/video-jobs/{job_id}", tags=["video-jobs"])
    async def get_video_job(job_id: str) -> dict:
        runtime_store, _ = require_runtime()
        try:
            normalized = str(uuid.UUID(job_id))
        except (ValueError, AttributeError) as exc:
            raise HTTPException(status_code=404, detail="Trabajo no encontrado.") from exc
        job = await run_in_threadpool(runtime_store.get_job, normalized)
        if job is None:
            raise HTTPException(status_code=404, detail="Trabajo no encontrado.")
        return job

    @app.get("/api/v1/matches/{match_ref}/video-job", tags=["video-jobs"])
    async def current_video_job(match_ref: str) -> dict | None:
        runtime_store, _ = require_runtime()
        checked_ref = match_ref_text(match_ref)
        return await run_in_threadpool(runtime_store.current_job, checked_ref)

    @app.delete("/api/v1/matches/{match_ref}/video", tags=["videos"])
    async def deactivate_match_video(match_ref: str) -> dict:
        runtime_store, _ = require_runtime()
        checked_ref = match_ref_text(match_ref)
        registry: PersistentVideoRegistry = app.state.video_catalog
        record = await run_in_threadpool(registry.for_match, checked_ref)
        if record is None:
            raise HTTPException(status_code=404, detail="El partido no tiene un vídeo activo.")
        overrides = await run_in_threadpool(runtime_store.overrides)
        static_video_id = None if checked_ref in overrides else record.video_id
        try:
            await run_in_threadpool(runtime_store.deactivate, match_id=checked_ref,
                                    expected_video_id=record.video_id, static_video_id=static_video_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"available": False, "match_ref": checked_ref}

    @app.get("/api/v1/videos/{video_id}/synchronization", tags=["videos"])
    async def get_synchronization(video_id: str) -> dict:
        require_runtime()
        record = await run_in_threadpool(app.state.video_catalog.get, video_id)
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
        require_runtime()
        record = await run_in_threadpool(app.state.video_catalog.get, video_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Vídeo no registrado.")
        try:
            return locate_video(record, period=period, minute=minute, lead_seconds=lead_seconds,
                                event_kind=event_kind, event_second=event_second)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return app


app = create_app()
