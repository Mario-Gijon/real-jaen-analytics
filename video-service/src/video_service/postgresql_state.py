"""PostgreSQL-backed Video Service job, association and calibration state."""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, IntegrityError

from .catalog import validate_calibration_document
from .postgresql import create_video_service_engine
from .postgresql_catalog import PostgreSQLVideoCatalogReader
from .state import JobCapacityError, JobConflictError


# A stable, Video Service-specific transaction lock. All operations that can
# reserve or publish an effective association acquire the same lock.
ADVISORY_LOCK_NAMESPACE = 1_380_465_334
ADVISORY_LOCK_KEY = 7


class JobOwnershipLost(RuntimeError):
    """The job lease or fencing token no longer belongs to this process."""


class CommitOutcomeUnknown(RuntimeError):
    """The database became unavailable before a commit outcome could be read."""


class PostgreSQLVideoStateStore:
    is_postgresql = True

    def __init__(self, engine: Engine | None = None, *, owner_id: str,
                 lease_seconds: int = 60, heartbeat_seconds: int = 15):
        if lease_seconds <= 0 or heartbeat_seconds <= 0 or heartbeat_seconds >= lease_seconds:
            raise ValueError("El lease y su heartbeat deben ser positivos y heartbeat menor que lease.")
        self.engine = engine or create_video_service_engine()
        self._owns_engine = engine is None
        self.owner_id = owner_id
        self.lease_seconds = lease_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.reader = PostgreSQLVideoCatalogReader(self.engine)

    def close(self) -> None:
        if self._owns_engine:
            self.engine.dispose()

    def initialize(self) -> None:
        """Verify the migrated shape without running or changing any migration."""
        with self.engine.connect() as connection:
            connection.execute(text("""
                SELECT created_order, heartbeat_at, fencing_token
                FROM video_service_job LIMIT 0
            """)).all()
            connection.execute(text("""
                SELECT youtube_video_id FROM video_service_catalog_video LIMIT 0
            """)).all()

    def healthcheck(self) -> None:
        with self.engine.connect() as connection:
            connection.execute(text("SELECT 1")).scalar_one()

    @staticmethod
    def _lock(connection) -> None:
        connection.execute(text("SELECT pg_advisory_xact_lock(:namespace, :key)"), {
            "namespace": ADVISORY_LOCK_NAMESPACE, "key": ADVISORY_LOCK_KEY,
        })

    @staticmethod
    def _effective_match(connection, match_ref: str) -> dict[str, Any] | None:
        override = connection.execute(text("""
            SELECT active_video_id FROM video_service_match_video_override WHERE match_id=:match_ref
        """), {"match_ref": match_ref}).mappings().first()
        if override is not None:
            video_id = override["active_video_id"]
            if video_id is None:
                return None
            row = connection.execute(text("""
                SELECT video_id, match_id AS match_ref, youtube_video_id
                FROM video_service_association WHERE video_id=:video_id
            """), {"video_id": video_id}).mappings().first()
            return dict(row) if row else None
        row = connection.execute(text("""
            SELECT video_id, match_ref, youtube_video_id
            FROM video_service_catalog_video WHERE match_ref=:match_ref
            ORDER BY video_id LIMIT 1
        """), {"match_ref": match_ref}).mappings().first()
        return dict(row) if row else None

    @staticmethod
    def _youtube_is_visible(connection, youtube_id: str) -> bool:
        static = connection.execute(text("""
            SELECT 1 FROM video_service_catalog_video AS s
            WHERE s.youtube_video_id=:youtube_id
              AND NOT EXISTS (
                  SELECT 1 FROM video_service_match_video_override AS o WHERE o.match_id=s.match_ref
              )
            LIMIT 1
        """), {"youtube_id": youtube_id}).first()
        if static is not None:
            return True
        runtime = connection.execute(text("""
            SELECT 1 FROM video_service_association AS a
            JOIN video_service_match_video_override AS o ON o.active_video_id=a.video_id
            WHERE a.youtube_video_id=:youtube_id LIMIT 1
        """), {"youtube_id": youtube_id}).first()
        return runtime is not None

    def _fail_expired(self, connection) -> list[str]:
        rows = connection.execute(text("""
            UPDATE video_service_job
            SET status='failed', error_code='service_restarted',
                message='El lease del trabajo expiró. Puedes volver a intentarlo.',
                updated_at=to_char(clock_timestamp() AT TIME ZONE 'UTC',
                                   'YYYY-MM-DD"T"HH24:MI:SS"+00:00"'),
                owner_id=NULL, lease_expires_at=NULL, heartbeat_at=NULL
            WHERE status IN ('pending','downloading','calibrating','saving')
              AND (lease_expires_at IS NULL OR lease_expires_at <= clock_timestamp())
            RETURNING job_id
        """)).all()
        return [row[0] for row in rows]

    def recover_interrupted_jobs(self) -> list[str]:
        with self.engine.begin() as connection:
            self._lock(connection)
            return self._fail_expired(connection)

    def renew_leases(self) -> list[str]:
        with self.engine.begin() as connection:
            self._lock(connection)
            rows = connection.execute(text("""
                UPDATE video_service_job
                SET heartbeat_at=clock_timestamp(),
                    lease_expires_at=clock_timestamp() + (:seconds * interval '1 second')
                WHERE owner_id=:owner_id
                  AND status IN ('pending','downloading','calibrating','saving')
                  AND lease_expires_at > clock_timestamp()
                RETURNING job_id
            """), {"owner_id": self.owner_id, "seconds": self.lease_seconds}).all()
        return [row[0] for row in rows]

    def is_owned(self, job_id: str, fencing_token: int | None) -> bool:
        if fencing_token is None:
            return False
        with self.engine.connect() as connection:
            return bool(connection.execute(text("""
                SELECT EXISTS(
                    SELECT 1 FROM video_service_job
                    WHERE job_id=:job_id AND owner_id=:owner_id AND fencing_token=:token
                      AND status IN ('pending','downloading','calibrating','saving')
                      AND lease_expires_at > clock_timestamp()
                )
            """), {"job_id": job_id, "owner_id": self.owner_id, "token": fencing_token}).scalar_one())

    def create_job(self, job_id: str, match_ref: str, youtube_video_id_value: str, source_url: str,
                   sports_events: dict[str, Any] | None, *, max_active_jobs: int = 2
                   ) -> tuple[dict[str, Any], int]:
        event_data = json.loads(json.dumps(sports_events, ensure_ascii=False, allow_nan=False)) \
            if sports_events is not None else None
        statement = text("""
            INSERT INTO video_service_job
                (job_id,match_id,youtube_video_id,source_url,status,message,sports_events_json,
                 created_at,updated_at,owner_id,lease_expires_at,heartbeat_at,fencing_token)
            VALUES (:job_id,:match_id,:youtube_id,:source_url,'pending','Trabajo pendiente.',:events,
                    to_char(clock_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS"+00:00"'),
                    to_char(clock_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS"+00:00"'),
                    :owner_id,clock_timestamp()+(:seconds * interval '1 second'),clock_timestamp(),
                    nextval('video_service_job_fencing_seq'))
            RETURNING job_id,match_id,youtube_video_id,status,message,error_code,created_at,updated_at,fencing_token
        """).bindparams(bindparam("events", type_=JSONB))
        try:
            with self.engine.begin() as connection:
                self._lock(connection)
                self._fail_expired(connection)
                active_count = connection.execute(text("""
                    SELECT count(*) FROM video_service_job
                    WHERE status IN ('pending','downloading','calibrating','saving')
                      AND lease_expires_at > clock_timestamp()
                """)).scalar_one()
                if active_count >= min(max_active_jobs, 2):
                    raise JobCapacityError("Ya hay demasiados vídeos en procesamiento. Espera a que termine uno.")
                if self._effective_match(connection, match_ref) is not None:
                    raise JobConflictError("El partido ya tiene un vídeo asociado. Elimínalo explícitamente antes de sustituirlo.")
                if self._youtube_is_visible(connection, youtube_video_id_value):
                    raise JobConflictError("Ese vídeo ya está asociado a otro partido.")
                row = connection.execute(statement, {
                    "job_id": job_id, "match_id": match_ref, "youtube_id": youtube_video_id_value,
                    "source_url": source_url, "events": event_data, "owner_id": self.owner_id,
                    "seconds": self.lease_seconds,
                }).mappings().one()
                token = row["fencing_token"]
                public_job = self.reader._public_job(row)
            return public_job, token
        except IntegrityError:
            raise JobConflictError("No se pudo iniciar el procesamiento duplicado.") from None

    def update_job(self, job_id: str, *, status: str, message: str,
                   error_code: str | None = None, calibration_file: str | None = None,
                   fencing_token: int | None = None) -> None:
        if fencing_token is None:
            raise JobOwnershipLost("No se dispone del fencing token del trabajo.")
        terminal = status in {"completed", "failed"}
        with self.engine.begin() as connection:
            row = connection.execute(text("""
                UPDATE video_service_job
                SET status=:status,message=:message,error_code=:error_code,
                    calibration_file=COALESCE(:calibration_file,calibration_file),
                    updated_at=to_char(clock_timestamp() AT TIME ZONE 'UTC',
                                      'YYYY-MM-DD"T"HH24:MI:SS"+00:00"'),
                    owner_id=CASE WHEN :terminal THEN NULL ELSE owner_id END,
                    lease_expires_at=CASE WHEN :terminal THEN NULL ELSE
                        clock_timestamp()+(:seconds * interval '1 second') END,
                    heartbeat_at=CASE WHEN :terminal THEN NULL ELSE clock_timestamp() END
                WHERE job_id=:job_id AND owner_id=:owner_id AND fencing_token=:token
                  AND status IN ('pending','downloading','calibrating','saving')
                  AND lease_expires_at > clock_timestamp()
                RETURNING job_id
            """), {
                "status": status, "message": message, "error_code": error_code,
                "calibration_file": calibration_file, "terminal": terminal,
                "seconds": self.lease_seconds, "job_id": job_id, "owner_id": self.owner_id,
                "token": fencing_token,
            }).first()
        if row is None:
            raise JobOwnershipLost("El lease del trabajo ya no pertenece a esta instalación.")

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        return self.reader.get_job(job_id)

    def current_job(self, match_id: str) -> dict[str, Any] | None:
        return self.reader.current_job(match_id)

    def processing_input(self, job_id: str) -> dict[str, Any] | None:
        return self.reader.processing_input(job_id)

    def association(self, video_id: str) -> dict[str, Any] | None:
        with self.engine.connect() as connection:
            row = connection.execute(text("""
                SELECT * FROM video_service_association WHERE video_id=:video_id
            """), {"video_id": video_id}).mappings().first()
        return dict(row) if row else None

    def overrides(self) -> dict[str, str | None]:
        with self.engine.connect() as connection:
            rows = connection.execute(text("""
                SELECT match_id,active_video_id FROM video_service_match_video_override
            """)).all()
        return {row[0]: row[1] for row in rows}

    def save_completed(self, *, job_id: str, match_id: str, video_id: str,
                       youtube_video_id: str, source_url: str, calibration: dict[str, Any],
                       synchronization_status: str, metadata: dict[str, Any], message: str,
                       fencing_token: int | None = None) -> str:
        if fencing_token is None:
            raise JobOwnershipLost("No se dispone del fencing token del trabajo.")
        validate_calibration_document(calibration)
        calibration_data = json.loads(json.dumps(calibration, ensure_ascii=False, allow_nan=False))
        metadata_data = json.loads(json.dumps(metadata, ensure_ascii=False, allow_nan=False))
        calibration_file = f"calibrations/{video_id}.json"
        calibration_id = f"runtime:{video_id}"
        processed_at = str(metadata["processed_at"])
        status = synchronization_status
        if status not in {"calibrated", "uncertain", "not_calibrated"}:
            raise ValueError("El estado de sincronización no está admitido.")
        try:
            with self.engine.begin() as connection:
                self._lock(connection)
                job = connection.execute(text("""
                    SELECT match_id FROM video_service_job
                    WHERE job_id=:job_id AND match_id=:match_id AND owner_id=:owner_id
                      AND fencing_token=:token AND status IN ('pending','downloading','calibrating','saving')
                      AND lease_expires_at > clock_timestamp()
                    FOR UPDATE
                """), {"job_id": job_id, "match_id": match_id, "owner_id": self.owner_id,
                      "token": fencing_token}).first()
                if job is None:
                    raise JobOwnershipLost("El lease del trabajo ya no pertenece a esta instalación.")
                if self._effective_match(connection, match_id) is not None:
                    raise JobConflictError("El partido ya tiene una asociación activa.")
                if self._youtube_is_visible(connection, youtube_video_id):
                    raise JobConflictError("El vídeo ya está asociado a otra referencia vigente.")
                connection.execute(text("""
                    INSERT INTO video_service_calibration(calibration_id,source_kind,source_name,document)
                    VALUES (:calibration_id,'runtime',:source_name,:document)
                """).bindparams(bindparam("document", type_=JSONB)), {
                    "calibration_id": calibration_id, "source_name": calibration_file,
                    "document": calibration_data,
                })
                connection.execute(text("""
                    INSERT INTO video_service_association
                        (video_id,match_id,youtube_video_id,source_url,calibration_file,calibration_id,
                         synchronization_status,processed_at,duration_seconds,file_bytes,width,height,codec,
                         calibration_version,job_id,metadata_json)
                    VALUES (:video_id,:match_id,:youtube_id,:source_url,:calibration_file,:calibration_id,
                            :sync_status,:processed_at,:duration,:file_bytes,:width,:height,:codec,
                            :calibration_version,:job_id,:metadata)
                """).bindparams(bindparam("metadata", type_=JSONB)), {
                    "video_id": video_id, "match_id": match_id, "youtube_id": youtube_video_id,
                    "source_url": source_url, "calibration_file": calibration_file,
                    "calibration_id": calibration_id, "sync_status": status,
                    "processed_at": processed_at, "duration": metadata_data["duration_seconds"],
                    "file_bytes": metadata_data["file_bytes"], "width": metadata_data.get("width"),
                    "height": metadata_data.get("height"), "codec": metadata_data.get("codec"),
                    "calibration_version": calibration_data["version"], "job_id": job_id,
                    "metadata": metadata_data,
                })
                connection.execute(text("""
                    INSERT INTO video_service_match_video_override(match_id,active_video_id,updated_at)
                    VALUES (:match_id,:video_id,to_char(clock_timestamp() AT TIME ZONE 'UTC',
                          'YYYY-MM-DD"T"HH24:MI:SS"+00:00"'))
                    ON CONFLICT(match_id) DO UPDATE SET active_video_id=excluded.active_video_id,
                        updated_at=excluded.updated_at
                """), {"match_id": match_id, "video_id": video_id})
                updated = connection.execute(text("""
                    UPDATE video_service_job SET status='completed',message=:message,error_code=NULL,
                        processed_at=:processed_at,calibration_file=:calibration_file,
                        updated_at=to_char(clock_timestamp() AT TIME ZONE 'UTC',
                                           'YYYY-MM-DD"T"HH24:MI:SS"+00:00"'),
                        owner_id=NULL,lease_expires_at=NULL,heartbeat_at=NULL
                    WHERE job_id=:job_id AND owner_id=:owner_id AND fencing_token=:token
                      AND status IN ('pending','downloading','calibrating','saving')
                      AND lease_expires_at > clock_timestamp()
                    RETURNING job_id
                """), {"message": message, "processed_at": processed_at,
                      "calibration_file": calibration_file, "job_id": job_id,
                      "owner_id": self.owner_id, "token": fencing_token}).first()
                if updated is None:
                    raise JobOwnershipLost("El lease del trabajo expiró antes de publicar.")
            return calibration_file
        except DBAPIError:
            try:
                with self.engine.connect() as connection:
                    durable = connection.execute(text("""
                        SELECT j.status,a.video_id FROM video_service_job AS j
                        LEFT JOIN video_service_association AS a ON a.job_id=j.job_id
                        WHERE j.job_id=:job_id
                    """), {"job_id": job_id}).first()
            except DBAPIError:
                raise CommitOutcomeUnknown("No se pudo determinar el resultado durable de la publicación.") from None
            if durable and durable[0] == "completed" and durable[1] == video_id:
                return calibration_file
            raise

    def deactivate(self, *, match_id: str, expected_video_id: str,
                   static_video_id: str | None = None) -> None:
        with self.engine.begin() as connection:
            self._lock(connection)
            self._fail_expired(connection)
            active = connection.execute(text("""
                SELECT 1 FROM video_service_job WHERE match_id=:match_id
                  AND status IN ('pending','downloading','calibrating','saving')
                  AND lease_expires_at > clock_timestamp() LIMIT 1
            """), {"match_id": match_id}).first()
            if active:
                raise ValueError("No se puede eliminar el vídeo mientras hay un procesamiento activo.")
            current = self._effective_match(connection, match_id)
            if current is None:
                raise LookupError("El partido no tiene una asociación de vídeo activa.")
            if current["video_id"] != expected_video_id:
                raise ValueError("La asociación cambió. Actualiza la ficha antes de volver a intentarlo.")
            connection.execute(text("""
                INSERT INTO video_service_match_video_override(match_id,active_video_id,updated_at)
                VALUES (:match_id,NULL,to_char(clock_timestamp() AT TIME ZONE 'UTC',
                      'YYYY-MM-DD"T"HH24:MI:SS"+00:00"'))
                ON CONFLICT(match_id) DO UPDATE SET active_video_id=NULL,updated_at=excluded.updated_at
            """), {"match_id": match_id})
