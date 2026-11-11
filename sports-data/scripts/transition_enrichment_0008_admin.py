#!/usr/bin/env python3
"""Safely adopt or restore an isolated 0007 -> 0008 enrichment transition.

This is an explicit offline maintenance tool. It never starts Acquisition or makes network requests. The install
services must be stopped by the operator; the cooperative locks and journal checks are additional safeguards, not a
substitute for that maintenance window.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import ExitStack, closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import sys
import uuid
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from persistencia import actualizacion, carga, sqlite, sincronizacion  # noqa: E402
from persistencia.bloqueo import bloquear  # noqa: E402

FORMAT = "real-jaen-enrichment-0008-transition-v1"
SOURCE_REVISION = "0007"
TARGET_REVISION = "0008"
VERSION = "rules-v1"
_SHA256 = set("0123456789abcdef")
_MIN_FREE = 256 * 1024 * 1024


class TransitionRejected(RuntimeError):
    """The install, candidate, snapshot or transition phase is not safe to change."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(char not in _SHA256 for char in value):
        raise TransitionRejected(f"{label} no es un SHA-256 canónico")
    return value


def _within(path: Path, root: Path) -> bool:
    try:
        Path(path).relative_to(Path(root))
        return True
    except ValueError:
        return False


def _private_dir(path: Path, label: str, *, create: bool = False) -> None:
    path = Path(path)
    if path.is_symlink():
        raise TransitionRejected(f"{label} no puede ser un enlace simbólico")
    if create and not path.exists():
        path.mkdir(mode=0o700)
    if not path.is_dir() or stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise TransitionRejected(f"{label} debe ser un directorio privado (sin permisos de grupo/otros)")


def _resolve_argument(path: str | Path, label: str) -> Path:
    expanded = Path(path).expanduser()
    if expanded.is_symlink():
        raise TransitionRejected(f"{label} no puede ser un enlace simbólico")
    return expanded.resolve()


def _fsync_dir(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_json_atomic(path: Path, value: Any) -> None:
    path = Path(path)
    temporary = path.with_name(f".{path.name}.tmp-{uuid.uuid4().hex}")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(_json(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
        _fsync_dir(path.parent)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _seal(value: dict[str, Any], key: str) -> dict[str, Any]:
    result = dict(value)
    result.pop(key, None)
    result[key] = _sha_bytes(_json(result))
    return result


def _verify_seal(value: dict[str, Any], key: str, label: str) -> None:
    signature = value.get(key)
    unsigned = dict(value)
    unsigned.pop(key, None)
    if signature != _sha_bytes(_json(unsigned)):
        raise TransitionRejected(f"Hash interno incorrecto en {label}")


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TransitionRejected(f"No se puede leer {label}") from error
    if not isinstance(value, dict):
        raise TransitionRejected(f"{label} debe ser un objeto JSON")
    return value


def _db_paths(base: Path, acquisition_journal: Path) -> dict[str, Path]:
    updates = Path(str(base) + ".actualizaciones")
    return {
        "sports": base,
        "coordinator": updates / "jobs.sqlite",
        "control": updates / "control" / "jobs.sqlite",
        "acquisition": acquisition_journal,
    }


def _locked(paths: list[Path]):
    """Take cooperative locks in a stable order; the base lock also excludes the normal coordinator owner."""
    stack = ExitStack()
    try:
        for path in sorted({Path(item).resolve() for item in paths}, key=str):
            stack.enter_context(bloquear(path))
    except BaseException:
        stack.close()
        raise
    return stack


def _history(rows: list[tuple[Any, ...]], state_column: int) -> dict[str, Any]:
    return {
        "rows": len(rows),
        "states": dict(sorted(Counter(row[state_column] for row in rows).items())),
        "rows_sha256": _sha_bytes(json.dumps(
            rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")),
    }


def _journal_state(base: Path, acquisition_journal: Path) -> dict[str, Any]:
    """Use the established journal validators, and extend them with the acquisition history contract."""
    from scripts import adopt_batches_fingerprint as prior

    try:
        journal, meta, coordinator_rows = prior._journal(base)
        control, control_rows = prior._control_journal(journal.parent)
    except prior.MaintenanceRejected as error:
        raise TransitionRejected(str(error)) from error
    if control is None:
        raise TransitionRejected("Falta el journal HTTP de control")
    histories = {
        "coordinator": _history(coordinator_rows, 1),
        "http_control": _history(control_rows, 2),
    }
    histories["coordinator"]["active_or_transitioning"] = []
    histories["http_control"]["active_or_transitioning"] = []
    acquisition_rows = _read_acquisition_rows(acquisition_journal)
    histories["acquisition"] = _history(acquisition_rows, 2)
    histories["acquisition"]["active"] = []
    return {
        "journal": journal,
        "control": control,
        "meta": meta,
        "coordinator_rows": coordinator_rows,
        "control_rows": control_rows,
        "histories": histories,
    }


def _read_acquisition_rows(path: Path) -> list[tuple[Any, ...]]:
    with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as connection:
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise TransitionRejected("Acquisition jobs.sqlite no supera integrity_check")
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise TransitionRejected("Acquisition jobs.sqlite contiene violaciones de claves foráneas")
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )}
        columns = tuple(row[1] for row in connection.execute("PRAGMA table_info(job)"))
        if tables != {"job"} or columns != ("id", "request", "state", "data"):
            raise TransitionRejected("Esquema desconocido en Acquisition jobs.sqlite")
        rows = connection.execute("SELECT id,request,state,data FROM job ORDER BY id").fetchall()
    for ident, _request, state, data_text in rows:
        try:
            data = json.loads(data_text)
        except (TypeError, json.JSONDecodeError) as error:
            raise TransitionRejected(f"Trabajo Acquisition ilegible: {ident}") from error
        if (state in {"queued", "running", "cancelling"} or state not in {
            "completed", "failed", "cancelled", "interrupted",
        } or not isinstance(data, dict) or data.get("id") != ident):
            raise TransitionRejected(f"Hay trabajo Acquisition activo o inconsistente: {ident}")
    return rows


def _sidecar_state(path: Path) -> dict[str, Any]:
    result = {}
    for suffix in sqlite.ACOMPANANTES:
        companion = Path(str(path) + suffix)
        if companion.is_symlink():
            raise TransitionRejected(f"Acompañante SQLite enlazado: {companion}")
        if companion.exists():
            if not companion.is_file():
                raise TransitionRejected(f"Acompañante SQLite no regular: {companion}")
            size = companion.stat().st_size
            digest = _sha_file(companion)
            if suffix == "-journal" and size:
                raise TransitionRejected(f"SQLite tiene un rollback journal no resuelto: {companion}")
            result[suffix] = {"size": size, "sha256": digest}
        else:
            result[suffix] = None
    return result


def _source_code_reference() -> dict[str, Any]:
    reference = carga.referencia_del_codigo()
    runtime = actualizacion.codigo()
    if reference.get("sha256_agregada") != runtime:
        raise TransitionRejected("La huella cargada por el coordinador no coincide con el código actual revisado")
    _canonical_sha(runtime, "Huella destino del coordinador")
    return reference


def _source_state(base: Path, scope: Path, acquisition_journal: Path, *,
                  allow_transition_mismatch: bool = False) -> dict[str, Any]:
    if not base.is_file() or base.is_symlink() or not scope.is_file() or scope.is_symlink():
        raise TransitionRejected("La SQLite y el archivo de ámbito deben existir como archivos regulares")
    if not acquisition_journal.is_file() or acquisition_journal.is_symlink():
        raise TransitionRejected("Falta acquisition-jobs.sqlite o no es un archivo regular")
    description = sqlite.comprobar_copia(base)
    if description["revision"] not in {SOURCE_REVISION, TARGET_REVISION}:
        raise TransitionRejected(f"La SQLite debe estar en Alembic {SOURCE_REVISION} o {TARGET_REVISION}")
    if description["revision"] == TARGET_REVISION:
        sports_check = sincronizacion.verificar(base)
        if not sports_check["ok"] or sports_check.get("cambios_en_seco"):
            raise TransitionRejected(f"La sincronización deportiva no verifica sin cambios: {sports_check}")
    journals = _journal_state(base, acquisition_journal)
    meta = journals["meta"]
    scope_sha = _sha_file(scope)
    source_code = _canonical_sha(meta.get("codigo"), "Huella confirmada de origen")
    _canonical_sha(meta.get("ambito"), "Huella confirmada del ámbito")
    try:
        generation = str(uuid.UUID(meta["generacion"]))
    except (KeyError, TypeError, ValueError) as error:
        raise TransitionRejected("UUID de generación confirmada no válido") from error
    if generation != meta["generacion"]:
        raise TransitionRejected("UUID de generación confirmada no canónico")
    if meta.get("ambito") != scope_sha:
        raise TransitionRejected("El ámbito SQLite no coincide con confirmado.detalle")
    if not allow_transition_mismatch and meta.get("descripcion") != description:
        raise TransitionRejected("La descripción semántica no coincide con confirmado.detalle")
    reference = _source_code_reference()
    coordinator_schema = _sqlite_schema(journals["journal"])
    if any(item[0] not in {"table", "index"} for item in coordinator_schema):
        raise TransitionRejected("El journal coordinador contiene triggers, vistas u objetos no admitidos")
    if not any(item[0] == "index" and item[1] == "unico_activo" for item in coordinator_schema):
        raise TransitionRejected("El journal coordinador carece del índice operativo unico_activo")
    return {
        "base": base,
        "source_base": base,
        "scope": scope,
        "acquisition_journal": acquisition_journal,
        "description": description,
        "description_sha256": description["huella"],
        "scope_sha256": scope_sha,
        "generation": generation,
        "source_code_sha256": source_code,
        "target_code_sha256": reference["sha256_agregada"],
        "source_meta": meta,
        "coordinator_rows": journals["coordinator_rows"],
        "control_rows": journals["control_rows"],
        "histories": journals["histories"],
        "coordinator_schema": coordinator_schema,
        "database_signature": _db_signature(base),
        "journal_signatures": {
            "coordinator": _db_signature(journals["journal"]),
            "http_control": _db_signature(journals["control"]),
            "acquisition": _db_signature(acquisition_journal),
        },
        "sidecars": {label: _sidecar_state(path) for label, path in _db_paths(base, acquisition_journal).items()},
    }


def _sqlite_schema(path: Path) -> list[tuple[Any, ...]]:
    with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as connection:
        return connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' "
            "ORDER BY type,name"
        ).fetchall()


def _db_signature(path: Path) -> str:
    from scripts import adopt_batches_fingerprint as prior
    return prior._db_signature(path)


def _safe_tree_inventory(root: Path, excluded: set[Path]) -> dict[str, dict[str, Any]]:
    root = Path(root).resolve()
    excluded = {Path(item).resolve() for item in excluded}
    result: dict[str, dict[str, Any]] = {}
    for current, dirs, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in dirs:
            path = current_path / name
            if path.is_symlink():
                raise TransitionRejected(f"No se permiten enlaces en los artefactos del estado: {path}")
        dirs[:] = [name for name in dirs if not (current_path / name).is_symlink()]
        for name in files:
            path = current_path / name
            resolved = path.resolve()
            if resolved in excluded or name.endswith(".lock"):
                continue
            if path.is_symlink() or not path.is_file():
                raise TransitionRejected(f"Artefacto no regular en el estado: {path}")
            relative = path.relative_to(root).as_posix()
            result[relative] = {"size": path.stat().st_size, "sha256": _sha_file(path)}
    return dict(sorted(result.items()))


def _copy_artifacts(root: Path, destination: Path, excluded: set[Path]) -> dict[str, dict[str, Any]]:
    root, destination = Path(root).resolve(), Path(destination)
    excluded = {Path(item).resolve() for item in excluded}
    inventory: dict[str, dict[str, Any]] = {}
    for current, dirs, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        relative_dir = current_path.relative_to(root)
        target_dir = destination / relative_dir
        target_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        for name in dirs:
            path = current_path / name
            if path.is_symlink():
                raise TransitionRejected(f"No se permiten enlaces en los artefactos: {path}")
        dirs[:] = [name for name in dirs if not (current_path / name).is_symlink()]
        for name in files:
            source = current_path / name
            if source.resolve() in excluded or name.endswith(".lock"):
                continue
            if source.is_symlink() or not source.is_file():
                raise TransitionRejected(f"Artefacto no regular en el estado: {source}")
            target = target_dir / name
            before = source.stat()
            digest = hashlib.sha256()
            size = 0
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with source.open("rb") as input_stream, os.fdopen(descriptor, "wb") as output_stream:
                    for block in iter(lambda: input_stream.read(1024 * 1024), b""):
                        output_stream.write(block)
                        digest.update(block)
                        size += len(block)
                    output_stream.flush()
                    os.fsync(output_stream.fileno())
            except BaseException:
                target.unlink(missing_ok=True)
                raise
            after = source.stat()
            if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
                raise TransitionRejected(f"El artefacto cambió mientras se copiaba: {source}")
            relative = source.relative_to(root).as_posix()
            inventory[relative] = {"size": size, "sha256": digest.hexdigest()}
    _fsync_dir(destination)
    return dict(sorted(inventory.items()))


def _copy_database(source: Path, destination: Path) -> dict[str, Any]:
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    sqlite.copia_consistente(source, destination)
    os.chmod(destination, 0o600)
    source_signature = _db_signature(source)
    copied_signature = _db_signature(destination)
    if copied_signature != source_signature:
        raise TransitionRejected(f"La copia SQLite coherente difiere del origen: {source}")
    return {"path": str(source.resolve()), "signature": source_signature,
            "file_sha256": _sha_file(destination), "description": sqlite.comprobar_copia(destination)}


def _source_rows_match_provenance(state: dict[str, Any], provenance: dict[str, Any]) -> None:
    histories = provenance.get("journal_histories")
    if not isinstance(histories, dict):
        raise TransitionRejected("La procedencia de la candidata no contiene historiales")
    for label in ("coordinator", "http_control", "acquisition"):
        actual = state["histories"][label]
        expected = histories.get(label)
        if not isinstance(expected, dict):
            raise TransitionRejected(f"La procedencia no contiene historial {label}")
        keys = ("rows", "states", "rows_sha256")
        if any(expected.get(key) != actual.get(key) for key in keys):
            raise TransitionRejected(f"El historial {label} no es el mismo que el estado de origen de la candidata")
        if label == "coordinator" and expected.get("active_or_transitioning") != []:
            raise TransitionRejected("La candidata se preparó desde un coordinador activo")
        if label == "http_control" and expected.get("active_or_transitioning") != []:
            raise TransitionRejected("La candidata se preparó desde un control HTTP activo")
        if label == "acquisition" and expected.get("active") != []:
            raise TransitionRejected("La candidata se preparó desde Acquisition activa")


def _validate_candidate(candidate_dir: Path, state: dict[str, Any], *, source_base: Path | None = None
                        ) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    from scripts import transition_enrichment_0008 as preparation

    candidate_dir = Path(candidate_dir).resolve()
    _private_dir(candidate_dir, "El directorio de la candidata")
    report_path = candidate_dir / preparation.REPORT_NAME
    candidate = candidate_dir / preparation.CANDIDATE_NAME
    if report_path.is_symlink() or candidate.is_symlink() or not report_path.is_file() or not candidate.is_file():
        raise TransitionRejected("La candidata debe contener candidate.sqlite y prepare-report.json regulares")
    if stat.S_IMODE(candidate.stat().st_mode) & 0o077 or stat.S_IMODE(report_path.stat().st_mode) & 0o077:
        raise TransitionRejected("La candidata y su informe deben tener permisos privados")
    report = _read_json(report_path, "prepare-report.json")
    if (report.get("status") != "candidate_not_installed" or report.get("candidate_installed") is not False
            or report.get("source_revision") != SOURCE_REVISION or report.get("target_revision") != TARGET_REVISION
            or report.get("enrichment_version") != VERSION):
        raise TransitionRejected("El informe no acredita una candidata 0007 -> 0008 rules-v1 no instalada")
    if Path(report.get("candidate_path", "")).resolve() != candidate.resolve():
        raise TransitionRejected("La ruta de candidate.sqlite no coincide con el informe")
    candidate_hash = _sha_file(candidate)
    if report.get("candidate_sqlite_sha256") != candidate_hash:
        raise TransitionRejected("El hash de candidate.sqlite no coincide con prepare-report.json")
    if any(Path(str(candidate) + suffix).exists() for suffix in sqlite.ACOMPANANTES):
        raise TransitionRejected("La candidata mantiene acompañantes SQLite pendientes")
    description = sqlite.comprobar_copia(candidate)
    if description != report.get("candidate_semantic_description") or description["revision"] != TARGET_REVISION:
        raise TransitionRejected("La descripción o revisión de la candidata no coincide con el informe")
    validation = report.get("validation")
    enrichment = report.get("enrichment")
    if not isinstance(validation, dict) or not all(validation.get(key) is True for key in (
        "existing_rows_and_values_preserved", "only_0008_schema_objects_added", "rules_v1_idempotent",
        "candidate_sidecars_absent",
    )):
        raise TransitionRejected("La candidata no acredita conservación del esquema e idempotencia")
    if (not isinstance(enrichment, dict) or enrichment.get("coverage_complete") is not True
            or enrichment.get("selected_annotations") != enrichment.get("selected_available_comments")
            or enrichment.get("idempotency_run", {}).get("inserted") != 0):
        raise TransitionRejected("La candidata no acredita cobertura completa e idempotencia")
    provenance = report.get("validated_source_provenance")
    if not isinstance(provenance, dict) or provenance.get("package_format") != "rj-analyst-coherent-state-backup-v1":
        raise TransitionRejected("La candidata no procede de un paquete coherente validado")
    _canonical_sha(provenance.get("current_destination_code_sha256"), "Huella destino de la candidata")
    _canonical_sha(provenance.get("confirmed_code_sha256"), "Huella origen de la candidata")
    _canonical_sha(provenance.get("confirmed_scope_sha256"), "Ámbito de la candidata")
    if provenance.get("confirmed_generation") != state["generation"]:
        raise TransitionRejected("La generación de la candidata no coincide con la generación instalada")
    if provenance.get("confirmed_scope_sha256") != state["scope_sha256"]:
        raise TransitionRejected("El ámbito de la candidata no coincide con el instalado")
    if provenance.get("confirmed_code_sha256") != state["source_code_sha256"]:
        raise TransitionRejected("La huella confirmada de la candidata no coincide con la instalada")
    if provenance.get("current_destination_code_sha256") != state["target_code_sha256"]:
        raise TransitionRejected("La huella destino de la candidata no coincide con el código revisado")
    if report.get("source_semantic_description") != state["description"]:
        raise TransitionRejected("La candidata no parte de la descripción semántica actualmente confirmada")
    if report.get("source_snapshot_semantic_sha256") != state["description_sha256"]:
        raise TransitionRejected("El hash semántico de origen de la candidata no coincide")
    source_base = Path(source_base or state.get("source_base") or state["base"])
    if report.get("preexisting_tables") != preparation._table_inventory(source_base):
        raise TransitionRejected("Las tablas deportivas originales no coinciden entre candidata y origen")
    if report.get("preexisting_tables_preserved") is not True:
        raise TransitionRejected("La candidata no acredita conservar las tablas de 0007")
    if report.get("schema_objects_added_by_0008") != preparation._verify_schema_transition(
        preparation._schema_objects(source_base), preparation._schema_objects(candidate),
    ):
        raise TransitionRejected("El esquema de candidata no contiene exclusivamente los objetos autorizados por 0008")
    if preparation._selected_capture_counts(candidate, VERSION) != {
        key: enrichment.get(key) for key in (
            "selected_spanish_captures", "selected_empty_captures", "selected_available_comments",
            "selected_annotations", "selected_participants",
        )
    }:
        raise TransitionRejected("Los recuentos reales de cobertura no coinciden con el informe de candidata")
    _source_rows_match_provenance(state, provenance)
    # Re-validate the original package read-only. This confirms its TAR, manifest, journals and code/scope provenance.
    package_dir = Path(provenance.get("package_directory", "")).resolve()
    validated_package = preparation.validate_coherent_backup_package(package_dir)
    package_provenance = {key: value for key, value in validated_package.items() if key != "status"}
    if validated_package.get("status") != "validated_coherent_package" or package_provenance != provenance:
        raise TransitionRejected("La procedencia del paquete original ya no coincide con el informe de candidata")
    return candidate, report, provenance


def _artifact_exclusions(base: Path, acquisition_journal: Path, stage_path: Path | None = None) -> set[Path]:
    result: set[Path] = set()
    for path in _db_paths(base, acquisition_journal).values():
        result.add(path.resolve())
        result.update(Path(str(path) + suffix).resolve() for suffix in sqlite.ACOMPANANTES)
    if stage_path is not None:
        result.add(Path(stage_path).resolve())
    return result


def _tree_bytes(root: Path, excluded: set[Path]) -> int:
    total = 0
    for current, dirs, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in dirs:
            if (current_path / name).is_symlink():
                raise TransitionRejected(f"No se permiten enlaces en la instalación: {current_path / name}")
        dirs[:] = [name for name in dirs if not (current_path / name).is_symlink()]
        for name in files:
            path = current_path / name
            if path.resolve() in excluded or name.endswith(".lock"):
                continue
            if path.is_symlink() or not path.is_file():
                raise TransitionRejected(f"Artefacto no regular en la instalación: {path}")
            total += path.stat().st_size
    return total


def _check_space(paths: list[Path], required: int) -> None:
    for path in paths:
        free = shutil.disk_usage(path).free
        if free < required:
            raise TransitionRejected(f"Espacio insuficiente en {path}: libres={free}, necesarios={required} bytes")


def _create_backup(transition_dir: Path, state: dict[str, Any]) -> dict[str, Any]:
    base = state["base"]
    acquisition = state["acquisition_journal"]
    db_paths = _db_paths(base, acquisition)
    backup_dir = transition_dir / "backup"
    backup_dir.mkdir(mode=0o700)
    payload = backup_dir / "payload"
    payload.mkdir(mode=0o700, parents=True)
    exclusions = _artifact_exclusions(base, acquisition)
    state_tree = payload / "state-artifacts"
    artifact_inventory = _copy_artifacts(base.parent, state_tree, exclusions)
    db_destinations = {
        "sports": payload / "databases" / "sports.sqlite",
        "coordinator": payload / "databases" / "coordinator.sqlite",
        "control": payload / "databases" / "control.sqlite",
        "acquisition": payload / "databases" / "acquisition.sqlite",
    }
    database_inventory = {label: _copy_database(db_paths[label], db_destinations[label]) for label in db_paths}
    sidecars_before = state["sidecars"]
    sidecars_after = {label: _sidecar_state(path) for label, path in db_paths.items()}
    if sidecars_before != sidecars_after:
        raise TransitionRejected("Cambió un acompañante SQLite durante el respaldo; no se declara coherente")
    current = _source_state(base, state["scope"], acquisition)
    for key in ("description", "scope_sha256", "generation", "source_code_sha256", "source_meta",
                "histories", "database_signature", "journal_signatures"):
        if current[key] != state[key]:
            raise TransitionRejected(f"El estado de origen cambió durante el respaldo: {key}")
    payload_hashes = {}
    for path in sorted(payload.rglob("*")):
        if path.is_symlink():
            raise TransitionRejected(f"No se permiten enlaces en el snapshot: {path}")
        if path.is_file():
            payload_hashes[path.relative_to(transition_dir / "backup").as_posix()] = {
                "size": path.stat().st_size, "sha256": _sha_file(path),
            }
    manifest = {
        "format": FORMAT,
        "manifest_version": 1,
        "created_at": _now(),
        "source": {
            "base": str(base), "scope": str(state["scope"]),
            "acquisition_journal": str(acquisition),
            "revision": SOURCE_REVISION,
            "description": state["description"],
            "description_sha256": state["description_sha256"],
            "generation": state["generation"],
            "confirmed_code_sha256": state["source_code_sha256"],
            "scope_sha256": state["scope_sha256"],
            "confirmed_meta": state["source_meta"],
            "database_signature": state["database_signature"],
            "journal_signatures": state["journal_signatures"],
            "journal_histories": state["histories"],
            "coordinator_schema": state["coordinator_schema"],
            "sidecars": sidecars_before,
        },
        "candidate": state["candidate"],
        "databases": database_inventory,
        "state_artifacts": artifact_inventory,
        "payload_files": payload_hashes,
        "payload_file_count": len(payload_hashes),
        "complete": True,
    }
    manifest = _seal(manifest, "manifest_sha256")
    _write_json_atomic(transition_dir / "backup" / "manifest.json", manifest)
    return manifest


def _load_backup(transition_dir: Path) -> dict[str, Any]:
    backup = Path(transition_dir) / "backup"
    _private_dir(backup, "El directorio del respaldo")
    payload = backup / "payload"
    _private_dir(payload, "El payload del respaldo")
    manifest_path = backup / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file() or stat.S_IMODE(manifest_path.stat().st_mode) & 0o077:
        raise TransitionRejected("El manifiesto del respaldo debe ser un archivo privado regular")
    manifest = _read_json(manifest_path, "manifiesto del respaldo")
    _verify_seal(manifest, "manifest_sha256", "manifiesto del respaldo")
    if (manifest.get("format") != FORMAT or manifest.get("manifest_version") != 1
            or manifest.get("complete") is not True):
        raise TransitionRejected("El snapshot no está completo o usa otro formato")
    expected = manifest.get("payload_files")
    if not isinstance(expected, dict):
        raise TransitionRejected("El manifiesto no contiene el inventario del snapshot")
    actual = {}
    allowed_lock_files = {
        f"payload/databases/{name}.sqlite.lock"
        for name in ("sports", "coordinator", "control", "acquisition")
    }
    for path in payload.rglob("*"):
        if path.is_symlink():
            raise TransitionRejected(f"Enlace inesperado en el snapshot: {path}")
        if path.is_file():
            # SQLite's cooperative lock helper leaves zero-byte .lock sentinels beside source databases. They are
            # runtime coordination files, not backup payload; validate and ignore them just like lock files in the
            # installation artifact inventory.
            if path.name.endswith(".lock"):
                relative = path.relative_to(backup).as_posix()
                if relative not in allowed_lock_files or path.stat().st_size != 0:
                    raise TransitionRejected(f"Archivo de bloqueo inesperado dentro del snapshot: {path}")
                continue
            if stat.S_IMODE(path.stat().st_mode) & 0o077:
                raise TransitionRejected(f"El archivo del snapshot no es privado: {path}")
            actual[path.relative_to(backup).as_posix()] = {"size": path.stat().st_size, "sha256": _sha_file(path)}
        elif path.is_dir() and stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise TransitionRejected(f"El directorio del snapshot no es privado: {path}")
        elif not path.is_dir():
            raise TransitionRejected(f"Entrada no regular dentro del snapshot: {path}")
    if actual != expected or len(actual) != manifest.get("payload_file_count"):
        raise TransitionRejected("El contenido del snapshot difiere de su inventario SHA-256")
    source = manifest.get("source")
    if not isinstance(source, dict) or source.get("revision") != SOURCE_REVISION:
        raise TransitionRejected("El manifiesto no contiene una fuente 0007 coherente")
    if (not isinstance(source.get("description"), dict)
            or source["description"].get("revision") != SOURCE_REVISION
            or source.get("description_sha256") != source["description"].get("huella")):
        raise TransitionRejected("La descripción semántica del snapshot no acredita 0007")
    for key in ("confirmed_code_sha256", "scope_sha256", "description_sha256"):
        _canonical_sha(source.get(key), f"{key} del snapshot")
    try:
        generation = str(uuid.UUID(source["generation"]))
    except (KeyError, TypeError, ValueError) as error:
        raise TransitionRejected("UUID de generación inválido en el snapshot") from error
    if generation != source["generation"]:
        raise TransitionRejected("UUID de generación no canónico en el snapshot")
    databases = manifest.get("databases")
    if not isinstance(databases, dict) or set(databases) != {"sports", "coordinator", "control", "acquisition"}:
        raise TransitionRejected("El manifiesto no cubre las cuatro SQLite necesarias")
    for label, item in databases.items():
        # Resolve the known mapping rather than trusting a path supplied by a manifest.
        relative = {
            "sports": "payload/databases/sports.sqlite",
            "coordinator": "payload/databases/coordinator.sqlite",
            "control": "payload/databases/control.sqlite",
            "acquisition": "payload/databases/acquisition.sqlite",
        }[label]
        path = backup / relative
        if not isinstance(item, dict):
            raise TransitionRejected(f"Entrada de base de datos inválida en el snapshot: {label}")
        if path.is_symlink() or not path.is_file() or stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise TransitionRejected(f"La copia SQLite de respaldo no es un archivo privado regular: {label}")
        if _db_signature(path) != item.get("signature"):
            raise TransitionRejected(f"La copia SQLite de respaldo no verifica: {label}")
        if any(Path(str(path) + suffix).exists() for suffix in sqlite.ACOMPANANTES):
            raise TransitionRejected(f"La SQLite del snapshot no es autónoma: {label}")
        if label == "sports" and sqlite.comprobar_copia(path) != source.get("description"):
            raise TransitionRejected("La copia deportiva del snapshot no coincide con la descripción de origen")
        signature_key = {"sports": None, "coordinator": "coordinator", "control": "http_control",
                         "acquisition": "acquisition"}[label]
        if signature_key is None:
            if item.get("signature") != source.get("database_signature"):
                raise TransitionRejected("La SQLite deportiva no coincide con su firma del estado confirmado")
        elif item.get("signature") != source.get("journal_signatures", {}).get(signature_key):
            raise TransitionRejected(f"El journal de respaldo no coincide con su firma confirmada: {label}")
    _verify_backup_journals(backup, manifest)
    return manifest


def _source_context_from_snapshot(base: Path, scope: Path, acquisition_journal: Path,
                                  transition_dir: Path, manifest: dict[str, Any], target_code: str) -> dict[str, Any]:
    source = manifest["source"]
    backup_base = Path(transition_dir) / "backup" / "payload" / "databases" / "sports.sqlite"
    description = source["description"]
    return {
        "base": Path(base), "source_base": backup_base, "scope": Path(scope),
        "acquisition_journal": Path(acquisition_journal), "description": description,
        "description_sha256": source["description_sha256"], "scope_sha256": source["scope_sha256"],
        "generation": source["generation"], "source_code_sha256": source["confirmed_code_sha256"],
        "target_code_sha256": target_code, "source_meta": source["confirmed_meta"],
        "histories": source["journal_histories"],
    }


def _assert_snapshot_matches_source(state: dict[str, Any], snapshot: dict[str, Any]) -> None:
    source = snapshot["source"]
    checks = {
        "revision": state["description"].get("revision") == SOURCE_REVISION,
        "description": state["description"] == source["description"],
        "description_sha256": state["description_sha256"] == source["description_sha256"],
        "generation": state["generation"] == source["generation"],
        "scope_sha256": state["scope_sha256"] == source["scope_sha256"],
        "confirmed_code_sha256": state["source_code_sha256"] == source["confirmed_code_sha256"],
        "confirmed_meta": state["source_meta"] == source["confirmed_meta"],
        "database_signature": state["database_signature"] == source["database_signature"],
        "journal_signatures": state["journal_signatures"] == source["journal_signatures"],
        "journal_histories": state["histories"] == source["journal_histories"],
        "sidecars": state["sidecars"] == source["sidecars"],
    }
    failed = [key for key, matches in checks.items() if not matches]
    if failed:
        raise TransitionRejected(f"El estado actual difiere del respaldo coherente: {', '.join(failed)}")


def _backup_journal_rows(path: Path, kind: str) -> tuple[list[tuple[Any, ...]], dict[str, Any] | None]:
    """Read a copied maintenance journal, verifying its known schema and inactive historical rows."""
    with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as connection:
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchall():
            raise TransitionRejected(f"SQLite de respaldo ilegible o con claves foráneas: {kind}")
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )}
        if kind == "coordinator":
            if tables != {"trabajo", "confirmado"}:
                raise TransitionRejected("Esquema de journal coordinador desconocido en el snapshot")
            if (tuple(row[1] for row in connection.execute("PRAGMA table_info(trabajo)"))
                    != ("id", "estado", "detalle")
                    or tuple(row[1] for row in connection.execute("PRAGMA table_info(confirmado)"))
                    != ("id", "detalle")):
                raise TransitionRejected("Columnas de journal coordinador desconocidas en el snapshot")
            rows = connection.execute("SELECT id,estado,detalle FROM trabajo ORDER BY id").fetchall()
            confirmed = connection.execute("SELECT id,detalle FROM confirmado ORDER BY id").fetchall()
            if len(confirmed) != 1 or confirmed[0][0] != 1:
                raise TransitionRejected("El snapshot no contiene una única confirmación del coordinador")
            try:
                meta = json.loads(confirmed[0][1])
            except (TypeError, json.JSONDecodeError) as error:
                raise TransitionRejected("confirmado.detalle del snapshot no es JSON válido") from error
            for ident, state, detail_text in rows:
                try:
                    detail = json.loads(detail_text)
                except (TypeError, json.JSONDecodeError) as error:
                    raise TransitionRejected(f"Trabajo coordinador ilegible en snapshot: {ident}") from error
                if (detail.get("id") != ident or detail.get("estado") != state
                        or state not in {"completed", "failed", "interrupted"}
                        or detail.get("fase") in {"publishing", "recovering"}):
                    raise TransitionRejected(f"El snapshot contiene trabajo activo o incoherente: {ident}")
            return rows, meta
        if kind == "control":
            if tables != {"job"} or tuple(row[1] for row in connection.execute(
                "PRAGMA table_info(job)"
            )) != ("id", "idem", "state", "data"):
                raise TransitionRejected("Esquema de journal HTTP desconocido en el snapshot")
            rows = connection.execute("SELECT id,idem,state,data FROM job ORDER BY id").fetchall()
            for ident, _idem, state, detail_text in rows:
                try:
                    detail = json.loads(detail_text)
                except (TypeError, json.JSONDecodeError) as error:
                    raise TransitionRejected(f"Trabajo HTTP ilegible en snapshot: {ident}") from error
                if (state in {"queued", "running"} or state not in {
                    "completed", "failed", "cancelled", "interrupted",
                } or detail.get("id") != ident or detail.get("status") != state):
                    raise TransitionRejected(f"El snapshot contiene trabajo HTTP activo o incoherente: {ident}")
            return rows, None
    raise TransitionRejected(f"Tipo de journal desconocido: {kind}")


def _verify_backup_journals(backup: Path, manifest: dict[str, Any]) -> None:
    source = manifest["source"]
    dbs = backup / "payload" / "databases"
    coordinator_path = dbs / "coordinator.sqlite"
    control_path = dbs / "control.sqlite"
    acquisition_path = dbs / "acquisition.sqlite"
    coordinator_rows, meta = _backup_journal_rows(coordinator_path, "coordinator")
    control_rows, _ = _backup_journal_rows(control_path, "control")
    acquisition_rows = _read_acquisition_rows(acquisition_path)
    if (meta != source.get("confirmed_meta") or meta.get("descripcion") != source.get("description")
            or meta.get("codigo") != source.get("confirmed_code_sha256")
            or meta.get("ambito") != source.get("scope_sha256")
            or meta.get("generacion") != source.get("generation")):
        raise TransitionRejected("La confirmación respaldada no coincide con la descripción deportiva")
    if _history(coordinator_rows, 1) | {"active_or_transitioning": []} != source["journal_histories"]["coordinator"]:
        raise TransitionRejected("El historial coordinador del snapshot no coincide con su manifiesto")
    if _history(control_rows, 2) | {"active_or_transitioning": []} != source["journal_histories"]["http_control"]:
        raise TransitionRejected("El historial HTTP del snapshot no coincide con su manifiesto")
    if _history(acquisition_rows, 2) | {"active": []} != source["journal_histories"]["acquisition"]:
        raise TransitionRejected("El historial Acquisition del snapshot no coincide con su manifiesto")
    if [list(row) for row in _sqlite_schema(coordinator_path)] != source.get("coordinator_schema"):
        raise TransitionRejected("El esquema del journal coordinador del snapshot cambió")


def _state_transition_path(transition_dir: Path) -> Path:
    return Path(transition_dir) / "transition.json"


def _record(transition_dir: Path, state: dict[str, Any], phase: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    updated = dict(state)
    history = list(updated.get("phases", []))
    history.append({"phase": phase, "at": _now()})
    updated["phase"] = phase
    updated["phases"] = history
    if extra:
        updated.update(extra)
    updated = _seal(updated, "transition_sha256")
    _write_json_atomic(_state_transition_path(transition_dir), updated)
    return updated


def _load_transition(transition_dir: Path, action: str) -> dict[str, Any]:
    _private_dir(transition_dir, "El directorio de transición")
    path = _state_transition_path(transition_dir)
    if path.is_symlink() or not path.is_file() or stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise TransitionRejected("transition.json debe ser un archivo privado regular")
    state = _read_json(path, "transition.json")
    _verify_seal(state, "transition_sha256", "transition.json")
    if state.get("format") != FORMAT or state.get("action") != action:
        raise TransitionRejected("El directorio pertenece a otra transición o acción")
    return state


def _record_transition(plan: dict[str, Any], manifest: dict[str, Any], candidate: Path, report: dict[str, Any],
                       provenance: dict[str, Any], transition_dir: Path) -> dict[str, Any]:
    initial = {
        "format": FORMAT, "action": "adopt", "phase": "preparing_backup", "phases": [],
        "base": str(plan["base"]), "scope": str(plan["scope"]),
        "acquisition_journal": str(plan["acquisition_journal"]),
        "candidate_path": str(candidate), "candidate_sha256": report["candidate_sqlite_sha256"],
        "candidate_report_sha256": _sha_file(Path(candidate).parent / "prepare-report.json"),
        "candidate_report": report,
        "candidate_provenance": provenance,
        "source_meta": plan["source_meta"], "source_description": plan["description"],
        "source_description_sha256": plan["description_sha256"],
        "source_generation": plan["generation"], "source_scope_sha256": plan["scope_sha256"],
        "source_code_sha256": plan["source_code_sha256"], "target_code_sha256": plan["target_code_sha256"],
        "target_description": report["candidate_semantic_description"],
        "target_description_sha256": report["candidate_semantic_description"]["huella"],
        "snapshot_manifest_sha256": manifest["manifest_sha256"],
        "snapshot_created_at": manifest["created_at"],
        "stage_path": str(plan["base"].with_name(f".{plan['base'].name}.enrichment-0008-{uuid.uuid4().hex}.stage")),
    }
    return _record(transition_dir, initial, "backup_complete")


def _same_history(actual: dict[str, Any], expected: dict[str, Any]) -> bool:
    return all(actual.get(key) == expected.get(key) for key in ("rows", "states", "rows_sha256"))


def _assert_unchanged_histories(live: dict[str, Any], manifest: dict[str, Any]) -> None:
    expected = manifest["source"]["journal_histories"]
    for name in ("coordinator", "http_control", "acquisition"):
        if not _same_history(live["histories"][name], expected[name]):
            raise TransitionRejected(f"El historial {name} cambió; no se reanuda ni se revierte automáticamente")
    signatures = manifest["source"]["journal_signatures"]
    for name in ("http_control", "acquisition"):
        if live["journal_signatures"][name] != signatures[name]:
            raise TransitionRejected(f"El journal {name} cambió fuera del historial esperado")
    expected_schema = [list(row) for row in manifest["source"]["coordinator_schema"]]
    if [list(row) for row in live["coordinator_schema"]] != expected_schema:
        raise TransitionRejected("El esquema del journal coordinador cambió")


def _assert_source_artifacts(base: Path, acquisition: Path, manifest: dict[str, Any], *, allow_copias: bool,
                             stage_path: Path | None = None) -> None:
    excluded = _artifact_exclusions(base, acquisition, stage_path)
    current = _safe_tree_inventory(base.parent, excluded)
    expected = manifest["state_artifacts"]
    if allow_copias:
        for name, info in expected.items():
            if current.get(name) != info:
                raise TransitionRejected(f"Un artefacto/evidencia original cambió o falta: {name}")
        unexpected = set(current) - set(expected)
        if any(not (name == "copias" or name.startswith("copias/")) for name in unexpected):
            raise TransitionRejected(f"Aparecieron artefactos no explicados desde la transición: {sorted(unexpected)[:5]}")
    elif current != expected:
        raise TransitionRejected("El inventario de evidencias no coincide con el respaldo")


def _expected_meta(state: dict[str, Any], target: bool) -> dict[str, Any]:
    meta = dict(state["source_meta"])
    if target:
        meta["codigo"] = state["target_code_sha256"]
        meta["descripcion"] = state["target_description"]
    return meta


def _classify_live(state: dict[str, Any], manifest: dict[str, Any]) -> tuple[str, bool]:
    base = Path(state["base"])
    acquisition = Path(state["acquisition_journal"])
    live = _source_state(base, Path(state["scope"]), acquisition, allow_transition_mismatch=True)
    _assert_unchanged_histories(live, manifest)
    if live["scope_sha256"] != state["source_scope_sha256"] or live["generation"] != state["source_generation"]:
        raise TransitionRejected("Cambió el ámbito o la generación confirmada desde el respaldo")
    target_desc = state["target_description"]
    if live["description"] == state["source_description"]:
        base_state = "source"
    elif live["description"] == target_desc:
        base_state = "target"
    else:
        raise TransitionRejected("La SQLite no coincide ni con el origen ni con la candidata confirmada")
    source_meta = _expected_meta(state, False)
    target_meta = _expected_meta(state, True)
    if live["source_meta"] == source_meta:
        metadata_target = False
    elif live["source_meta"] == target_meta:
        metadata_target = True
    else:
        raise TransitionRejected("confirmado.detalle contiene cambios ajenos a codigo/descripcion")
    if base_state == "source" and metadata_target:
        raise TransitionRejected("Estado imposible: journal destino con SQLite de origen")
    return base_state, metadata_target


def _update_confirmation(journal: Path, state: dict[str, Any]) -> None:
    expected = _expected_meta(state, False)
    target = _expected_meta(state, True)
    with closing(sqlite3.connect(journal, timeout=10)) as connection:
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("BEGIN IMMEDIATE")
        rows = connection.execute("SELECT id,detalle FROM confirmado ORDER BY id").fetchall()
        if len(rows) != 1 or rows[0][0] != 1:
            raise TransitionRejected("La confirmación cambió antes del commit administrativo")
        try:
            current = json.loads(rows[0][1])
        except (TypeError, json.JSONDecodeError) as error:
            raise TransitionRejected("confirmado.detalle dejó de ser JSON válido") from error
        if current == target:
            connection.rollback()
            return
        if current != expected:
            raise TransitionRejected("La confirmación cambió antes del commit administrativo")
        connection.execute("UPDATE confirmado SET detalle=? WHERE id=1", (
            json.dumps(target, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        ))
        connection.commit()


def _prepare_base_stage(candidate: Path, stage: Path, expected_description: dict[str, Any]) -> None:
    if stage.exists() or stage.is_symlink():
        if stage.is_file() and sqlite.comprobar_copia(stage) == expected_description:
            return
        if stage.is_file():
            stage.unlink()
        else:
            raise TransitionRejected("La ruta de staging de la candidata está ocupada por un objeto no regular")
    sqlite.copia_consistente(candidate, stage)
    os.chmod(stage, 0o600)
    if sqlite.comprobar_copia(stage, expected_description) != expected_description:
        raise TransitionRejected("La candidata preparada para sustitución difiere del informe")
    with stage.open("rb") as stream:
        os.fsync(stream.fileno())
    _fsync_dir(stage.parent)


def _coordinator_open(base: Path, scope: Path, expected_generation: str) -> None:
    with actualizacion.Coordinador(base, scope) as coordinator:
        generation = coordinator.capturar().id
    if generation != expected_generation:
        raise TransitionRejected("El coordinador abrió una generación distinta a la preservada")


def _write_receipt(path: Path, receipt: dict[str, Any]) -> dict[str, Any]:
    sealed = _seal(receipt, "receipt_sha256")
    _write_json_atomic(path, sealed)
    return sealed


def adopt(base: str | Path, scope: str | Path, acquisition_journal: str | Path,
          candidate_dir: str | Path, transition_dir: str | Path, confirmation: str,
          *, _checkpoint=lambda _phase: None) -> dict[str, Any]:
    base = _resolve_argument(base, "La SQLite de origen")
    scope = _resolve_argument(scope, "El ámbito")
    acquisition_journal = _resolve_argument(acquisition_journal, "El journal de Acquisition")
    candidate_dir = _resolve_argument(candidate_dir, "El directorio de candidata")
    transition_dir = _resolve_argument(transition_dir, "El directorio de transición")
    reference = _source_code_reference()
    if not base.is_file() or not scope.is_file() or not acquisition_journal.is_file():
        raise TransitionRejected("Faltan SQLite, ámbito o journal de Acquisition")
    if _within(transition_dir, base.parent) or _within(base.parent, transition_dir):
        raise TransitionRejected("La transición debe quedar fuera del árbol de instalación")
    if _within(candidate_dir, base.parent) or _within(base.parent, candidate_dir):
        raise TransitionRejected("La candidata debe quedar fuera del árbol de instalación")
    if _within(transition_dir, candidate_dir) or _within(candidate_dir, transition_dir):
        raise TransitionRejected("Candidata y transición deben residir en árboles distintos")
    parent = transition_dir.parent
    _private_dir(parent, "El directorio padre de transición")

    with _locked([base, Path(str(base) + ".actualizaciones") / "jobs.sqlite",
                  Path(str(base) + ".actualizaciones") / "control" / "jobs.sqlite", acquisition_journal]):
        transition_exists = transition_dir.exists()
        transition_state: dict[str, Any] | None = None
        if transition_exists:
            _private_dir(transition_dir, "El directorio de transición")
            transition_path = _state_transition_path(transition_dir)
            if transition_path.exists():
                transition_state = _load_transition(transition_dir, "adopt")
                snapshot = _load_backup(transition_dir)
                if transition_state.get("snapshot_manifest_sha256") != snapshot["manifest_sha256"]:
                    raise TransitionRejected("transition.json y manifiesto de respaldo no coinciden")
                source = _source_context_from_snapshot(base, scope, acquisition_journal, transition_dir,
                                                       snapshot, reference["sha256_agregada"])
            elif (transition_dir / "backup" / "manifest.json").is_file():
                # A crash may occur after the complete backup manifest is durable but before transition.json.
                snapshot = _load_backup(transition_dir)
                source = _source_state(base, scope, acquisition_journal)
                _assert_snapshot_matches_source(source, snapshot)
                _assert_source_artifacts(base, acquisition_journal, snapshot, allow_copias=False)
                source["target_code_sha256"] = reference["sha256_agregada"]
            else:
                raise TransitionRejected(
                    "El directorio contiene un respaldo parcial sin manifiesto durable; consérvelo y use otra ruta nueva"
                )
        else:
            source = _source_state(base, scope, acquisition_journal)
            if source["description"].get("revision") != SOURCE_REVISION:
                raise TransitionRejected(f"Una adopción nueva exige origen Alembic {SOURCE_REVISION}")
            source["target_code_sha256"] = reference["sha256_agregada"]

        if source["target_code_sha256"] != reference["sha256_agregada"]:
            raise TransitionRejected("El destino cambió durante el preflight")
        candidate, report, provenance = _validate_candidate(
            candidate_dir, source, source_base=source.get("source_base", source["base"]),
        )
        candidate_report_hash = _sha_file(candidate_dir / "prepare-report.json")
        confirmation_expected = (
            f"adopt enrichment 0007->0008 {source['generation']} {source['target_code_sha256']} services-stopped"
        )
        if confirmation != confirmation_expected:
            raise TransitionRejected(f"Confirmación explícita incorrecta; se exige exactamente: {confirmation_expected}")

        if transition_state is not None:
            for key, value in (("base", str(base)), ("scope", str(scope)),
                               ("acquisition_journal", str(acquisition_journal)),
                               ("candidate_path", str(candidate)), ("candidate_sha256", report["candidate_sqlite_sha256"]),
                               ("candidate_report_sha256", candidate_report_hash),
                               ("target_code_sha256", source["target_code_sha256"])):
                if transition_state.get(key) != value:
                    raise TransitionRejected(f"La transición existente difiere en {key}")
            _assert_source_artifacts(base, acquisition_journal, snapshot, allow_copias=True,
                                     stage_path=Path(transition_state["stage_path"]))
        else:
            if _within(transition_dir, candidate_dir) or _within(candidate_dir, transition_dir):
                raise TransitionRejected("La salida y la candidata se solapan")
            if not transition_exists:
                required = _tree_bytes(base.parent, _artifact_exclusions(base, acquisition_journal))
                required += sum(path.stat().st_size for path in _db_paths(base, acquisition_journal).values()) * 3
                required += candidate.stat().st_size * 2 + _MIN_FREE
                _check_space([transition_dir.parent, base.parent], required)
                _private_dir(transition_dir, "El directorio de transición", create=True)
                try:
                    _checkpoint("before_backup")
                except BaseException:
                    # The checkpoint is before writing anything; leave no empty directory that would be
                    # indistinguishable from an interrupted partial snapshot on the next invocation.
                    if not any(transition_dir.iterdir()):
                        transition_dir.rmdir()
                        _fsync_dir(transition_dir.parent)
                    raise
                source["candidate"] = {
                    "path": str(candidate), "sqlite_sha256": report["candidate_sqlite_sha256"],
                    "report_sha256": candidate_report_hash,
                }
                snapshot = _create_backup(transition_dir, source)
            else:
                # Reuse the already validated complete snapshot; never recopy or overwrite it.
                _assert_snapshot_matches_source(source, snapshot)
            transition_state = _record_transition(source, snapshot, candidate, report, provenance, transition_dir)
            _checkpoint("backup_complete")
            _assert_source_artifacts(base, acquisition_journal, snapshot, allow_copias=False)

        # Re-check all source invariants against the durable backup before changing either file.
        current = _source_state(base, scope, acquisition_journal, allow_transition_mismatch=True)
        base_state, metadata_target = _classify_live(transition_state, snapshot)
        if base_state == "source":
            stage = Path(transition_state["stage_path"])
            _prepare_base_stage(candidate, stage, transition_state["target_description"])
            transition_state = _record(transition_dir, transition_state, "sqlite_replacing")
            _checkpoint("before_sqlite_replace")
            sqlite.restaurar(stage, base, TARGET_REVISION)
            stage.unlink(missing_ok=True)
            _fsync_dir(base.parent)
            transition_state = _record(transition_dir, transition_state, "sqlite_replaced")
            _checkpoint("sqlite_replaced")
            base_state = "target"
        if base_state != "target":
            raise TransitionRejected("La SQLite candidata no llegó al estado de destino")
        if not metadata_target:
            transition_state = _record(transition_dir, transition_state, "journal_committing")
            _checkpoint("before_journal_commit")
            _update_confirmation(Path(str(base) + ".actualizaciones") / "jobs.sqlite", transition_state)
            _checkpoint("journal_committed")
            transition_state = _record(transition_dir, transition_state, "journal_committed")

        live = _source_state(base, scope, acquisition_journal)
        if live["description"] != transition_state["target_description"]:
            raise TransitionRejected("La descripción SQLite posterior no coincide con la candidata")
        if live["source_meta"] != _expected_meta(transition_state, True):
            raise TransitionRejected("El journal no confirma exactamente codigo+descripcion de destino")
        expected_schema = [list(row) for row in snapshot["source"]["coordinator_schema"]]
        if [list(row) for row in live["coordinator_schema"]] != expected_schema:
            raise TransitionRejected("El esquema del journal coordinador cambió")
        _assert_unchanged_histories(live, snapshot)
        stage = Path(transition_state["stage_path"])
        if stage.exists() or stage.is_symlink():
            if stage.is_symlink() or not stage.is_file() or sqlite.comprobar_copia(stage) != transition_state["target_description"]:
                raise TransitionRejected("El staging residual no coincide con la candidata 0008; se conserva para inspección")
            stage.unlink()
            _fsync_dir(stage.parent)
        _assert_source_artifacts(base, acquisition_journal, snapshot, allow_copias=True,
                                 stage_path=stage)
        _checkpoint("before_coordinator_open")

    # The coordinator takes the same lock itself. Opening it after the maintenance locks are released verifies the
    # actual runtime without admitting any update request or touching Acquisition.
    if actualizacion.codigo() != transition_state["target_code_sha256"]:
        raise TransitionRejected("El código runtime cambió antes de abrir la generación adoptada")
    _coordinator_open(base, scope, transition_state["source_generation"])
    transition_state = _record(transition_dir, transition_state, "coordinator_opened")
    _checkpoint("coordinator_opened")
    with _locked([base, Path(str(base) + ".actualizaciones") / "jobs.sqlite",
                  Path(str(base) + ".actualizaciones") / "control" / "jobs.sqlite", acquisition_journal]):
        live = _source_state(base, scope, acquisition_journal)
        if live["source_meta"] != _expected_meta(transition_state, True):
            raise TransitionRejected("El coordinador cambió la confirmación después de abrirse")
        _assert_unchanged_histories(live, snapshot)
    receipt_path = transition_dir / "adoption.json"
    receipt_fields = {
        "format": FORMAT, "action": "adopted", "manifest_sha256": snapshot["manifest_sha256"],
        "candidate_sha256": transition_state["candidate_sha256"],
        "generation": transition_state["source_generation"],
        "source_code_sha256": transition_state["source_code_sha256"],
        "target_code_sha256": transition_state["target_code_sha256"],
        "source_description_sha256": transition_state["source_description_sha256"],
        "target_description_sha256": transition_state["target_description_sha256"],
        "scope_sha256": transition_state["source_scope_sha256"],
        "journal_histories": snapshot["source"]["journal_histories"],
        "coordinator_open_verified": True,
    }
    if receipt_path.exists() or receipt_path.is_symlink():
        if receipt_path.is_symlink() or not receipt_path.is_file():
            raise TransitionRejected("El recibo de adopción existente no es regular")
        receipt = _read_json(receipt_path, "recibo de adopción")
        _verify_seal(receipt, "receipt_sha256", "recibo de adopción")
        if any(receipt.get(key) != value for key, value in receipt_fields.items()):
            raise TransitionRejected("El recibo de adopción existente no coincide con la transición")
    else:
        receipt = _write_receipt(receipt_path, {**receipt_fields, "adopted_at": _now()})
    transition_state = _record(transition_dir, transition_state, "adopted", {"adoption_receipt_sha256": receipt["receipt_sha256"]})
    return {"status": "adopted", "generation": transition_state["source_generation"],
            "target_code_sha256": transition_state["target_code_sha256"],
            "target_description_sha256": transition_state["target_description_sha256"],
            "manifest_sha256": snapshot["manifest_sha256"], "receipt_sha256": receipt["receipt_sha256"]}


def _runtime_probe(root: Path, expected_code: str, base: Path, scope: Path,
                   expected_generation: str | None = None, *, open_coordinator: bool = False) -> dict[str, Any]:
    root = Path(root).resolve()
    if root.is_symlink() or not root.is_dir():
        raise TransitionRejected("El árbol del runtime de origen no existe o no es un directorio")
    code = r'''import json, sys
from pathlib import Path
from persistencia import actualizacion, carga
expected, base, scope, generation, do_open = sys.argv[1:]
reference = carga.referencia_del_codigo()
actual = actualizacion.codigo()
if reference.get("sha256_agregada") != expected or actual != expected:
    raise SystemExit("source runtime fingerprint mismatch")
opened = None
if do_open == "1":
    with actualizacion.Coordinador(Path(base), Path(scope)) as coordinator:
        opened = coordinator.capturar().id
    if opened != generation:
        raise SystemExit("source runtime opened another generation")
print(json.dumps({"code_sha256": actual, "generation": opened}, sort_keys=True))
'''
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(root)
    environment.pop("PYTHONHOME", None)
    try:
        result = subprocess.run(
            [sys.executable, "-c", code, expected_code, str(base), str(scope), expected_generation or "",
             "1" if open_coordinator else "0"],
            cwd=root, env=environment, capture_output=True, text=True, timeout=90, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise TransitionRejected(f"No se pudo comprobar el runtime de origen: {type(error).__name__}") from error
    if result.returncode:
        # Do not forward arbitrary runtime output; report a bounded, credential-free diagnostic only.
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise TransitionRejected(f"El runtime de origen no verifica/abre el coordinador: {detail[-1][:240] if detail else 'error'}")
    try:
        response = json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as error:
        raise TransitionRejected("El runtime de origen devolvió una respuesta de verificación ilegible") from error
    if response.get("code_sha256") != expected_code or (open_coordinator and response.get("generation") != expected_generation):
        raise TransitionRejected("La respuesta del runtime de origen no acredita la huella/generación esperada")
    return response


def _replace_sqlite_file(source: Path, destination: Path, expected_signature: str) -> None:
    destination = Path(destination).resolve()
    source = Path(source).resolve()
    stage = destination.with_name(f".{destination.name}.restore-{uuid.uuid4().hex}.sqlite")
    sqlite.copia_consistente(source, stage)
    os.chmod(stage, 0o600)
    if _db_signature(stage) != expected_signature:
        stage.unlink(missing_ok=True)
        raise TransitionRejected(f"La copia preparada para restaurar no verifica: {destination.name}")
    try:
        journal = Path(str(destination) + "-journal")
        if journal.exists() and journal.stat().st_size:
            raise TransitionRejected(f"Hay un rollback journal no resuelto en {destination}")
        if destination.exists():
            with closing(sqlite3.connect(destination.as_uri() + "?mode=rw", uri=True, timeout=10)) as connection:
                checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                if checkpoint and checkpoint[0] != 0:
                    raise TransitionRejected(f"No se pudo integrar el WAL de {destination}")
        wal = Path(str(destination) + "-wal")
        if wal.exists() and wal.stat().st_size:
            raise TransitionRejected(f"Quedó un WAL no integrado en {destination}")
        if _db_signature(destination) != _db_signature(source):
            # The live database can differ only in the coordinator confirmation when replacing that journal.
            pass
        for suffix in sqlite.ACOMPANANTES:
            sidecar = Path(str(destination) + suffix)
            if sidecar.exists():
                if suffix != "-shm" and sidecar.stat().st_size:
                    raise TransitionRejected(f"No se elimina un acompañante SQLite con contenido: {sidecar}")
                sidecar.unlink()
        os.replace(stage, destination)
        os.chmod(destination, 0o600)
        _fsync_dir(destination.parent)
        if _db_signature(destination) != expected_signature:
            raise TransitionRejected(f"La SQLite restaurada no verifica: {destination.name}")
    finally:
        stage.unlink(missing_ok=True)


def restore(base: str | Path, scope: str | Path, acquisition_journal: str | Path,
            transition_dir: str | Path, source_runtime_root: str | Path, confirmation: str,
            *, _checkpoint=lambda _phase: None) -> dict[str, Any]:
    base = _resolve_argument(base, "La SQLite de origen")
    scope = _resolve_argument(scope, "El ámbito")
    acquisition_journal = _resolve_argument(acquisition_journal, "El journal de Acquisition")
    transition_dir = _resolve_argument(transition_dir, "El directorio de transición")
    _private_dir(transition_dir.parent, "El directorio padre de transición")
    if _within(transition_dir, base.parent) or _within(base.parent, transition_dir):
        raise TransitionRejected("El directorio de transición debe quedar fuera del árbol de instalación")
    transition_state = _load_transition(transition_dir, "adopt")
    snapshot = _load_backup(transition_dir)
    if transition_state.get("snapshot_manifest_sha256") != snapshot["manifest_sha256"]:
        raise TransitionRejected("La transición y el snapshot no coinciden")
    if (transition_state.get("base") != str(base) or transition_state.get("scope") != str(scope)
            or transition_state.get("acquisition_journal") != str(acquisition_journal)):
        raise TransitionRejected("Las rutas del restore no coinciden con las del adopt")
    source_code = _canonical_sha(transition_state.get("source_code_sha256"), "Huella original")
    target_code = _canonical_sha(transition_state.get("target_code_sha256"), "Huella destino")
    current_reference = _source_code_reference()
    if current_reference["sha256_agregada"] != target_code:
        raise TransitionRejected("Restore debe ejecutarse con el código exacto que adoptó 0008")
    expected_confirmation = f"restore enrichment transition {snapshot['manifest_sha256']} services-stopped"
    if confirmation != expected_confirmation:
        raise TransitionRejected(f"Confirmación explícita incorrecta; se exige exactamente: {expected_confirmation}")
    source_runtime_root = _resolve_argument(source_runtime_root, "El runtime original")
    _runtime_probe(source_runtime_root, source_code, base, scope, transition_state["source_generation"])

    backup = transition_dir / "backup"
    db_backups = {
        label: backup / relative for label, relative in {
            "sports": "payload/databases/sports.sqlite",
            "coordinator": "payload/databases/coordinator.sqlite",
            "control": "payload/databases/control.sqlite",
            "acquisition": "payload/databases/acquisition.sqlite",
        }.items()
    }
    live_paths = _db_paths(base, acquisition_journal)
    receipt_path = transition_dir / "restore.json"
    if receipt_path.exists():
        if receipt_path.is_symlink() or not receipt_path.is_file() or stat.S_IMODE(receipt_path.stat().st_mode) & 0o077:
            raise TransitionRejected("El recibo de restore debe ser un archivo privado regular")
        receipt = _read_json(receipt_path, "recibo de restore")
        _verify_seal(receipt, "receipt_sha256", "recibo de restore")
        if receipt.get("manifest_sha256") != snapshot["manifest_sha256"] or receipt.get("action") != "restored":
            raise TransitionRejected("El recibo de restore no corresponde a esta transición")

    with _locked([base, Path(str(base) + ".actualizaciones") / "jobs.sqlite",
                  Path(str(base) + ".actualizaciones") / "control" / "jobs.sqlite", acquisition_journal]):
        live = _source_state(base, scope, acquisition_journal, allow_transition_mismatch=True)
        _assert_unchanged_histories(live, snapshot)
        if live["scope_sha256"] != transition_state["source_scope_sha256"] or live["generation"] != transition_state["source_generation"]:
            raise TransitionRejected("Hay una generación o ámbito posterior; se rechaza el restore")
        if live["source_meta"] not in (_expected_meta(transition_state, False), _expected_meta(transition_state, True)):
            raise TransitionRejected("El journal contiene cambios ajenos al adopt")
        if live["description"] not in (transition_state["source_description"], transition_state["target_description"]):
            raise TransitionRejected("La SQLite actual no coincide con el origen ni la candidata adoptada")
        if live["description"] == transition_state["source_description"] and live["source_meta"] == _expected_meta(transition_state, True):
            if not str(transition_state.get("phase", "")).startswith("restore_"):
                raise TransitionRejected("Estado 0007/confirmación 0008 sin una reversión interrumpida registrada")
        _assert_source_artifacts(base, acquisition_journal, snapshot, allow_copias=True,
                                 stage_path=Path(transition_state["stage_path"]))
        if not receipt_path.exists():
            transition_state = _record(transition_dir, transition_state, "restore_preparing")
            _checkpoint("restore_preparing")

        backup_source = snapshot["source"]
        # Restore each SQLite as an idempotent step. The phase record is durable before and after each replacement.
        if live["description"] != transition_state["source_description"]:
            transition_state = _record(transition_dir, transition_state, "restore_sports_replacing")
            sqlite.restaurar(db_backups["sports"], base, SOURCE_REVISION)
            _checkpoint("restore_sports_replaced")
            transition_state = _record(transition_dir, transition_state, "restore_sports_replaced")
        for label in ("coordinator", "control", "acquisition"):
            signature_label = {"control": "http_control"}.get(label, label)
            source_signature = backup_source["journal_signatures"][signature_label]
            target_path = live_paths[label]
            if _db_signature(target_path) != source_signature:
                transition_state = _record(transition_dir, transition_state, f"restore_{label}_replacing")
                _replace_sqlite_file(db_backups[label], target_path, source_signature)
                _checkpoint(f"restore_{label}_replaced")
                transition_state = _record(transition_dir, transition_state, f"restore_{label}_replaced")

        restored = _source_state(base, scope, acquisition_journal)
        if (restored["description"] != backup_source["description"]
                or restored["source_meta"] != backup_source["confirmed_meta"]
                or restored["generation"] != backup_source["generation"]
                or restored["database_signature"] != backup_source["database_signature"]):
            raise TransitionRejected("La pareja SQLite/journals restaurada no coincide con el snapshot")
        _assert_unchanged_histories(restored, snapshot)
        _assert_source_artifacts(base, acquisition_journal, snapshot, allow_copias=True,
                                 stage_path=Path(transition_state["stage_path"]))
        if restored["description"]["revision"] == TARGET_REVISION:
            verification = sincronizacion.verificar(base)
            if not verification["ok"] or verification.get("cambios_en_seco"):
                raise TransitionRejected(f"La sincronización deportiva restaurada no verifica: {verification}")
        transition_state = _record(transition_dir, transition_state, "source_state_restored")
        _checkpoint("source_state_restored")

    # This is the old, separately identified runtime. We prove its exact fingerprint before changing state, then open
    # and close its real Coordinator after the restored SQLite and all journals are durable.
    opened = _runtime_probe(source_runtime_root, source_code, base, scope,
                            transition_state["source_generation"], open_coordinator=True)
    with _locked([base, Path(str(base) + ".actualizaciones") / "jobs.sqlite",
                  Path(str(base) + ".actualizaciones") / "control" / "jobs.sqlite", acquisition_journal]):
        final = _source_state(base, scope, acquisition_journal)
        if final["source_meta"] != snapshot["source"]["confirmed_meta"]:
            raise TransitionRejected("El runtime original alteró la confirmación al abrir la generación")
        _assert_unchanged_histories(final, snapshot)
    receipt_fields = {
        "format": FORMAT, "action": "restored", "manifest_sha256": snapshot["manifest_sha256"],
        "generation": transition_state["source_generation"], "source_code_sha256": source_code,
        "target_code_sha256": target_code, "source_description_sha256": transition_state["source_description_sha256"],
        "scope_sha256": transition_state["source_scope_sha256"],
        "journal_histories": snapshot["source"]["journal_histories"],
        "source_runtime_open_verified": opened["generation"],
    }
    if receipt_path.exists():
        restored_receipt = _read_json(receipt_path, "recibo de restore")
        _verify_seal(restored_receipt, "receipt_sha256", "recibo de restore")
        if any(restored_receipt.get(key) != value for key, value in receipt_fields.items()):
            raise TransitionRejected("El recibo de restore existente no coincide con la transición")
    else:
        restored_receipt = _write_receipt(receipt_path, {**receipt_fields, "restored_at": _now()})
    transition_state = _record(transition_dir, transition_state, "restored",
                               {"restore_receipt_sha256": restored_receipt["receipt_sha256"]})
    return {"status": "restored", "generation": transition_state["source_generation"],
            "source_code_sha256": source_code, "manifest_sha256": snapshot["manifest_sha256"],
            "receipt_sha256": restored_receipt["receipt_sha256"]}


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    adopt_parser = sub.add_parser("adopt", help="adopt an already prepared 0008 candidate; requires stopped services")
    restore_parser = sub.add_parser("restore", help="restore the coherent 0007 snapshot before any new work")
    for item in (adopt_parser, restore_parser):
        item.add_argument("--base", required=True, type=Path)
        item.add_argument("--scope", required=True, type=Path)
        item.add_argument("--acquisition-journal", required=True, type=Path)
        item.add_argument("--transition-dir", required=True, type=Path)
        item.add_argument("--confirm", required=True)
    adopt_parser.add_argument("--candidate-dir", required=True, type=Path)
    restore_parser.add_argument("--source-runtime-root", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.action == "adopt":
            result = adopt(args.base, args.scope, args.acquisition_journal, args.candidate_dir,
                           args.transition_dir, args.confirm)
        else:
            result = restore(args.base, args.scope, args.acquisition_journal, args.transition_dir,
                             args.source_runtime_root, args.confirm)
    except (TransitionRejected, OSError, sqlite.CopiaNoValida, sqlite3.Error) as error:
        parser.exit(2, f"{args.action} rechazado: {error}\n")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
