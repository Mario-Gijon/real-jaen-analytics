#!/usr/bin/env python3
"""Prepare an unpublished SQLite candidate for the 0007 -> 0008 enrichment transition.

This utility intentionally has no install, adopt, or restore operation. It accepts a standalone SQLite file only;
coordinator backup packages must be validated as a separate, coherent base+journal+manifest set before use.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import tarfile
import tempfile
from collections import Counter
from contextlib import contextmanager
from pathlib import PurePosixPath
from typing import Any
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alembic import command  # noqa: E402
from enrichment.store import (  # noqa: E402
    DEFAULT_ENRICHMENT_VERSION,
    persist_candidate_enrichments,
)
from persistencia import carga, sqlite  # noqa: E402
from persistencia.bloqueo import bloquear  # noqa: E402


SOURCE_REVISION = "0007"
TARGET_REVISION = "0008"
REPORT_NAME = "prepare-report.json"
CANDIDATE_NAME = "candidate.sqlite"
COHERENT_PACKAGE_FORMAT = "rj-analyst-coherent-state-backup-v1"
COHERENT_BUNDLE_NAME = "coherent-bundle.tar"
COHERENT_VERIFICATION_NAME = "verification.json"
COHERENT_DATABASE_PATH = "state/real-jaen.sqlite"
COHERENT_COORDINATOR_PATH = "state/real-jaen.sqlite.actualizaciones/jobs.sqlite"
COHERENT_CONTROL_PATH = "state/real-jaen.sqlite.actualizaciones/control/jobs.sqlite"
COHERENT_ACQUISITION_PATH = "acquisition-jobs/jobs.sqlite"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MIN_FREE_SPACE = 64 * 1024 * 1024
ENRICHMENT_TABLES = {
    "narrative_comment_enrichment": (
        "capture_id", "source_comment_id", "enrichment_version", "category", "attributes_json",
    ),
    "narrative_comment_enrichment_participant": (
        "capture_id", "source_comment_id", "enrichment_version", "participant_position", "source_role",
        "role", "source_id_json", "normalizer_role",
    ),
}
ENRICHMENT_INDEXES = {
    "ix_narrative_comment_enrichment_category_version": (
        "narrative_comment_enrichment", ("category", "enrichment_version"),
    ),
    "ix_narrative_comment_enrichment_version": (
        "narrative_comment_enrichment", ("enrichment_version",),
    ),
    "ix_narrative_comment_enrichment_participant_player_version": (
        "narrative_comment_enrichment_participant", ("source_id_json", "enrichment_version"),
    ),
    "ix_narrative_comment_enrichment_participant_role_version": (
        "narrative_comment_enrichment_participant", ("role", "enrichment_version"),
    ),
}


class PreparationRejected(RuntimeError):
    """The source or candidate could not be verified safely."""


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _safe_package_file(path: Path, label: str) -> Path:
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise PreparationRejected(f"{label} debe ser un archivo regular, no un enlace")
    return path


def _check_space(paths: list[Path], required: int) -> None:
    for path in paths:
        usage = shutil.disk_usage(path)
        if usage.free < required:
            raise PreparationRejected(
                f"Espacio libre insuficiente en {path}: disponibles={usage.free}, necesarios={required} bytes"
            )


def _canonical_archive_path(name: str) -> str:
    if not isinstance(name, str) or not name or "\\" in name or "\x00" in name:
        raise PreparationRejected("El paquete contiene una ruta TAR no válida")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise PreparationRejected(f"Ruta peligrosa en el paquete TAR: {name!r}")
    normalized = path.as_posix()
    if normalized != name.rstrip("/"):
        raise PreparationRejected(f"Ruta no canónica en el paquete TAR: {name!r}")
    return normalized


def _file_identity(path: Path) -> tuple[int, int, int, int, int]:
    info = Path(path).stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _read_package_verification(
    package_dir: Path,
) -> tuple[Path, dict[str, Any], bytes, tuple[int, int, int, int, int]]:
    package_dir = Path(package_dir).expanduser()
    if package_dir.is_symlink() or not package_dir.is_dir():
        raise PreparationRejected("--package-dir debe ser un directorio real del paquete")
    package_dir = package_dir.resolve()
    bundle = _safe_package_file(package_dir / COHERENT_BUNDLE_NAME, "El bundle coherente")
    verification_path = _safe_package_file(
        package_dir / COHERENT_VERIFICATION_NAME, "El informe de verificación",
    )
    verification_raw = verification_path.read_bytes()
    try:
        verification = json.loads(verification_raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PreparationRejected("verification.json no es JSON válido") from error
    required = {
        "status", "bundle_sha256", "bundle_size", "manifest_entries", "verified_file_entries",
        "database_sha256", "generation", "revision", "selected_spanish_captures_with_comments",
        "selected_spanish_comments", "coordinator", "http_control", "acquisition",
    }
    if not isinstance(verification, dict) or not required.issubset(verification):
        raise PreparationRejected("verification.json está incompleto o usa un formato desconocido")
    if verification["status"] != "verified" or verification["revision"] != SOURCE_REVISION:
        raise PreparationRejected("El informe externo no acredita un respaldo 0007 verificado")
    if not isinstance(verification["bundle_size"], int) or verification["bundle_size"] != bundle.stat().st_size:
        raise PreparationRejected("El tamaño del bundle no coincide con verification.json")
    expected_bundle_hash = verification["bundle_sha256"]
    if not isinstance(expected_bundle_hash, str) or not _SHA256_RE.fullmatch(expected_bundle_hash):
        raise PreparationRejected("verification.json no contiene un SHA-256 de bundle válido")
    identity_before = _file_identity(bundle)
    actual_bundle_hash = _sha256_file(bundle)
    identity_after = _file_identity(bundle)
    if identity_before != identity_after:
        raise PreparationRejected("El bundle cambió mientras se comprobaba su SHA-256")
    if actual_bundle_hash != expected_bundle_hash:
        raise PreparationRejected("El SHA-256 del bundle no coincide con verification.json")
    return bundle, verification, verification_raw, identity_after


def _read_archive_manifest(archive: tarfile.TarFile) -> tuple[dict[str, Any], bytes, dict[str, tarfile.TarInfo]]:
    try:
        manifest_member = archive.getmember("manifest.json")
    except KeyError as error:
        raise PreparationRejected("El bundle no contiene manifest.json en su raíz") from error
    if not manifest_member.isfile() or manifest_member.issym() or manifest_member.islnk():
        raise PreparationRejected("manifest.json debe ser un archivo regular")
    stream = archive.extractfile(manifest_member)
    if stream is None:
        raise PreparationRejected("No se pudo leer manifest.json")
    with stream:
        manifest_raw = stream.read(8 * 1024 * 1024 + 1)
    if len(manifest_raw) > 8 * 1024 * 1024:
        raise PreparationRejected("manifest.json excede el tamaño permitido")
    try:
        manifest = json.loads(manifest_raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PreparationRejected("manifest.json no es JSON válido") from error
    if not isinstance(manifest, dict) or manifest.get("format") != COHERENT_PACKAGE_FORMAT:
        raise PreparationRejected("Formato de paquete coherente desconocido")
    if not isinstance(manifest.get("files"), list) or not manifest["files"]:
        raise PreparationRejected("El manifiesto no contiene un inventario completo de archivos")
    entries: dict[str, dict[str, Any]] = {}
    for item in manifest["files"]:
        if not isinstance(item, dict) or set(item) != {"kind", "path", "sha256", "size"}:
            raise PreparationRejected("Entrada incompleta o desconocida en manifest.files")
        if item["kind"] != "file" or not isinstance(item["size"], int) or item["size"] < 0:
            raise PreparationRejected("Solo se admiten entradas de archivo con tamaño no negativo")
        name = _canonical_archive_path(item["path"])
        if name == "manifest.json" or name in entries:
            raise PreparationRejected(f"Ruta duplicada o reservada en manifest.files: {name}")
        if not isinstance(item["sha256"], str) or not _SHA256_RE.fullmatch(item["sha256"]):
            raise PreparationRejected(f"SHA-256 inválido para {name}")
        entries[name] = item
    members: dict[str, tarfile.TarInfo] = {}
    members_by_name: dict[str, tarfile.TarInfo] = {}
    all_names: set[str] = set()
    for member in archive.getmembers():
        name = _canonical_archive_path(member.name)
        if name in all_names:
            raise PreparationRejected(f"Ruta duplicada en el TAR: {name}")
        all_names.add(name)
        if member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
            raise PreparationRejected(f"Tipo TAR no permitido en el paquete: {name}")
        members_by_name[name] = member
        if member.isfile():
            if name == "manifest.json":
                if member.size != manifest_member.size:
                    raise PreparationRejected("Hay más de una versión de manifest.json")
                continue
            if name not in entries:
                raise PreparationRejected(f"Archivo del TAR no declarado en el manifiesto: {name}")
            if member.size != entries[name]["size"]:
                raise PreparationRejected(f"Tamaño TAR/manifiesto distinto: {name}")
            members[name] = member
    if set(members) != set(entries):
        missing = sorted(set(entries) - set(members))
        raise PreparationRejected(f"Faltan archivos declarados en el TAR: {missing[:5]}")
    for name in all_names:
        parts = PurePosixPath(name).parts
        for index in range(1, len(parts)):
            parent = "/".join(parts[:index])
            ancestor = members_by_name.get(parent)
            if ancestor is not None and ancestor.isfile():
                raise PreparationRejected(f"Un archivo TAR contiene otra ruta: {parent}")
    return manifest, manifest_raw, members


def _verify_archive_files(archive: tarfile.TarFile, entries: dict[str, dict[str, Any]],
                          members: dict[str, tarfile.TarInfo]) -> None:
    for name, member in members.items():
        stream = archive.extractfile(member)
        if stream is None:
            raise PreparationRejected(f"No se pudo leer el archivo del bundle: {name}")
        digest = hashlib.sha256()
        size = 0
        with stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                size += len(chunk)
                digest.update(chunk)
        if size != entries[name]["size"] or digest.hexdigest() != entries[name]["sha256"]:
            raise PreparationRejected(f"El contenido no coincide con el manifiesto: {name}")


def _selected_spanish_source_counts(path: Path) -> tuple[int, int]:
    from enrichment.store import SELECTED_SPANISH_CAPTURE_PREDICATE

    with closing(_read_only(path)) as connection:
        captures = connection.execute(
            "SELECT count(*) FROM narrative_comment_selection AS selection "
            "JOIN narrative_comment_capture AS capture ON capture.id=selection.capture_id "
            "AND capture.match_id=selection.match_id WHERE " + SELECTED_SPANISH_CAPTURE_PREDICATE +
            " AND capture.availability='available'"
        ).fetchone()[0]
        comments = connection.execute(
            "SELECT count(*) FROM narrative_comment AS comment "
            "JOIN narrative_comment_selection AS selection ON selection.capture_id=comment.capture_id "
            "JOIN narrative_comment_capture AS capture ON capture.id=selection.capture_id "
            "AND capture.match_id=selection.match_id WHERE " + SELECTED_SPANISH_CAPTURE_PREDICATE +
            " AND capture.availability='available'"
        ).fetchone()[0]
    return captures, comments


def _copy_tar_member(archive: tarfile.TarFile, member: tarfile.TarInfo, destination: Path,
                     *, expected_sha256: str, expected_size: int) -> None:
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    source = archive.extractfile(member)
    if source is None:
        raise PreparationRejected(f"No se pudo extraer el archivo ya verificado: {member.name}")
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        digest = hashlib.sha256()
        size = 0
        with source, os.fdopen(descriptor, "wb") as output:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                size += len(chunk)
                digest.update(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if size != expected_size or digest.hexdigest() != expected_sha256:
            raise PreparationRejected(f"El archivo cambió durante la extracción privada: {member.name}")
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def _rows_summary(rows: list[tuple[Any, ...]]) -> dict[str, Any]:
    states = Counter(row[1] if len(row) > 1 else row[0] for row in rows)
    return {"rows": len(rows), "states": dict(sorted(states.items())),
            # The coherent-state backup writer hashes compact JSON without a trailing newline. Its
            # convention differs from the older coordinator-adoption manifests.
            "rows_sha256": _sha256_bytes(json.dumps(
                rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
            ).encode("utf-8"))}


def _validate_acquisition_jobs(path: Path, expected: dict[str, Any]) -> dict[str, Any]:
    with closing(_read_only(path)) as connection:
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise PreparationRejected("Acquisition jobs.sqlite no supera integrity_check")
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise PreparationRejected("Acquisition jobs.sqlite contiene violaciones de claves foráneas")
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )}
        if tables != {"job"} or tuple(row[1] for row in connection.execute("PRAGMA table_info(job)")) != (
            "id", "request", "state", "data",
        ):
            raise PreparationRejected("Esquema desconocido en Acquisition jobs.sqlite")
        rows = connection.execute("SELECT id,request,state,data FROM job ORDER BY id").fetchall()
    for ident, request, state, data_text in rows:
        try:
            data = json.loads(data_text)
        except (TypeError, json.JSONDecodeError) as error:
            raise PreparationRejected(f"Trabajo Acquisition ilegible: {ident}") from error
        if state in {"queued", "running", "cancelling"} or state not in {
            "completed", "failed", "cancelled", "interrupted",
        }:
            raise PreparationRejected(f"Hay trabajo Acquisition activo o con estado desconocido: {ident}")
        if not isinstance(data, dict) or data.get("id") != ident:
            raise PreparationRejected(f"Identidad incoherente en trabajo Acquisition {ident}")
    summary = {"rows": len(rows), "states": dict(sorted(Counter(row[2] for row in rows).items())),
               "active": [], "rows_sha256": _sha256_bytes(json.dumps(
                   rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
               ).encode("utf-8"))}
    for key in ("rows", "states", "active", "rows_sha256"):
        if expected.get(key) != summary[key]:
            raise PreparationRejected(f"El resumen de Acquisition no coincide con el manifiesto: {key}")
    if expected.get("integrity") != "ok" or expected.get("foreign_key_violations") != 0:
        raise PreparationRejected("El manifiesto no declara Acquisition jobs.sqlite íntegro y sin violaciones FK")
    summary["integrity"] = "ok"
    summary["foreign_key_violations"] = 0
    return summary


@contextmanager
def _validated_package_source(package_dir: Path):
    """Validate the complete read-only tar package, then yield a private standalone 0007 SQLite and provenance."""
    bundle, verification, verification_raw, verified_bundle_identity = _read_package_verification(package_dir)
    with tarfile.open(bundle, mode="r:") as archive:
        manifest, manifest_raw, members = _read_archive_manifest(archive)
        entries = {item["path"]: item for item in manifest["files"]}
        if int(verification["manifest_entries"]) != len(entries) or int(
            verification["verified_file_entries"]
        ) != len(entries):
            raise PreparationRejected("El número de archivos de verification.json no coincide con el manifiesto")
        required_paths = {
            COHERENT_DATABASE_PATH, COHERENT_COORDINATOR_PATH, COHERENT_CONTROL_PATH, COHERENT_ACQUISITION_PATH,
        }
        if not required_paths.issubset(entries):
            raise PreparationRejected("El paquete no incluye la SQLite y todos los journals requeridos")
        if any(name.startswith("/") for name in entries):
            raise PreparationRejected("El manifiesto contiene rutas absolutas")
        sidecar_paths = {
            path + suffix for path in required_paths for suffix in ("-wal", "-shm", "-journal")
        }
        unresolved = sorted(sidecar_paths & set(entries))
        if unresolved:
            raise PreparationRejected(f"El paquete incluye acompañantes SQLite no resueltos: {unresolved}")
        _verify_archive_files(archive, entries, members)

        database_meta = manifest.get("database")
        journal_meta = manifest.get("journals")
        source_meta = manifest.get("source")
        if not isinstance(database_meta, dict) or not isinstance(journal_meta, dict) or not isinstance(source_meta, dict):
            raise PreparationRejected("El manifiesto no contiene database, journals y source")
        if database_meta.get("revision") != SOURCE_REVISION or verification.get("revision") != SOURCE_REVISION:
            raise PreparationRejected(f"La base empaquetada debe estar en Alembic {SOURCE_REVISION}")
        if source_meta.get("database") != "/state/real-jaen.sqlite":
            raise PreparationRejected("La ruta SQLite declarada por el manifiesto no es la esperada")
        for key in ("coordinator", "http_control", "acquisition"):
            if key not in journal_meta or key not in verification:
                raise PreparationRejected(f"Falta el historial de journal {key}")

        core_size = sum(entries[path]["size"] for path in required_paths)
        temp_parent = Path(tempfile.gettempdir()).resolve()
        _check_space(
            [temp_parent], core_size + entries[COHERENT_DATABASE_PATH]["size"] + _MIN_FREE_SPACE,
        )
        with tempfile.TemporaryDirectory(prefix="rj-0008-package-verify-", dir=temp_parent) as temp_name:
            staging = Path(temp_name)
            os.chmod(staging, 0o700)
            base = staging / COHERENT_DATABASE_PATH
            coordinator = staging / COHERENT_COORDINATOR_PATH
            control = staging / COHERENT_CONTROL_PATH
            acquisition = staging / COHERENT_ACQUISITION_PATH
            for name, target in (
                (COHERENT_DATABASE_PATH, base), (COHERENT_COORDINATOR_PATH, coordinator),
                (COHERENT_CONTROL_PATH, control), (COHERENT_ACQUISITION_PATH, acquisition),
            ):
                _copy_tar_member(
                    archive, members[name], target,
                    expected_sha256=entries[name]["sha256"], expected_size=entries[name]["size"],
                )

            description = _description(base)
            if sqlite.revision(base) != SOURCE_REVISION or description["revision"] != SOURCE_REVISION:
                raise PreparationRejected("La SQLite del paquete no está realmente en Alembic 0007")
            if description != database_meta.get("description"):
                raise PreparationRejected("La descripción semántica de SQLite difiere del manifiesto")
            if description.get("huella") != verification.get("database_sha256"):
                raise PreparationRejected("La descripción SQLite difiere del hash semántico de verification.json")
            if database_meta.get("comments") != {
                "selected_spanish_captures_with_comments": verification.get("selected_spanish_captures_with_comments"),
                "selected_spanish_comments": verification.get("selected_spanish_comments"),
            }:
                raise PreparationRejected("Los recuentos españoles del manifiesto y verification.json difieren")
            captures, comments = _selected_spanish_source_counts(base)
            if (captures, comments) != (
                verification["selected_spanish_captures_with_comments"], verification["selected_spanish_comments"],
            ):
                raise PreparationRejected("Los comentarios seleccionados no coinciden con los recuentos del paquete")

            from scripts import adopt_batches_fingerprint as batch_maintenance

            try:
                journal_path, confirmed, coordinator_rows = batch_maintenance._journal(base)
            except batch_maintenance.MaintenanceRejected as error:
                raise PreparationRejected(str(error)) from error
            if Path(journal_path) != coordinator:
                raise PreparationRejected("El journal coordinador no está asociado a la SQLite del paquete")
            try:
                control_path, control_rows = batch_maintenance._control_journal(coordinator.parent)
            except batch_maintenance.MaintenanceRejected as error:
                raise PreparationRejected(str(error)) from error
            if control_path != control:
                raise PreparationRejected("No se encontró el journal HTTP de control esperado")
            coordinator_summary = {
                **_rows_summary(coordinator_rows),
                "active": [],
                "active_or_transitioning": [],
                "integrity": "ok",
                "foreign_key_violations": 0,
            }
            control_summary = {
                "rows": len(control_rows),
                "states": dict(sorted(Counter(row[2] for row in control_rows).items())),
                "active": [],
                "active_or_transitioning": [],
                "integrity": "ok",
                "foreign_key_violations": 0,
                "rows_sha256": _sha256_bytes(json.dumps(
                    control_rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
                ).encode("utf-8")),
            }
            for label, actual in (("coordinator", coordinator_summary), ("http_control", control_summary)):
                expected = journal_meta[label]
                for key in ("rows", "states", "rows_sha256"):
                    if expected.get(key) != actual.get(key):
                        raise PreparationRejected(f"Historial {label} distinto en el manifiesto: {key}")
                if expected.get("active_or_transitioning") != [] or expected.get("integrity") != "ok" or expected.get(
                    "foreign_key_violations"
                ) != 0:
                    raise PreparationRejected(f"El manifiesto no declara un journal {label} íntegro e inactivo")
            if confirmed.get("descripcion") != description:
                raise PreparationRejected("confirmado.detalle.descripcion no coincide con SQLite")
            if confirmed.get("generacion") != database_meta.get("confirmed_generation") or confirmed.get(
                "generacion"
            ) != verification.get("generation"):
                raise PreparationRejected("La generación confirmada difiere del manifiesto")
            try:
                canonical_generation = str(uuid.UUID(confirmed["generacion"]))
            except (KeyError, TypeError, ValueError) as error:
                raise PreparationRejected("La generación confirmada no es un UUID válido") from error
            if canonical_generation != confirmed["generacion"]:
                raise PreparationRejected("La generación confirmada no es un UUID canónico")
            if confirmed.get("ambito") != database_meta.get("scope_fingerprint"):
                raise PreparationRejected("La huella de ámbito confirmada difiere del manifiesto")
            confirmed_code = confirmed.get("codigo")
            if confirmed_code != database_meta.get("code_fingerprint"):
                raise PreparationRejected("La huella de código confirmada difiere del manifiesto")
            for key in ("scope_fingerprint", "code_fingerprint"):
                if not isinstance(database_meta.get(key), str) or not _SHA256_RE.fullmatch(database_meta[key]):
                    raise PreparationRejected(f"Huella inválida en database.{key}")
            acquisition_summary = _validate_acquisition_jobs(acquisition, journal_meta["acquisition"])
            if verification["acquisition"].get("active") != acquisition_summary["active"]:
                raise PreparationRejected("El resumen de trabajos activos de Acquisition difiere")
            for label, actual in (
                ("coordinator", coordinator_summary), ("http_control", control_summary),
                ("acquisition", acquisition_summary),
            ):
                expected = verification[label]
                if not isinstance(expected, dict) or expected.get("active") != []:
                    raise PreparationRejected(f"verification.json declara trabajos activos o no resume {label}")
                for key in ("rows", "states"):
                    if expected.get(key) != actual.get(key):
                        raise PreparationRejected(f"El informe externo de {label} difiere: {key}")

            current_reference = carga.referencia_del_codigo()
            manifest_signature = _sha256_bytes(manifest_raw)
            provenance = {
                "package_format": COHERENT_PACKAGE_FORMAT,
                "package_directory": str(Path(package_dir).resolve()),
                "bundle_sha256": verification["bundle_sha256"],
                "bundle_size": verification["bundle_size"],
                "manifest_sha256": manifest_signature,
                "verification_sha256": _sha256_bytes(verification_raw),
                "confirmed_generation": confirmed["generacion"],
                "confirmed_scope_sha256": confirmed["ambito"],
                "confirmed_code_sha256": confirmed_code,
                "current_destination_code_sha256": current_reference["sha256_agregada"],
                "source_description_sha256": description["huella"],
                "source_database_file_sha256": entries[COHERENT_DATABASE_PATH]["sha256"],
                "coordinator_journal_sha256": entries[COHERENT_COORDINATOR_PATH]["sha256"],
                "control_journal_sha256": entries[COHERENT_CONTROL_PATH]["sha256"],
                "acquisition_journal_sha256": entries[COHERENT_ACQUISITION_PATH]["sha256"],
                "journal_histories": {
                    "coordinator": {key: coordinator_summary[key] for key in (
                        "rows", "states", "rows_sha256", "active_or_transitioning",
                    )},
                    "http_control": {key: control_summary[key] for key in (
                        "rows", "states", "rows_sha256", "active_or_transitioning",
                    )},
                    "acquisition": {key: acquisition_summary[key] for key in (
                        "rows", "states", "rows_sha256", "active",
                    )},
                },
                "verified_payload_files": len(entries),
                "selected_spanish_captures_with_comments": captures,
                "selected_spanish_comments": comments,
                "scope_raw_file_included": False,
            }

            # This SQLite file is a standalone, private, traceable source. It is created only after
            # the complete archive and all journal/database consistency checks passed.
            standalone_source = staging / "validated-source.sqlite"
            sqlite.copia_consistente(base, standalone_source)
            os.chmod(standalone_source, 0o600)
            if _description(standalone_source) != description:
                raise PreparationRejected("La SQLite autónoma no conserva la descripción confirmada")
            if any(Path(str(standalone_source) + suffix).exists() for suffix in sqlite.ACOMPANANTES):
                raise PreparationRejected("La SQLite autónoma mantiene acompañantes pendientes")
            if _file_identity(bundle) != verified_bundle_identity:
                raise PreparationRejected("El bundle cambió durante la validación; se descarta la fuente autónoma")
            yield standalone_source, provenance


def validate_coherent_backup_package(package_dir: str | Path) -> dict[str, Any]:
    """Read-only validate a recognized coherent bundle without installing or migrating it."""
    with _validated_package_source(Path(package_dir)) as (_source, provenance):
        return {"status": "validated_coherent_package", **provenance}


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_only(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)


def _description(path: Path) -> dict[str, Any]:
    try:
        result = sqlite.describir(path)
    except sqlite3.Error as error:
        raise PreparationRejected(f"No se pudo describir SQLite: {path}") from error
    if result["integridad"] != "ok":
        raise PreparationRejected(f"integrity_check no es correcto para {path}: {result['integridad']}")
    if result["claves_foraneas"]:
        raise PreparationRejected(f"foreign_key_check encontró errores en {path}: {result['claves_foraneas'][:5]}")
    return result


def _table_inventory(path: Path) -> dict[str, dict[str, Any]]:
    """Hash each table's ordered rows without changing the source or loading all rows into memory."""
    result: dict[str, dict[str, Any]] = {}
    with closing(_read_only(path)) as connection:
        tables = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "AND name <> 'alembic_version' ORDER BY name"
        )]
        for table in tables:
            quoted_table = table.replace('"', '""')
            columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{quoted_table}")')]
            if not columns:
                raise PreparationRejected(f"La tabla no tiene columnas legibles: {table}")
            projection = ", ".join(f'"{column.replace(chr(34), chr(34) * 2)}"' for column in columns)
            ordering = projection
            digest = hashlib.sha256()
            digest.update(_json_bytes([table, columns]))
            count = 0
            cursor = connection.execute(f'SELECT {projection} FROM "{quoted_table}" ORDER BY {ordering}')
            while rows := cursor.fetchmany(512):
                for row in rows:
                    digest.update(_json_bytes([repr(tuple(row))]))
                    count += 1
            result[table] = {"columns": columns, "rows": count, "sha256": digest.hexdigest()}
    return result


def _schema_objects(path: Path) -> dict[tuple[str, str], dict[str, str | None]]:
    with closing(_read_only(path)) as connection:
        rows = connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall()
    return {(kind, name): {"table": table, "sql": sql} for kind, name, table, sql in rows}


def _index_columns(connection: sqlite3.Connection, index: str) -> tuple[str, ...]:
    quoted = index.replace('"', '""')
    return tuple(row[2] for row in connection.execute(f'PRAGMA index_info("{quoted}")'))


def _foreign_keys(connection: sqlite3.Connection, table: str) -> set[tuple[str, tuple[tuple[str, str], ...]]]:
    quoted = table.replace('"', '""')
    groups: dict[int, list[tuple[int, str, str, str]]] = {}
    for ident, sequence, target, source_column, target_column, *_rest in connection.execute(
        f'PRAGMA foreign_key_list("{quoted}")'
    ):
        groups.setdefault(ident, []).append((sequence, target, source_column, target_column))
    return {
        (rows[0][1], tuple((source, target) for _sequence, _table, source, target in sorted(rows)))
        for rows in groups.values()
    }


def _verify_enrichment_schema(path: Path) -> None:
    expected_primary_keys = {
        "narrative_comment_enrichment": ("capture_id", "source_comment_id", "enrichment_version"),
        "narrative_comment_enrichment_participant": (
            "capture_id", "source_comment_id", "enrichment_version", "participant_position",
        ),
    }
    expected_unique_constraints = {
        "narrative_comment_enrichment": {
            ("capture_id", "source_comment_id", "enrichment_version"),
        },
        "narrative_comment_enrichment_participant": {
            ("capture_id", "source_comment_id", "enrichment_version", "participant_position"),
            ("capture_id", "source_comment_id", "enrichment_version", "source_role"),
        },
    }
    expected_checks = {
        "narrative_comment_enrichment": (
            "length(trim(enrichment_version)) > 0",
            "category is null or length(trim(category)) > 0",
            "json_valid(attributes_json) and json_type(attributes_json) = 'object'",
        ),
        "narrative_comment_enrichment_participant": (
            "participant_position >= 0",
            "length(trim(source_role)) > 0",
            "role is null or length(trim(role)) > 0",
            "normalizer_role is null or length(trim(normalizer_role)) > 0",
            "source_id_json is null or (json_valid(source_id_json) and json_type(source_id_json) in ('integer', 'text'))",
        ),
    }
    with closing(_read_only(path)) as connection:
        for table, expected_columns in ENRICHMENT_TABLES.items():
            info = connection.execute(f'PRAGMA table_info("{table}")').fetchall()
            if tuple(row[1] for row in info) != expected_columns:
                raise PreparationRejected(f"La tabla {table} no coincide con el esquema autorizado de 0008")
            primary_key = tuple(row[1] for row in sorted((row for row in info if row[5]), key=lambda row: row[5]))
            if primary_key != expected_primary_keys[table]:
                raise PreparationRejected(f"La clave primaria de {table} no coincide con 0008")

            unique_constraints = set()
            for index_row in connection.execute(f'PRAGMA index_list("{table}")'):
                if index_row[2]:
                    unique_constraints.add(_index_columns(connection, index_row[1]))
            if unique_constraints != expected_unique_constraints[table]:
                raise PreparationRejected(f"Las restricciones únicas de {table} no coinciden con 0008")

            definition = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,),
            ).fetchone()[0]
            normalized_definition = " ".join(definition.lower().split())
            checks = re.findall(r"\bcheck\s*\(", normalized_definition)
            if len(checks) != len(expected_checks[table]) or any(
                fragment not in normalized_definition for fragment in expected_checks[table]
            ):
                raise PreparationRejected(f"Las restricciones CHECK de {table} no coinciden con 0008")

        expected_indexes = {name for name in ENRICHMENT_INDEXES}
        actual_indexes: set[str] = set()
        for table in ENRICHMENT_TABLES:
            quoted = table.replace('"', '""')
            actual_indexes.update(row[1] for row in connection.execute(f'PRAGMA index_list("{quoted}")')
                                  if not row[1].startswith("sqlite_autoindex_"))
        if actual_indexes != expected_indexes:
            raise PreparationRejected(f"Índices enrichment inesperados: {sorted(actual_indexes)}")
        for index, (table, expected_columns) in ENRICHMENT_INDEXES.items():
            if _index_columns(connection, index) != expected_columns:
                raise PreparationRejected(f"El índice {index} no coincide con el esquema autorizado de 0008")

        expected_fks = {
            "narrative_comment_enrichment": {
                ("narrative_comment", (("capture_id", "capture_id"),
                                        ("source_comment_id", "source_comment_id"))),
            },
            "narrative_comment_enrichment_participant": {
                ("narrative_comment_enrichment", (
                    ("capture_id", "capture_id"),
                    ("source_comment_id", "source_comment_id"),
                    ("enrichment_version", "enrichment_version"),
                )),
            },
        }
        for table, expected in expected_fks.items():
            if _foreign_keys(connection, table) != expected:
                raise PreparationRejected(f"Las claves foráneas de {table} no coinciden con 0008")


def _verify_schema_transition(before: dict, after: dict) -> list[str]:
    before_keys, after_keys = set(before), set(after)
    if not before_keys.issubset(after_keys):
        raise PreparationRejected("La migración eliminó o renombró objetos SQLite preexistentes")
    for key in before_keys:
        if before[key] != after[key]:
            raise PreparationRejected(f"La migración alteró un objeto preexistente: {key[0]} {key[1]}")

    expected = {("table", name) for name in ENRICHMENT_TABLES}
    expected |= {("index", name) for name in ENRICHMENT_INDEXES}
    added = after_keys - before_keys
    if added != expected:
        raise PreparationRejected(f"La migración creó objetos no autorizados: {sorted(added ^ expected)}")
    return [f"{kind}:{name}" for kind, name in sorted(added)]


def _verify_preserved_tables(path: Path, source_inventory: dict[str, dict[str, Any]]) -> None:
    candidate_inventory = _table_inventory(path)
    expected_tables = set(source_inventory) | set(ENRICHMENT_TABLES)
    if set(candidate_inventory) != expected_tables:
        raise PreparationRejected("La candidata contiene tablas no previstas o falta alguna tabla existente")
    changed = [name for name, previous in source_inventory.items() if candidate_inventory[name] != previous]
    if changed:
        raise PreparationRejected(f"La migración o enrichment alteró filas o valores existentes: {changed[:10]}")


def _selected_capture_counts(path: Path, version: str) -> dict[str, int]:
    # Reuse the store's exact selected-Spanish predicate and coverage validator.
    from enrichment.store import (  # local import documents and preserves the single source of selection semantics
        SELECTED_SPANISH_CAPTURE_PREDICATE,
        ensure_selected_enrichments_complete,
    )

    with closing(sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        eligible = connection.execute(
            "SELECT count(*) FROM narrative_comment_selection AS selection "
            "JOIN narrative_comment_capture AS capture ON capture.id = selection.capture_id "
            "AND capture.match_id = selection.match_id WHERE " + SELECTED_SPANISH_CAPTURE_PREDICATE
        ).fetchone()[0]
        empty_captures = connection.execute(
            "SELECT count(*) FROM narrative_comment_selection AS selection "
            "JOIN narrative_comment_capture AS capture ON capture.id = selection.capture_id "
            "AND capture.match_id = selection.match_id WHERE " + SELECTED_SPANISH_CAPTURE_PREDICATE +
            " AND capture.availability = 'empty'"
        ).fetchone()[0]
        comments = connection.execute(
            "SELECT count(*) FROM narrative_comment AS comment "
            "JOIN narrative_comment_selection AS selection ON selection.capture_id = comment.capture_id "
            "JOIN narrative_comment_capture AS capture ON capture.id = selection.capture_id "
            "AND capture.match_id = selection.match_id WHERE " + SELECTED_SPANISH_CAPTURE_PREDICATE +
            " AND capture.availability = 'available'"
        ).fetchone()[0]
        annotations = connection.execute(
            "SELECT count(*) FROM narrative_comment_enrichment AS enrichment "
            "JOIN narrative_comment_selection AS selection ON selection.capture_id = enrichment.capture_id "
            "JOIN narrative_comment_capture AS capture ON capture.id = selection.capture_id "
            "AND capture.match_id = selection.match_id WHERE " + SELECTED_SPANISH_CAPTURE_PREDICATE +
            " AND enrichment.enrichment_version = ?",
            (version,),
        ).fetchone()[0]
        participants = connection.execute(
            "SELECT count(*) FROM narrative_comment_enrichment_participant AS participant "
            "JOIN narrative_comment_selection AS selection ON selection.capture_id = participant.capture_id "
            "JOIN narrative_comment_capture AS capture ON capture.id = selection.capture_id "
            "AND capture.match_id = selection.match_id WHERE " + SELECTED_SPANISH_CAPTURE_PREDICATE +
            " AND participant.enrichment_version = ?",
            (version,),
        ).fetchone()[0]
        ensure_selected_enrichments_complete(connection, version)
    return {
        "selected_spanish_captures": eligible,
        "selected_empty_captures": empty_captures,
        "selected_available_comments": comments,
        "selected_annotations": annotations,
        "selected_participants": participants,
    }


def _write_private_json(path: Path, value: Any) -> None:
    raw = _json_bytes(value)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(path, 0o600)
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _reject_unverified_package_source(source: Path) -> None:
    updates = Path(str(source) + ".actualizaciones")
    payload = next((parent for parent in source.parents if parent.name == "payload"), None)
    manifest = (payload.parent / "manifest.json") if payload is not None else (source.parent / "manifest.json")
    if updates.exists() or manifest.is_file():
        raise PreparationRejected(
            "El origen pertenece a un árbol con journal o manifiesto; esta herramienta solo acepta una SQLite "
            "aislada y no valida paquetes coherentes de coordinador"
        )


def _validate_paths(source: Path, output_dir: Path) -> tuple[Path, Path]:
    source = Path(source).expanduser().resolve()
    output_dir = Path(output_dir).expanduser().resolve()
    if not source.is_file():
        raise PreparationRejected("--source debe indicar una SQLite existente, no un directorio o paquete")
    _reject_unverified_package_source(source)
    if output_dir == source or _within(source, output_dir):
        raise PreparationRejected("El directorio de salida no puede coincidir con ni contener la SQLite de origen")
    protected_roots = (ROOT.resolve(), source.parent)
    if any(_within(output_dir, protected) for protected in protected_roots):
        raise PreparationRejected("La salida debe quedar fuera del repositorio y del árbol que contiene el origen")
    if output_dir.exists():
        raise PreparationRejected(f"El directorio de salida ya existe: {output_dir}")
    if not output_dir.parent.is_dir():
        raise PreparationRejected("El directorio padre de la salida debe existir")
    return source, output_dir


def prepare_candidate(source: str | Path, output_dir: str | Path, *,
                      source_provenance: dict[str, Any] | None = None) -> dict[str, Any]:
    """Create and verify a private 0008 candidate without changing the standalone source SQLite."""
    source, output_dir = _validate_paths(Path(source), Path(output_dir))
    output_dir.mkdir(mode=0o700)
    os.chmod(output_dir, 0o700)
    candidate = output_dir / CANDIDATE_NAME
    committed = False
    try:
        with bloquear(source):
            source_before = _description(source)
            if sqlite.revision(source) != SOURCE_REVISION:
                raise PreparationRejected(f"La SQLite de origen debe estar en {SOURCE_REVISION}")
            source_inventory = _table_inventory(source)
            source_file_hash_before = _sha256_file(source)

            sqlite.copia_consistente(source, candidate)
            os.chmod(candidate, 0o600)
            copied_description = sqlite.comprobar_copia(candidate, source_before)
            source_after_copy = _description(source)
            source_file_hash_after = _sha256_file(source)
            if source_after_copy != source_before or source_file_hash_after != source_file_hash_before:
                raise PreparationRejected("El origen cambió durante la copia; se descarta la candidata")
            if copied_description != source_before:
                raise PreparationRejected("La copia consistente no coincide con la descripción del origen")

        original_objects = _schema_objects(candidate)
        migration_config = carga.configuracion_alembic(candidate)
        command.upgrade(migration_config, TARGET_REVISION)
        os.chmod(candidate, 0o600)
        migrated_description = _description(candidate)
        if migrated_description["revision"] != TARGET_REVISION:
            raise PreparationRejected("La migración no dejó la candidata en Alembic 0008")

        migrated_objects = _schema_objects(candidate)
        added_objects = _verify_schema_transition(original_objects, migrated_objects)
        _verify_enrichment_schema(candidate)
        _verify_preserved_tables(candidate, source_inventory)

        first_run = persist_candidate_enrichments(candidate, DEFAULT_ENRICHMENT_VERSION)
        second_run = persist_candidate_enrichments(candidate, DEFAULT_ENRICHMENT_VERSION)
        if first_run["inserted"] != second_run["unchanged"] or second_run["inserted"] != 0:
            raise PreparationRejected("rules-v1 no pasó la comprobación de idempotencia")
        _verify_preserved_tables(candidate, source_inventory)

        coverage = _selected_capture_counts(candidate, DEFAULT_ENRICHMENT_VERSION)
        if coverage["selected_available_comments"] != coverage["selected_annotations"]:
            raise PreparationRejected("La cobertura rules-v1 no coincide con los comentarios seleccionados")
        final_description = _description(candidate)
        if final_description["revision"] != TARGET_REVISION:
            raise PreparationRejected("La revisión de candidata cambió tras el enriquecimiento")

        # Close/normalize SQLite's write journal on the candidate only, then verify no sidecars remain.
        with closing(sqlite3.connect(candidate)) as connection:
            mode = connection.execute("PRAGMA journal_mode").fetchone()[0].lower()
            if mode == "wal":
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                connection.execute("PRAGMA journal_mode = DELETE")
        leftovers = [str(candidate) + suffix for suffix in sqlite.ACOMPANANTES if Path(str(candidate) + suffix).exists()]
        if leftovers:
            raise PreparationRejected(f"La candidata mantiene journals abiertos: {leftovers}")
        closed_description = _description(candidate)
        if closed_description != final_description:
            raise PreparationRejected("La candidata cambió al cerrar su journal SQLite")
        final_description = closed_description
        os.chmod(candidate, 0o600)
        candidate_hash = _sha256_file(candidate)

        reference = carga.referencia_del_codigo()
        report = {
            "format": "real-jaen-enrichment-candidate-prepare-v1",
            "status": "candidate_not_installed",
            "source_kind": ("validated_coherent_backup_package" if source_provenance else
                            "standalone_sqlite; coordinator package/journals were not supplied or validated"),
            "source_path": str(source),
            "candidate_path": str(candidate),
            "candidate_installed": False,
            **({"validated_source_provenance": source_provenance} if source_provenance else {}),
            "source_revision": SOURCE_REVISION,
            "target_revision": TARGET_REVISION,
            "enrichment_version": DEFAULT_ENRICHMENT_VERSION,
            "code_reference": {
                "text": reference["texto"],
                "commit": reference["commit"],
                "sha256_agregada": reference["sha256_agregada"],
            },
            "source_semantic_description": source_before,
            "candidate_semantic_description": final_description,
            "source_snapshot_semantic_sha256": source_before["huella"],
            "candidate_sqlite_sha256": candidate_hash,
            "source_main_file_sha256_before_copy": source_file_hash_before,
            "source_main_file_sha256_after_copy": source_file_hash_after,
            "preexisting_tables_preserved": True,
            "preexisting_table_count": len(source_inventory),
            "preexisting_tables": source_inventory,
            "schema_objects_added_by_0008": added_objects,
            "enrichment": {
                "first_run": first_run,
                "idempotency_run": second_run,
                **coverage,
                "coverage_complete": True,
            },
            "validation": {
                "source_integrity_check": source_before["integridad"],
                "source_foreign_key_violations": len(source_before["claves_foraneas"]),
                "candidate_integrity_check": final_description["integridad"],
                "candidate_foreign_key_violations": len(final_description["claves_foraneas"]),
                "source_unchanged_during_copy": True,
                "existing_rows_and_values_preserved": True,
                "only_0008_schema_objects_added": True,
                "rules_v1_idempotent": True,
                "candidate_sidecars_absent": True,
            },
            "limitations": [
                *([] if source_provenance else [
                    "No se validó journal, manifiesto ni paquete coherente de coordinador; el origen fue una SQLite suelta.",
                ]),
                *(["El manifiesto del paquete no está firmado criptográficamente; los hashes acreditan integridad respecto al informe local, no autenticidad independiente.",
                   "El paquete contiene la huella de ámbito pero no el archivo de ámbito; se comprobó la igualdad entre manifiesto y confirmado.detalle.ambito, no se recalculó desde bytes de ámbito."]
                  if source_provenance else []),
                "Esta candidata no se instala y no adopta ni modifica ninguna huella confirmada.",
                "Una futura instalación debe verificar la descripción y la huella realmente confirmadas en ese momento.",
            ],
        }
        _write_private_json(output_dir / REPORT_NAME, report)
        committed = True
        return report
    except BaseException:
        if not committed:
            try:
                shutil.rmtree(output_dir)
            except OSError as cleanup_error:
                raise PreparationRejected(
                    f"Falló la preparación y no se pudo limpiar la salida parcial {output_dir}: {cleanup_error}"
                ) from cleanup_error
        raise


def prepare_from_coherent_package(package_dir: str | Path, output_dir: str | Path) -> dict[str, Any]:
    """Validate a coherent bundle read-only, then prepare 0008 from its private standalone SQLite copy."""
    package_dir = Path(package_dir).expanduser()
    if package_dir.is_symlink() or not package_dir.is_dir():
        raise PreparationRejected("--package-dir debe ser un directorio real del paquete")
    package_dir = package_dir.resolve()
    output_dir = Path(output_dir).expanduser().resolve()
    if output_dir == package_dir or _within(output_dir, package_dir) or _within(package_dir, output_dir):
        raise PreparationRejected("La salida no puede coincidir con ni contener el paquete, o quedar dentro de él")
    if _within(output_dir, ROOT.resolve()):
        raise PreparationRejected("La salida debe quedar fuera del repositorio de la API")
    if output_dir.exists():
        raise PreparationRejected(f"El directorio de salida ya existe: {output_dir}")
    if not output_dir.parent.is_dir():
        raise PreparationRejected("El directorio padre de la salida debe existir")
    with _validated_package_source(package_dir) as (source, provenance):
        required_space = source.stat().st_size * 3 + _MIN_FREE_SPACE
        _check_space([source.parent, output_dir.parent], required_space)
        return prepare_candidate(source, output_dir, source_provenance=provenance)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="operation", required=True)
    prepare = subparsers.add_parser("prepare", help="crear una candidata privada 0007 -> 0008 no instalada")
    prepare.add_argument("--source", required=True, type=Path, help="ruta de la SQLite suelta en revisión 0007")
    prepare.add_argument("--output-dir", required=True, type=Path, help="directorio privado nuevo, fuera del árbol de origen")
    validate_package = subparsers.add_parser(
        "validate-package", help="validar en solo lectura un paquete coherente reconocido, sin instalarlo",
    )
    validate_package.add_argument("--package-dir", required=True, type=Path)
    prepare_package = subparsers.add_parser(
        "prepare-package", help="validar un paquete y preparar 0008 desde su SQLite autónoma privada",
    )
    prepare_package.add_argument("--package-dir", required=True, type=Path)
    prepare_package.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.operation == "prepare":
            report = prepare_candidate(args.source, args.output_dir)
        elif args.operation == "validate-package":
            report = validate_coherent_backup_package(args.package_dir)
        else:
            report = prepare_from_coherent_package(args.package_dir, args.output_dir)
    except (PreparationRejected, OSError, sqlite.CopiaNoValida, tarfile.TarError, sqlite3.Error) as error:
        parser.exit(2, f"{args.operation} rechazado: {error}\n")
    if args.operation == "validate-package":
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 0
    result = {
        "status": report["status"],
        "candidate_path": report["candidate_path"],
        "report_path": str(Path(args.output_dir).resolve() / REPORT_NAME),
        "revision": report["target_revision"],
        "candidate_sqlite_sha256": report["candidate_sqlite_sha256"],
    }
    if args.operation == "prepare-package":
        result["source_kind"] = report["source_kind"]
        result["source_provenance"] = report["validated_source_provenance"]
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
