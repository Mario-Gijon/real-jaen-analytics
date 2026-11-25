#!/usr/bin/env python3
"""Explicit, read-only SQLite/JSON import into an empty Video Service PostgreSQL database."""
from __future__ import annotations

import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from video_service.catalog import CatalogError, VideoCatalog, validate_calibration_document
from video_service.postgresql import create_video_service_engine


TABLES = (
    "video_service_job", "video_service_calibration", "video_service_catalog_video",
    "video_service_association", "video_service_match_video_override",
)
ACTIVE = {"pending", "downloading", "calibrating", "saving"}


class MigrationInputError(ValueError):
    pass


def _strict_json(value: str | None, label: str) -> Any:
    if value is None:
        return None
    def reject_constant(_: str):
        raise MigrationInputError(f"{label} contiene un valor JSON no estándar.")
    try:
        return json.loads(value, parse_constant=reject_constant)
    except MigrationInputError:
        raise
    except (json.JSONDecodeError, TypeError):
        raise MigrationInputError(f"{label} no contiene JSON válido.") from None


def _safe_source_file(root: Path, name: str, label: str) -> tuple[str, Path]:
    if not isinstance(name, str) or not name.strip():
        raise MigrationInputError(f"Falta la ruta de {label}.")
    relative = Path(name)
    if relative.is_absolute() or ".." in relative.parts:
        raise MigrationInputError(f"La ruta de {label} no es relativa y segura.")
    root = root.resolve(strict=True)
    candidate = root / relative
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise MigrationInputError(f"La ruta de {label} contiene un enlace simbólico.")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError:
        raise MigrationInputError(f"No existe el archivo de {label} referenciado.") from None
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise MigrationInputError(f"La ruta de {label} no apunta a un archivo regular seguro.")
    return relative.as_posix(), resolved


def _load_state(state_dir: Path) -> dict[str, Any]:
    root = state_dir.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise MigrationInputError("El directorio de estado no existe.")
    db_path = root / "video-service.sqlite"
    if db_path.is_symlink() or not db_path.is_file():
        raise MigrationInputError("No se encontró video-service.sqlite como archivo regular.")
    uri = db_path.as_uri() + "?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True, timeout=5)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise MigrationInputError("La comprobación de integridad de SQLite ha fallado.")
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise MigrationInputError("SQLite contiene referencias foráneas inválidas.")
            expected_tables = {"video_job", "video_association", "match_video_override"}
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if not expected_tables.issubset(tables):
                raise MigrationInputError("El SQLite de origen no contiene el esquema esperado de Video Service.")
            jobs = [dict(row) for row in connection.execute(
                "SELECT rowid AS legacy_order,* FROM video_job ORDER BY rowid")]
            associations = [dict(row) for row in connection.execute(
                "SELECT * FROM video_association ORDER BY video_id")]
            overrides = [dict(row) for row in connection.execute(
                "SELECT * FROM match_video_override ORDER BY match_id")]
    except MigrationInputError:
        raise
    except sqlite3.Error:
        raise MigrationInputError("No se pudo leer el SQLite de origen en modo de solo lectura.") from None

    if any(job["status"] in ACTIVE for job in jobs):
        raise MigrationInputError("Hay trabajos activos; finalízalos antes de migrar el estado.")
    allowed = ACTIVE | {"completed", "failed"}
    if any(job["status"] not in allowed for job in jobs):
        raise MigrationInputError("SQLite contiene un estado de trabajo no admitido.")
    for job in jobs:
        job["sports_events_json"] = _strict_json(job.get("sports_events_json"), "sports_events_json")
    for association in associations:
        if not isinstance(association.get("calibration_file"), str) or not association["calibration_file"].strip():
            raise MigrationInputError("Una asociación no incluye una ruta de calibración válida.")
        if association.get("synchronization_status") not in {"calibrated", "uncertain", "not_calibrated"}:
            raise MigrationInputError("Una asociación tiene un estado de sincronización no admitido.")
        association["metadata_json"] = _strict_json(association["metadata_json"], "metadata_json")
        if not isinstance(association["metadata_json"], dict):
            raise MigrationInputError("Los metadatos de una asociación no son un objeto JSON.")

    return {"root": root, "jobs": jobs, "associations": associations, "overrides": overrides}


def _collect_runtime_calibrations(state: dict[str, Any]) -> list[dict[str, Any]]:
    root = state["root"]
    names = {row.get("calibration_file") for row in state["associations"]}
    names.update(row.get("calibration_file") for row in state["jobs"] if row.get("calibration_file"))
    names.discard(None)
    calibration_dir = root / "calibrations"
    if calibration_dir.exists() or calibration_dir.is_symlink():
        if calibration_dir.is_symlink() or not calibration_dir.is_dir():
            raise MigrationInputError("El directorio de calibraciones persistentes no es seguro.")
        for current, directories, files in os.walk(calibration_dir, followlinks=False):
            current_path = Path(current)
            if any((current_path / name).is_symlink() for name in directories + files):
                raise MigrationInputError("El directorio de calibraciones contiene enlaces simbólicos.")
            for filename in files:
                if filename.lower().endswith(".json"):
                    names.add((current_path / filename).relative_to(root).as_posix())
    docs: dict[str, dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []
    for name in sorted(names):
        normalized, path = _safe_source_file(root, name, "calibración persistente")
        try:
            document = _strict_json(path.read_text(encoding="utf-8"), "La calibración persistente")
        except (OSError, UnicodeError):
            raise MigrationInputError("No se pudo leer una calibración persistente.") from None
        try:
            document = validate_calibration_document(document)
        except CatalogError as exc:
            raise MigrationInputError("Una calibración persistente no cumple la validación actual.") from None
        docs[normalized] = document
        rows.append({"calibration_id": f"runtime:{normalized}", "source_kind": "runtime",
                     "source_name": normalized, "document": document})
    for association in state["associations"]:
        name = association["calibration_file"]
        normalized = Path(name).as_posix()
        document = docs.get(normalized)
        if document is None:
            raise MigrationInputError("Una asociación no tiene un documento de calibración.")
        if association["synchronization_status"] == "calibrated" and (
            document.get("status") != "calibrated"
            or isinstance(document.get("version"), bool)
            or not isinstance(document.get("version"), int)
            or document["version"] < 1
        ):
            raise MigrationInputError("Una asociación calibrada no coincide con su documento.")
    return rows


def _load_static_catalog(catalog_dir: Path, manifest_name: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    try:
        catalog_root = catalog_dir.expanduser().resolve(strict=True)
        _, manifest_path = _safe_source_file(catalog_root, manifest_name, "manifiesto estático")
    except (OSError, MigrationInputError):
        raise MigrationInputError("No se encontró un manifiesto estático seguro.") from None
    try:
        raw_manifest = _strict_json(manifest_path.read_text(encoding="utf-8"), "El manifiesto estático")
    except (OSError, UnicodeError):
        raise MigrationInputError("No se pudo leer el manifiesto estático.") from None
    if isinstance(raw_manifest, dict) and isinstance(raw_manifest.get("videos"), list):
        for item in raw_manifest["videos"]:
            if isinstance(item, dict) and item.get("calibration_file") is not None:
                if not isinstance(item["calibration_file"], str):
                    raise MigrationInputError("El catálogo contiene una ruta de calibración no válida.")
                _safe_source_file(catalog_root, item["calibration_file"], "calibración del catálogo")
    try:
        catalog = VideoCatalog.load(catalog_root, manifest_name)
    except (CatalogError, OSError):
        raise MigrationInputError("No se pudo validar el catálogo estático y sus calibraciones.") from None
    catalog_dir = catalog_root
    records = []
    docs: dict[str, dict[str, Any]] = {}
    for record in catalog.list():
        calibration_id = None
        if record.calibration_file is not None:
            normalized, _ = _safe_source_file(catalog_dir, record.calibration_file, "calibración del catálogo")
            calibration_id = f"static:{normalized}"
            docs[normalized] = record.calibration
        records.append({
            "video_id": record.video_id, "display_name": record.display_name, "match_ref": record.match_ref,
            "origin": record.origin, "source_url": record.source_url,
            "calibration_file": record.calibration_file, "calibration_id": calibration_id,
            "synchronization_status": record.synchronization_status,
        })
    cal_rows = [{"calibration_id": f"static:{name}", "source_kind": "static",
                 "source_name": name, "document": doc} for name, doc in sorted(docs.items())]
    return records, cal_rows


def _json_equal(left: Any, right: Any) -> bool:
    return left == right


def migrate(engine: Engine, state_dir: Path, catalog_dir: Path,
            manifest_name: str = "videos.json") -> dict[str, int]:
    state = _load_state(state_dir)
    runtime_calibrations = _collect_runtime_calibrations(state)
    static_catalog, static_calibrations = _load_static_catalog(catalog_dir, manifest_name)
    calibrations = runtime_calibrations + static_calibrations
    associations = state["associations"]
    jobs = state["jobs"]
    overrides = state["overrides"]
    job_insert = text("""
        INSERT INTO video_service_job
            (job_id,match_id,youtube_video_id,source_url,status,message,error_code,sports_events_json,
             created_at,updated_at,processed_at,calibration_file,legacy_order)
        VALUES (:job_id,:match_id,:youtube_video_id,:source_url,:status,:message,:error_code,:sports_events_json,
                :created_at,:updated_at,:processed_at,:calibration_file,:legacy_order)
    """).bindparams(bindparam("sports_events_json", type_=JSONB))
    calibration_insert = text("""
        INSERT INTO video_service_calibration(calibration_id,source_kind,source_name,document)
        VALUES (:calibration_id,:source_kind,:source_name,:document)
    """).bindparams(bindparam("document", type_=JSONB))
    catalog_insert = text("""
        INSERT INTO video_service_catalog_video
            (video_id,display_name,match_ref,origin,source_url,calibration_file,calibration_id,synchronization_status)
        VALUES (:video_id,:display_name,:match_ref,:origin,:source_url,:calibration_file,:calibration_id,:synchronization_status)
    """)
    association_insert = text("""
        INSERT INTO video_service_association
            (video_id,match_id,youtube_video_id,source_url,calibration_file,calibration_id,
             synchronization_status,processed_at,duration_seconds,file_bytes,width,height,codec,
             calibration_version,job_id,metadata_json)
        VALUES (:video_id,:match_id,:youtube_video_id,:source_url,:calibration_file,:calibration_id,
                :synchronization_status,:processed_at,:duration_seconds,:file_bytes,:width,:height,:codec,
                :calibration_version,:job_id,:metadata_json)
    """).bindparams(bindparam("metadata_json", type_=JSONB))
    override_insert = text("""
        INSERT INTO video_service_match_video_override(match_id,active_video_id,updated_at)
        VALUES (:match_id,:active_video_id,:updated_at)
    """)

    # The context owns one transaction. Any validation, constraint or JSONB
    # error aborts the complete import without altering its destination.
    with engine.begin() as connection:
        counts = {table: connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
                  for table in TABLES}
        if any(counts.values()):
            raise MigrationInputError("El destino PostgreSQL no está vacío.")
        if calibrations:
            connection.execute(calibration_insert, calibrations)
        if static_catalog:
            connection.execute(catalog_insert, static_catalog)
        prepared_jobs = []
        for row in jobs:
            item = dict(row)
            item["sports_events_json"] = item.get("sports_events_json")
            prepared_jobs.append(item)
        if prepared_jobs:
            connection.execute(job_insert, prepared_jobs)
        prepared_associations = []
        for row in associations:
            item = dict(row)
            item["calibration_id"] = f"runtime:{Path(item['calibration_file']).as_posix()}"
            item["metadata_json"] = item["metadata_json"]
            prepared_associations.append(item)
        if prepared_associations:
            connection.execute(association_insert, prepared_associations)
        if overrides:
            connection.execute(override_insert, overrides)

        expected = {
            "video_service_job": len(jobs),
            "video_service_calibration": len(calibrations),
            "video_service_catalog_video": len(static_catalog),
            "video_service_association": len(associations),
            "video_service_match_video_override": len(overrides),
        }
        actual = {table: connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
                  for table in TABLES}
        if actual != expected:
            raise MigrationInputError("Los recuentos de la importación no coinciden.")
        imported_docs = connection.execute(text("""
            SELECT calibration_id, document FROM video_service_calibration
        """)).mappings().all()
        expected_docs = {row["calibration_id"]: row["document"] for row in calibrations}
        for row in imported_docs:
            if not _json_equal(expected_docs[row["calibration_id"]], row["document"]):
                raise MigrationInputError("Un documento de calibración cambió durante la importación.")
        imported_jobs = connection.execute(text("""
            SELECT job_id, sports_events_json FROM video_service_job
        """)).mappings().all()
        source_jobs = {row["job_id"]: row.get("sports_events_json") for row in jobs}
        if any(not _json_equal(source_jobs[row["job_id"]], row["sports_events_json"]) for row in imported_jobs):
            raise MigrationInputError("Un documento de eventos deportivos cambió durante la importación.")
        imported_metadata = connection.execute(text("""
            SELECT video_id, metadata_json FROM video_service_association
        """)).mappings().all()
        source_metadata = {row["video_id"]: row["metadata_json"] for row in associations}
        if any(not _json_equal(source_metadata[row["video_id"]], row["metadata_json"])
               for row in imported_metadata):
            raise MigrationInputError("Un documento de metadatos cambió durante la importación.")

    return {table: len(rows) for table, rows in {
        "jobs": jobs, "calibrations": calibrations, "catalog": static_catalog,
        "associations": associations, "overrides": overrides,
    }.items()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True, type=Path,
                        help="Directorio antiguo que contiene video-service.sqlite y calibrations/.")
    parser.add_argument("--catalog-dir", required=True, type=Path,
                        help="Directorio del manifiesto estático y sus calibraciones.")
    parser.add_argument("--manifest", default="videos.json", help="Manifiesto relativo al catálogo (por defecto videos.json).")
    args = parser.parse_args()
    engine = None
    try:
        engine = create_video_service_engine("VIDEO_SERVICE_MIGRATION_DATABASE_URL")
        result = migrate(engine, args.state_dir, args.catalog_dir, args.manifest)
        print(json.dumps({"status": "ok", "imported": result}, ensure_ascii=False, sort_keys=True))
        return 0
    except MigrationInputError as exc:
        print(f"Error de migración: {exc}", file=sys.stderr)
        return 2
    except Exception:
        print("Error de migración PostgreSQL; la transacción se revirtió y no se muestran detalles de conexión.",
              file=sys.stderr)
        return 2
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
