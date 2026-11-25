"""Read-only PostgreSQL catalogue and job reader for the Hito 07A transition."""
from __future__ import annotations

from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Engine
from sqlalchemy.dialects.postgresql import ARRAY, TEXT

from .catalog import CatalogError, VideoRecord, validate_calibration_document
from .postgresql import create_video_service_engine
from .youtube import InvalidYouTubeUrl, youtube_video_id


_STATIC = text("""
    SELECT video_id, display_name, match_ref, origin, source_url,
           calibration_file, calibration_id, synchronization_status
    FROM video_service_catalog_video
""")
_OVERRIDES = text("SELECT match_id, active_video_id FROM video_service_match_video_override")
_ASSOCIATION = text("""
    SELECT video_id, match_id, youtube_video_id, source_url, calibration_file,
           calibration_id, synchronization_status
    FROM video_service_association WHERE video_id = :video_id
""")
_CALIBRATIONS = text("""
    SELECT calibration_id, document
    FROM video_service_calibration WHERE calibration_id = ANY(:calibration_ids)
""").bindparams(bindparam("calibration_ids", type_=ARRAY(TEXT)))


class PostgreSQLVideoCatalogReader:
    """Expose the existing registry semantics without loading calibration JSON for metadata lookups."""

    def __init__(self, engine: Engine | None = None):
        self.engine = engine or create_video_service_engine()
        self._owns_engine = engine is None

    def close(self) -> None:
        if self._owns_engine:
            self.engine.dispose()

    @staticmethod
    def _runtime_row(row: Any) -> dict[str, Any]:
        return {
            "video_id": row["video_id"],
            "display_name": f"YouTube · {row['youtube_video_id']}",
            "match_ref": row["match_id"],
            "origin": "youtube",
            "source_url": row["source_url"],
            "calibration_file": row["calibration_file"],
            "calibration_id": row["calibration_id"],
            "synchronization_status": row["synchronization_status"],
        }

    def _record(self, row: Any) -> VideoRecord:
        document = None
        calibration_id = row["calibration_id"]
        if calibration_id:
            with self.engine.connect() as connection:
                document = connection.execute(
                    text("SELECT document FROM video_service_calibration WHERE calibration_id=:id"),
                    {"id": calibration_id},
                ).scalar_one_or_none()
            if document is None:
                raise CatalogError("La calibración registrada no tiene documento persistido.")
            document = validate_calibration_document(document)
        if row["synchronization_status"] == "calibrated" and (
            document is None or document.get("status") != "calibrated"
            or isinstance(document.get("version"), bool)
            or not isinstance(document.get("version"), int)
            or document["version"] < 1
        ):
            raise CatalogError("La calibración persistida no coincide con su estado registrado.")
        return VideoRecord(
            video_id=row["video_id"],
            display_name=row["display_name"],
            match_ref=row["match_ref"],
            origin=row["origin"],
            source_url=row["source_url"],
            calibration_file=row["calibration_file"],
            synchronization_status=row["synchronization_status"],
            calibration=document,
        )

    def _visible_metadata(self) -> list[dict[str, Any]]:
        """Fetch metadata in bounded queries; calibration documents are intentionally excluded."""
        with self.engine.connect() as connection:
            static_rows = [dict(row) for row in connection.execute(_STATIC).mappings()]
            overrides = {
                row["match_id"]: row["active_video_id"]
                for row in connection.execute(_OVERRIDES).mappings()
            }
            active_ids = list(dict.fromkeys(value for value in overrides.values() if value is not None))
            runtime_rows = {}
            if active_ids:
                result = connection.execute(text("""
                    SELECT video_id, match_id, youtube_video_id, source_url,
                           calibration_file, calibration_id, synchronization_status
                    FROM video_service_association WHERE video_id = ANY(:ids)
                """), {"ids": active_ids}).mappings()
                runtime_rows = {row["video_id"]: dict(row) for row in result}

        static_matches = {row["match_ref"] for row in static_rows}
        visible: list[dict[str, Any]] = []
        for row in static_rows:
            match_ref = row["match_ref"]
            if match_ref not in overrides:
                visible.append(row)
            elif overrides[match_ref] is not None:
                runtime = runtime_rows.get(overrides[match_ref])
                if runtime is None:
                    raise CatalogError("La asociación activa no tiene un registro persistente.")
                visible.append(self._runtime_row(runtime))
        for match_id, video_id in overrides.items():
            if match_id in static_matches or video_id is None:
                continue
            runtime = runtime_rows.get(video_id)
            if runtime is None:
                raise CatalogError("La asociación activa no tiene un registro persistente.")
            visible.append(self._runtime_row(runtime))
        return sorted(visible, key=lambda row: row["video_id"])

    def list(self) -> list[VideoRecord]:
        rows = self._visible_metadata()
        calibration_ids = list(dict.fromkeys(row["calibration_id"] for row in rows if row["calibration_id"]))
        docs: dict[str, Any] = {}
        if calibration_ids:
            with self.engine.connect() as connection:
                docs = {
                    row["calibration_id"]: row["document"]
                    for row in connection.execute(_CALIBRATIONS, {"calibration_ids": calibration_ids}).mappings()
                }
        records = []
        for row in rows:
            document = docs.get(row["calibration_id"]) if row["calibration_id"] else None
            if row["calibration_id"] and document is None:
                raise CatalogError("La calibración registrada no tiene documento persistido.")
            if document is not None:
                document = validate_calibration_document(document)
            if row["synchronization_status"] == "calibrated" and (
                document is None or document.get("status") != "calibrated"
                or isinstance(document.get("version"), bool)
                or not isinstance(document.get("version"), int)
                or document["version"] < 1
            ):
                raise CatalogError("La calibración persistida no coincide con su estado registrado.")
            records.append(VideoRecord(
                video_id=row["video_id"], display_name=row["display_name"], match_ref=row["match_ref"],
                origin=row["origin"], source_url=row["source_url"], calibration_file=row["calibration_file"],
                synchronization_status=row["synchronization_status"], calibration=document,
            ))
        return records

    def get(self, video_id: str) -> VideoRecord | None:
        with self.engine.connect() as connection:
            static = connection.execute(text("""
                SELECT video_id, display_name, match_ref, origin, source_url,
                       calibration_file, calibration_id, synchronization_status
                FROM video_service_catalog_video WHERE video_id=:video_id
            """), {"video_id": video_id}).mappings().first()
            if static is not None:
                override = connection.execute(text("""
                    SELECT active_video_id FROM video_service_match_video_override WHERE match_id=:match_id
                """), {"match_id": static["match_ref"]}).mappings().first()
                if override is None:
                    row = dict(static)
                elif override["active_video_id"] != video_id:
                    return None
                else:
                    runtime = connection.execute(_ASSOCIATION, {"video_id": video_id}).mappings().first()
                    if runtime is None:
                        raise CatalogError("La asociación activa no tiene un registro persistente.")
                    row = self._runtime_row(runtime)
            else:
                runtime = connection.execute(_ASSOCIATION, {"video_id": video_id}).mappings().first()
                if runtime is None:
                    return None
                override = connection.execute(text("""
                    SELECT active_video_id FROM video_service_match_video_override WHERE match_id=:match_id
                """), {"match_id": runtime["match_id"]}).mappings().first()
                if override is None or override["active_video_id"] != video_id:
                    return None
                row = self._runtime_row(runtime)
        return self._record(row)

    def for_match(self, match_ref: str) -> VideoRecord | None:
        with self.engine.connect() as connection:
            static_rows = list(connection.execute(text("""
                SELECT video_id, display_name, match_ref, origin, source_url,
                       calibration_file, calibration_id, synchronization_status
                FROM video_service_catalog_video WHERE match_ref=:match_ref ORDER BY video_id
            """), {"match_ref": match_ref}).mappings())
            override = connection.execute(text("""
                SELECT active_video_id FROM video_service_match_video_override WHERE match_id=:match_ref
            """), {"match_ref": match_ref}).mappings().first()
            if override is None:
                static_row = dict(static_rows[0]) if static_rows else None
            else:
                static_row = None
                active_id = override["active_video_id"]
                if active_id is None:
                    return None
                runtime = connection.execute(_ASSOCIATION, {"video_id": active_id}).mappings().first()
                if runtime is None:
                    raise CatalogError("La asociación activa no tiene un registro persistente.")
                row = self._runtime_row(runtime)
        if static_row is not None:
            return self._record(static_row)
        if override is None:
            return None
        return self._record(row)

    association_for_match = for_match

    def for_youtube_id(self, expected_youtube_id: str) -> VideoRecord | None:
        for row in self._visible_metadata():
            if row["origin"] != "youtube" or not row["source_url"]:
                continue
            try:
                if youtube_video_id(row["source_url"]) == expected_youtube_id:
                    return self._record(row)
            except InvalidYouTubeUrl:
                continue
        return None

    def calibration(self, video_id: str) -> dict[str, Any] | None:
        record = self.get(video_id)
        return record.calibration if record is not None else None

    @staticmethod
    def _public_job(row: Any) -> dict[str, Any]:
        return {
            "job_id": row["job_id"], "match_ref": row["match_id"],
            "youtube_video_id": row["youtube_video_id"], "status": row["status"],
            "message": row["message"], "error_code": row["error_code"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        }

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self.engine.connect() as connection:
            row = connection.execute(text("""
                SELECT job_id, match_id, youtube_video_id, status, message, error_code, created_at, updated_at
                FROM video_service_job WHERE job_id=:job_id
            """), {"job_id": job_id}).mappings().first()
        return self._public_job(row) if row else None

    def current_job(self, match_ref: str) -> dict[str, Any] | None:
        with self.engine.connect() as connection:
            row = connection.execute(text("""
                SELECT job_id, match_id, youtube_video_id, status, message, error_code, created_at, updated_at
                FROM video_service_job
                WHERE match_id=:match_id AND status IN ('pending','downloading','calibrating','saving','failed')
                ORDER BY created_at DESC, legacy_order DESC NULLS LAST, job_id DESC LIMIT 1
            """), {"match_id": match_ref}).mappings().first()
        return self._public_job(row) if row else None

    def processing_input(self, job_id: str) -> dict[str, Any] | None:
        with self.engine.connect() as connection:
            row = connection.execute(text("""
                SELECT match_id, source_url, sports_events_json
                FROM video_service_job WHERE job_id=:job_id
            """), {"job_id": job_id}).mappings().first()
        if row is None:
            return None
        return {"match_ref": row["match_id"], "source_url": row["source_url"],
                "sports_events": row["sports_events_json"]}
