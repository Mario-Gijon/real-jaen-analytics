"""Persistent video-job and association state, isolated from sports-data SQLite."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
import sqlite3
from pathlib import Path
from typing import Any


ACTIVE_STATES = ("pending", "downloading", "calibrating", "saving")


class JobConflictError(ValueError):
    pass


class JobCapacityError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class VideoStateStore:
    def __init__(self, state_dir: Path):
        self.state_dir = Path(state_dir).expanduser().resolve()
        self.db_path = self.state_dir / "video-service.sqlite"
        self.calibrations_dir = self.state_dir / "calibrations"

    def initialize(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.calibrations_dir.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS video_job (
                    job_id TEXT PRIMARY KEY,
                    match_id TEXT NOT NULL,
                    youtube_video_id TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN
                      ('pending','downloading','calibrating','saving','completed','failed')),
                    message TEXT NOT NULL,
                    error_code TEXT,
                    sports_events_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    processed_at TEXT,
                    calibration_file TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_video_job_per_match
                  ON video_job(match_id) WHERE status IN ('pending','downloading','calibrating','saving');
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_video_job_per_youtube_id
                  ON video_job(youtube_video_id) WHERE status IN ('pending','downloading','calibrating','saving');
                CREATE TABLE IF NOT EXISTS video_association (
                    video_id TEXT PRIMARY KEY,
                    match_id TEXT NOT NULL,
                    youtube_video_id TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    calibration_file TEXT NOT NULL,
                    synchronization_status TEXT NOT NULL CHECK
                      (synchronization_status IN ('calibrated','uncertain','not_calibrated')),
                    processed_at TEXT NOT NULL,
                    duration_seconds REAL NOT NULL,
                    file_bytes INTEGER NOT NULL,
                    width INTEGER,
                    height INTEGER,
                    codec TEXT,
                    calibration_version INTEGER NOT NULL,
                    job_id TEXT NOT NULL REFERENCES video_job(job_id),
                    metadata_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS video_association_match ON video_association(match_id);
                CREATE TABLE IF NOT EXISTS match_video_override (
                    match_id TEXT PRIMARY KEY,
                    active_video_id TEXT REFERENCES video_association(video_id),
                    updated_at TEXT NOT NULL
                );
            """)
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(video_job)")}
            if "sports_events_json" not in columns:
                connection.execute("ALTER TABLE video_job ADD COLUMN sports_events_json TEXT")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def recover_interrupted_jobs(self) -> list[str]:
        now = utc_now()
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT job_id FROM video_job WHERE status IN ('pending','downloading','calibrating','saving')"
            ).fetchall()
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("""
                UPDATE video_job SET status='failed', error_code='service_restarted',
                    message='El servicio se reinició durante el trabajo. Puedes intentarlo de nuevo.', updated_at=?
                WHERE status IN ('pending','downloading','calibrating','saving')
            """, (now,))
            connection.commit()
        return [row["job_id"] for row in rows]

    def cleanup_calibration_temps(self) -> None:
        """Remove only abandoned atomic-write files inside the service-owned calibration directory."""
        root = self.calibrations_dir.resolve()
        if not root.is_dir():
            return
        for child in root.iterdir():
            if child.suffix == ".tmp" and not child.is_symlink() and child.is_file() and child.resolve().parent == root:
                child.unlink(missing_ok=True)

    def create_job(self, job_id: str, match_ref: str, youtube_video_id: str, source_url: str,
                   sports_events: dict[str, Any] | None, *, max_active_jobs: int = 2) -> None:
        now = utc_now()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                active_count = connection.execute(
                    "SELECT count(*) FROM video_job WHERE status IN ('pending','downloading','calibrating','saving')"
                ).fetchone()[0]
                if active_count >= max_active_jobs:
                    connection.rollback()
                    raise JobCapacityError("Ya hay demasiados vídeos en procesamiento. Espera a que termine uno.")
                connection.execute("""
                    INSERT INTO video_job(job_id,match_id,youtube_video_id,source_url,status,message,created_at,updated_at,
                                          sports_events_json)
                    VALUES(?,?,?,?,?,?,?,?,?)
                """, (job_id, match_ref, youtube_video_id, source_url, "pending", "Trabajo pendiente.", now, now,
                      json.dumps(sports_events, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
                      if sports_events is not None else None))
                connection.commit()
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                active = connection.execute("""
                    SELECT job_id, match_id FROM video_job
                    WHERE status IN ('pending','downloading','calibrating','saving')
                      AND (match_id=? OR youtube_video_id=?) LIMIT 1
                """, (match_ref, youtube_video_id)).fetchone()
                if active and active["match_id"] == match_ref:
                    raise JobConflictError("Ya hay un procesamiento activo para este partido.") from exc
                if active:
                    raise JobConflictError("Ese vídeo ya se está procesando para otro partido.") from exc
                raise JobConflictError("No se pudo iniciar el procesamiento duplicado.") from exc
            except BaseException:
                connection.rollback()
                raise

    def update_job(self, job_id: str, *, status: str, message: str,
                   error_code: str | None = None, calibration_file: str | None = None) -> None:
        with closing(self._connect()) as connection:
            connection.execute("""
                UPDATE video_job SET status=?, message=?, error_code=?,
                    calibration_file=COALESCE(?, calibration_file), updated_at=? WHERE job_id=?
            """, (status, message, error_code, calibration_file, utc_now(), job_id))

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT * FROM video_job WHERE job_id=?", (job_id,)).fetchone()
        return self._public_job(row) if row else None

    def current_job(self, match_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            row = connection.execute("""
                SELECT * FROM video_job WHERE match_id=? AND status IN
                    ('pending','downloading','calibrating','saving','failed')
                ORDER BY created_at DESC,rowid DESC LIMIT 1
            """, (match_id,)).fetchone()
        return self._public_job(row) if row else None

    def processing_input(self, job_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT match_id,source_url,sports_events_json FROM video_job WHERE job_id=?", (job_id,)
            ).fetchone()
        if row is None:
            return None
        try:
            events = json.loads(row["sports_events_json"]) if row["sports_events_json"] is not None else None
        except json.JSONDecodeError as exc:
            raise ValueError("El contrato de eventos persistido no es JSON válido.") from exc
        return {"match_ref": row["match_id"], "source_url": row["source_url"], "sports_events": events}

    @staticmethod
    def _public_job(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "job_id": row["job_id"],
            "match_ref": row["match_id"],
            "youtube_video_id": row["youtube_video_id"],
            "status": row["status"],
            "message": row["message"],
            "error_code": row["error_code"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def save_completed(self, *, job_id: str, match_id: str, video_id: str,
                       youtube_video_id: str, source_url: str, calibration: dict[str, Any],
                       synchronization_status: str, metadata: dict[str, Any], message: str) -> str:
        filename = f"calibrations/{video_id}.json"
        final_path = (self.state_dir / filename).resolve()
        if not final_path.is_relative_to(self.calibrations_dir.resolve()):
            raise ValueError("El destino de calibración no es seguro.")
        temp_path = final_path.with_suffix(f".{job_id}.tmp")
        final_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(calibration, indent=2, ensure_ascii=False, allow_nan=False).encode("utf-8") + b"\n"
        with temp_path.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            import os
            os.fsync(handle.fileno())
        temp_path.replace(final_path)
        processed_at = str(metadata["processed_at"])
        metadata_json = json.dumps(metadata, ensure_ascii=False, allow_nan=False, sort_keys=True)
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                job = connection.execute("SELECT status,match_id FROM video_job WHERE job_id=?", (job_id,)).fetchone()
                if job is None or job["match_id"] != match_id or job["status"] not in ACTIVE_STATES:
                    raise ValueError("El trabajo ya no está activo o no corresponde al partido.")
                override = connection.execute(
                    "SELECT active_video_id FROM match_video_override WHERE match_id=?", (match_id,)
                ).fetchone()
                if override is not None and override["active_video_id"] is not None:
                    raise ValueError("El partido ya tiene un vídeo activo.")
                connection.execute("""
                    INSERT INTO video_association(video_id,match_id,youtube_video_id,source_url,calibration_file,
                        synchronization_status,processed_at,duration_seconds,file_bytes,width,height,codec,
                        calibration_version,job_id,metadata_json)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (video_id, match_id, youtube_video_id, source_url, filename, synchronization_status,
                      processed_at, metadata["duration_seconds"], metadata["file_bytes"], metadata.get("width"),
                      metadata.get("height"), metadata.get("codec"), calibration["version"], job_id, metadata_json))
                connection.execute("""
                    INSERT INTO match_video_override(match_id,active_video_id,updated_at) VALUES(?,?,?)
                    ON CONFLICT(match_id) DO UPDATE SET active_video_id=excluded.active_video_id,
                        updated_at=excluded.updated_at
                """, (match_id, video_id, utc_now()))
                connection.execute("""
                    UPDATE video_job SET status='completed',message=?,error_code=NULL,processed_at=?,
                        calibration_file=?,updated_at=? WHERE job_id=?
                """, (message, processed_at, filename, processed_at, job_id))
                connection.commit()
        except BaseException:
            final_path.unlink(missing_ok=True)
            raise
        return filename

    def overrides(self) -> dict[str, str | None]:
        with closing(self._connect()) as connection:
            rows = connection.execute("SELECT match_id,active_video_id FROM match_video_override").fetchall()
        return {row["match_id"]: row["active_video_id"] for row in rows}

    def association(self, video_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT * FROM video_association WHERE video_id=?", (video_id,)).fetchone()
        return dict(row) if row else None

    def deactivate(self, *, match_id: str, expected_video_id: str, static_video_id: str | None) -> None:
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                active_job = connection.execute("""
                    SELECT 1 FROM video_job WHERE match_id=?
                      AND status IN ('pending','downloading','calibrating','saving') LIMIT 1
                """, (match_id,)).fetchone()
                if active_job:
                    raise ValueError("No se puede eliminar el vídeo mientras hay un procesamiento activo.")
                override = connection.execute(
                    "SELECT active_video_id FROM match_video_override WHERE match_id=?", (match_id,)
                ).fetchone()
                current_video_id = override["active_video_id"] if override else static_video_id
                if current_video_id is None:
                    raise LookupError("El partido no tiene una asociación de vídeo activa.")
                if current_video_id != expected_video_id:
                    raise ValueError("La asociación cambió. Actualiza la ficha antes de volver a intentarlo.")
                connection.execute("""
                    INSERT INTO match_video_override(match_id,active_video_id,updated_at) VALUES(?,NULL,?)
                    ON CONFLICT(match_id) DO UPDATE SET active_video_id=NULL,updated_at=excluded.updated_at
                """, (match_id, utc_now()))
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
