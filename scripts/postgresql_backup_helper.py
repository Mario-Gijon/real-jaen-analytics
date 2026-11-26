#!/usr/bin/env python3
"""Private helpers used by the PostgreSQL v3 backup and restore commands.

This file never prints connection strings, SQL row contents, credentials, or bundle contents.
"""

from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat
import sys
import tarfile
import tempfile
import uuid


ACTIVE_ACQUISITION = {"queued", "running", "cancelling"}
FORBIDDEN_KEYS = {"profile", "transport_root", "cookie", "cookies", "token", "password", "secret"}
_URI_WITH_USERINFO = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s/@]+@[^\s]+")
_SECRET_QUERY = re.compile(r"(?i)([?&](?:access_?token|api_?key|authorization|password|secret|token)=)[^&#\s]+")
_BEARER = re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/-]+=*")


def _safe_json(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _safe_path(path: Path) -> None:
    try:
        mode = path.lstat().st_mode
    except OSError:
        raise RuntimeError("expected a regular, non-symlink file") from None
    if not stat.S_ISREG(mode):
        raise RuntimeError("expected a regular, non-symlink file")


def _clean_job_spec(value):
    if isinstance(value, dict):
        return {key: _clean_job_spec(item) for key, item in value.items()
                if not any(forbidden in key.lower() for forbidden in FORBIDDEN_KEYS)}
    if isinstance(value, list):
        return [_clean_job_spec(item) for item in value]
    if isinstance(value, str):
        value = _URI_WITH_USERINFO.sub("[redacted URL with credentials]", value)
        value = _SECRET_QUERY.sub(r"\1[redacted]", value)
        value = _BEARER.sub("[redacted authorization]", value)
    return value


def _validate_operation_ledger(value: object, operation_id: str) -> dict:
    if (not isinstance(value, dict)
            or set(value) != {"schema", "operation_id", "maximum", "sends", "history_matches"}
            or value.get("operation_id") != operation_id or value.get("schema") != 1
            or type(value.get("maximum")) is not int or not 1 <= value["maximum"] <= 50
            or not isinstance(value.get("sends"), list) or len(value["sends"]) > value["maximum"]
            or not isinstance(value.get("history_matches"), list)
            or len(value["history_matches"]) > 3
            or value["history_matches"] != sorted(set(value["history_matches"]))
            or any(type(match_id) is not int or match_id <= 0 for match_id in value["history_matches"])):
        raise RuntimeError("Acquisition operation ledger structure is invalid")
    for send in value["sends"]:
        if (not isinstance(send, dict)
                or not {"job_id", "channel", "path", "status"} <= set(send)
                or not set(send) <= {"job_id", "channel", "path", "status", "http_status", "error"}
                or not isinstance(send["job_id"], str) or not send["job_id"]
                or not isinstance(send["channel"], str) or not send["channel"]
                or not isinstance(send["path"], str) or len(send["path"]) > 160
                or send["status"] not in {"reserved", "completed"}
                or ("http_status" in send and send["http_status"] is not None
                    and type(send["http_status"]) is not int)
                or ("error" in send and send["error"] is not None
                    and not isinstance(send["error"], str))):
            raise RuntimeError("Acquisition operation ledger entry is invalid")
    return value


def _sqlite_open(path: Path):
    _safe_path(path)
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)


def _snapshot_acquisition(root: Path, destination: Path) -> dict:
    if root.is_symlink() or not root.is_dir():
        raise RuntimeError("unsafe Acquisition jobs root")
    root = root.resolve(strict=True)
    source_db = root / "jobs.sqlite"
    with closing(_sqlite_open(source_db)) as source:
        if source.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("Acquisition SQLite integrity check failed")
        rows = source.execute("SELECT id,request,state,data FROM job ORDER BY id").fetchall()
    if any(state in ACTIVE_ACQUISITION for _, _, state, _ in rows):
        raise RuntimeError("active Acquisition jobs prevent a consistent backup")

    destination.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(destination)) as target:
        with target:
            with closing(sqlite3.connect(f"file:{source_db}?mode=ro", uri=True, timeout=5)) as source:
                source.backup(target)
            for job_id, raw in target.execute("SELECT id,data FROM job").fetchall():
                cleaned = _clean_job_spec(json.loads(raw))
                target.execute("UPDATE job SET data=? WHERE id=?",
                               (json.dumps(cleaned, sort_keys=True, separators=(",", ":")), job_id))
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Acquisition SQLite snapshot failed integrity check")
    os.chmod(destination, 0o600)

    files: dict[str, bytes] = {"jobs.sqlite": destination.read_bytes()}
    completed = 0
    work_root = root / "work"
    for job_id, request, state, raw_data in rows:
        try:
            if str(uuid.UUID(job_id)) != job_id or str(uuid.UUID(request)) != request:
                raise ValueError()
            data = json.loads(raw_data)
        except (ValueError, TypeError, json.JSONDecodeError):
            raise RuntimeError("Acquisition job journal contains an invalid identity") from None
        if data.get("id") != job_id or data.get("state") != state or data.get("request_id") != request:
            raise RuntimeError("Acquisition job journal state is inconsistent")
        if state != "completed":
            continue
        folder = work_root / job_id
        if folder.is_symlink() or not folder.is_dir():
            raise RuntimeError("completed Acquisition package directory is missing")
        required = ("job.json", "inventory.json", "report.json", "bundle.json")
        local = {}
        for name in required:
            path = folder / name
            _safe_path(path)
            local[name] = path.read_bytes()
        descriptor = data.get("bundle")
        body = local["bundle.json"]
        report = json.loads(local["report.json"])
        if (not isinstance(descriptor, dict)
                or descriptor != {"bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}
                or report.get("descriptor") != descriptor
                or stat.S_IMODE((folder / "bundle.json").stat().st_mode) & 0o222):
            raise RuntimeError("completed Acquisition package is not sealed and verified")
        inventory = json.loads(local["inventory.json"])
        if inventory.get("sha256") != data.get("inventory_sha256"):
            raise RuntimeError("completed Acquisition inventory does not match its journal")
        try:
            spec = json.loads(local["job.json"])
        except json.JSONDecodeError:
            raise RuntimeError("completed Acquisition job specification is invalid") from None
        local["job.json"] = _safe_json(_clean_job_spec(spec))
        for name, content in local.items():
            files[f"work/{job_id}/{name}"] = content
        completed += 1

    operation_root = root / "operations"
    operation_count = 0
    if operation_root.exists():
        if operation_root.is_symlink() or not operation_root.is_dir():
            raise RuntimeError("unsafe Acquisition operation ledger directory")
        for path in sorted(operation_root.iterdir()):
            if path.suffix != ".json":
                continue
            _safe_path(path)
            operation_id = path.stem
            try:
                if str(uuid.UUID(operation_id)) != operation_id:
                    raise ValueError()
                value = json.loads(path.read_bytes())
            except (ValueError, TypeError, json.JSONDecodeError):
                raise RuntimeError("Acquisition operation ledger is invalid") from None
            value = _validate_operation_ledger(value, operation_id)
            files[f"operations/{path.name}"] = _safe_json(_clean_job_spec(value))
            operation_count += 1

    metadata = {
        "jobs_total": len(rows),
        "jobs_completed": completed,
        "operation_ledgers": operation_count,
        "package_files": completed * 4,
        "file_inventory": {name: {"bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}
                           for name, body in sorted(files.items())},
        "files": len(files),
        "total_bytes": sum(len(body) for body in files.values()),
    }
    with tarfile.open(fileobj=sys.stdout.buffer, mode="w|", format=tarfile.PAX_FORMAT) as archive:
        for name, body in sorted(files.items()):
            info = tarfile.TarInfo(name)
            info.size = len(body)
            info.mode = 0o444 if name.endswith("bundle.json") else 0o600
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            archive.addfile(info, io.BytesIO(body))
    print(json.dumps(metadata, sort_keys=True), file=sys.stderr)
    return metadata


def _validate_archive(stream, root: Path, *, write: bool) -> dict:
    if root.is_symlink() or not root.is_dir():
        raise RuntimeError("unsafe Acquisition jobs root")
    root = root.resolve(strict=True)
    members = {}
    with tarfile.open(fileobj=stream, mode="r|*") as archive:
        for member in archive:
            name = PurePosixPath(member.name)
            if (name.is_absolute() or not name.parts or any(part in {"", ".", ".."} for part in name.parts)
                    or member.issym() or member.islnk() or not member.isfile()
                    or member.name in members):
                raise RuntimeError("Acquisition archive contains an unsafe member")
            allowed = (member.name == "jobs.sqlite"
                       or len(name.parts) == 2 and name.parts[0] == "operations"
                       and name.parts[1].endswith(".json")
                       or len(name.parts) == 3 and name.parts[0] == "work"
                       and name.parts[2] in {"job.json", "inventory.json", "report.json", "bundle.json"})
            if not allowed:
                raise RuntimeError("Acquisition archive contains an unexpected member")
            if name.parts[0] == "operations":
                try:
                    if str(uuid.UUID(Path(name.parts[1]).stem)) != Path(name.parts[1]).stem:
                        raise ValueError()
                except (ValueError, IndexError):
                    raise RuntimeError("Acquisition operation ledger path is invalid") from None
            if name.parts[0] == "work":
                try:
                    if str(uuid.UUID(name.parts[1])) != name.parts[1]:
                        raise ValueError()
                except (ValueError, IndexError):
                    raise RuntimeError("Acquisition archive job path is invalid") from None
            extracted = archive.extractfile(member)
            if extracted is None:
                raise RuntimeError("Acquisition archive member could not be read")
            content = extracted.read()
            if len(content) != member.size:
                raise RuntimeError("Acquisition archive member is truncated")
            members[member.name] = content

    if "jobs.sqlite" not in members:
        raise RuntimeError("Acquisition archive is missing its job journal")
    with tempfile.TemporaryDirectory(prefix="rja-acq-restore-") as temporary:
        database = Path(temporary) / "jobs.sqlite"
        database.write_bytes(members["jobs.sqlite"])
        with closing(sqlite3.connect(database)) as connection:
            with connection:
                if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("Acquisition archive SQLite integrity check failed")
                rows = connection.execute("SELECT id,request,state,data FROM job ORDER BY id").fetchall()
        if any(state in ACTIVE_ACQUISITION for _, _, state, _ in rows):
            raise RuntimeError("Acquisition archive contains active jobs")
        completed = 0
        for job_id, request, state, raw_data in rows:
            data = json.loads(raw_data)
            if data != _clean_job_spec(data):
                raise RuntimeError("Acquisition archive journal contains excluded private metadata")
            if (str(uuid.UUID(request)) != request or data.get("id") != job_id
                    or data.get("request_id") != request or data.get("state") != state):
                raise RuntimeError("Acquisition archive job journal is inconsistent")
            if state != "completed":
                continue
            prefix = f"work/{job_id}/"
            files = {key[len(prefix):]: value for key, value in members.items() if key.startswith(prefix)}
            if set(files) != {"job.json", "inventory.json", "report.json", "bundle.json"}:
                raise RuntimeError("Acquisition archive completed package is incomplete")
            descriptor = data.get("bundle")
            body = files["bundle.json"]
            if descriptor != {"bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}:
                raise RuntimeError("Acquisition archive package does not match its journal")
            if json.loads(files["report.json"]).get("descriptor") != descriptor:
                raise RuntimeError("Acquisition archive report does not match its package")
            if json.loads(files["inventory.json"]).get("sha256") != data.get("inventory_sha256"):
                raise RuntimeError("Acquisition archive inventory does not match its journal")
            if json.loads(files["job.json"]) != _clean_job_spec(json.loads(files["job.json"])):
                raise RuntimeError("Acquisition archive package contains private job metadata")
            completed += 1
        for name, body in members.items():
            if name.startswith("operations/"):
                value = json.loads(body)
                operation_id = Path(name).stem
                if (_validate_operation_ledger(value, operation_id) != value
                        or value != _clean_job_spec(value)):
                    raise RuntimeError("Acquisition archive operation ledger contains private metadata")
    if write:
        if any(root.iterdir()):
            raise RuntimeError("Acquisition restore target is not empty")
        for name, content in sorted(members.items()):
            relative = PurePosixPath(name)
            destination = root.joinpath(*relative.parts)
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(destination, flags, 0o444 if name.endswith("bundle.json") else 0o600)
            try:
                with os.fdopen(fd, "wb", closefd=False) as out:
                    out.write(content)
                    out.flush()
                    os.fsync(out.fileno())
            finally:
                os.close(fd)
        directory_fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    operation_ledgers = sum(name.startswith("operations/") for name in members)
    package_files = sum(name.startswith("work/") for name in members)
    return {"jobs_total": len(rows), "jobs_completed": completed,
            "operation_ledgers": operation_ledgers, "package_files": package_files,
            "files": len(members), "total_bytes": sum(map(len, members.values())),
            "file_inventory": {name: {"bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}
                               for name, content in sorted(members.items())}}


def _fence(root: Path, action: str, token: str) -> None:
    root = root.resolve(strict=True)
    marker = root / "deployment-maintenance"
    if action == "create":
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(marker, flags, 0o600)
        try:
            os.write(fd, (token + "\n").encode("ascii"))
            os.fsync(fd)
        finally:
            os.close(fd)
    elif action == "clear":
        if marker.is_symlink() or not marker.is_file() or marker.read_text(encoding="ascii").strip() != token:
            raise RuntimeError("Acquisition maintenance fence does not belong to this operation")
        marker.unlink()
    else:
        raise RuntimeError("unknown maintenance fence action")
    directory_fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _postgres_snapshot(kind: str) -> dict:
    try:
        from sqlalchemy import create_engine, text
        from sqlalchemy.engine import make_url
    except Exception:
        raise RuntimeError("PostgreSQL snapshot support is unavailable") from None

    if kind == "sports":
        variable, owner, app, database, revision, version_table = (
            "SPORTS_DATA_MIGRATION_DATABASE_URL_FILE", "sports_data_owner", "sports_data_app",
            "sports_data", "pg0005", "alembic_version")
        required_counts = ("team", "player", "match", "narrative_comment", "narrative_comment_enrichment",
                           "sports_data_publication", "sports_data_update_job", "sports_data_photo",
                           "sports_data_photo_blob", "sports_data_photo_import")
    else:
        variable, owner, app, database, revision, version_table = (
            "VIDEO_SERVICE_MIGRATION_DATABASE_URL_FILE", "video_service_owner", "video_service_app",
            "video_service", "vg0002", "video_service_alembic_version")
        required_counts = ("video_service_job", "video_service_calibration", "video_service_catalog_video",
                           "video_service_association", "video_service_match_video_override")

    credential = Path(os.environ[variable])
    _safe_path(credential)
    url = make_url(credential.read_text(encoding="utf-8").strip())
    if url.username != owner or url.database != database or url.host != "postgresql" or url.port != 5432:
        raise RuntimeError("PostgreSQL credential targets an unexpected role or database")
    if url.drivername not in {"postgresql", "postgresql+psycopg"}:
        raise RuntimeError("Psycopg 3 connection is required")
    if set(url.query) - {"sslmode", "sslrootcert", "channel_binding"}:
        raise RuntimeError("PostgreSQL credential has unsupported connection options")
    engine = create_engine(url.set(drivername="postgresql+psycopg", query={"sslmode": "disable"}),
                           pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            actual_db, actual_user, server_num = connection.execute(text("""
                SELECT current_database(), current_user, current_setting('server_version_num')::integer
            """)).one()
            if (actual_db, actual_user) != (database, owner) or not 180000 <= server_num < 190000:
                raise RuntimeError("PostgreSQL server or owner identity is unsupported")
            revision_rows = connection.execute(text(f'SELECT version_num FROM "{version_table}"')).scalars().all()
            if revision_rows != [revision]:
                raise RuntimeError("PostgreSQL schema revision is unsupported")

            tables = connection.execute(text("""
                SELECT tablename FROM pg_catalog.pg_tables
                WHERE schemaname='public' ORDER BY tablename
            """)).scalars().all()
            if not set(required_counts).issubset(tables):
                raise RuntimeError("PostgreSQL schema is missing expected application tables")

            table_manifest = {}
            for table in tables:
                regclass = f'public."{table.replace(chr(34), chr(34) * 2)}"'
                key_columns = connection.execute(text("""
                    SELECT a.attname
                    FROM pg_constraint AS c
                    JOIN LATERAL unnest(c.conkey) WITH ORDINALITY AS key(attnum, position) ON true
                    JOIN pg_attribute AS a ON a.attrelid=c.conrelid AND a.attnum=key.attnum
                    WHERE c.contype='p' AND c.conrelid=to_regclass(:table_name)
                    ORDER BY key.position
                """), {"table_name": regclass}).scalars().all()
                columns = ", ".join('"' + name.replace('"', '""') + '"' for name in key_columns)
                order = columns or "row_to_json(r)::text"
                quoted_table = '"' + table.replace('"', '""') + '"'
                result = connection.execution_options(stream_results=True).exec_driver_sql(
                    f"SELECT row_to_json(r)::text FROM public.{quoted_table} AS r ORDER BY {order}")
                digest = hashlib.sha256()
                count = 0
                for (row,) in result:
                    body = row.encode("utf-8")
                    digest.update(len(body).to_bytes(8, "big"))
                    digest.update(body)
                    count += 1
                table_manifest[table] = {"rows": count, "sha256": digest.hexdigest()}

            counts = {name: table_manifest[name]["rows"] for name in required_counts}
            if kind == "sports":
                generation = connection.execute(text("""
                    SELECT generation_id::text, generation_number
                    FROM sports_data_publication_state WHERE singleton_id=1
                """)).one()
                publications = counts["sports_data_publication"]
                uncertain = connection.execute(text("""
                    SELECT count(*) FROM sports_data_update_job
                    WHERE status IN ('queued','running') OR phase='publishing'
                """)).scalar_one()
                dangling = connection.execute(text("""
                    SELECT count(*) FROM sports_data_publication_state AS state
                    LEFT JOIN sports_data_publication AS publication
                      ON publication.publication_id=state.last_publication_id
                    WHERE state.last_publication_id IS NOT NULL
                      AND (publication.publication_id IS NULL
                           OR publication.result_generation <> state.generation_id)
                """)).scalar_one()
                photo_blobs = []
                photo_total = 0
                photo_rows = connection.execution_options(stream_results=True).exec_driver_sql(
                    "SELECT sha256, byte_count, content FROM sports_data_photo_blob ORDER BY sha256")
                for sha256, byte_count, content in photo_rows:
                    content = bytes(content)
                    actual = hashlib.sha256(content).hexdigest()
                    if actual != sha256 or len(content) != byte_count:
                        raise RuntimeError("PostgreSQL photo blob hash or size is invalid")
                    photo_blobs.append({"sha256": sha256, "bytes": byte_count})
                    photo_total += byte_count
                extra = {"generation": {"id": generation[0], "number": generation[1]},
                         "publications": publications, "uncertain_publication_jobs": int(uncertain),
                         "dangling_publication_state": int(dangling),
                         "photos": {"rows": counts["sports_data_photo"],
                                    "blobs": counts["sports_data_photo_blob"],
                                    "blob_bytes": photo_total, "blob_inventory": photo_blobs}}
            else:
                extra = {"jobs": counts["video_service_job"],
                         "associations": counts["video_service_association"],
                         "calibrations": counts["video_service_calibration"]}

            sequences = []
            sequence_names = connection.execute(text("""
                SELECT n.nspname, c.relname
                FROM pg_class AS c JOIN pg_namespace AS n ON n.oid=c.relnamespace
                WHERE c.relkind='S' AND n.nspname='public' ORDER BY c.relname
            """)).all()
            for schema, name in sequence_names:
                sequence = f'"{schema.replace(chr(34), chr(34) * 2)}"."{name.replace(chr(34), chr(34) * 2)}"'
                last_value, is_called = connection.exec_driver_sql(
                    f"SELECT last_value, is_called FROM {sequence}").one()
                sequences.append({"name": name, "last_value": last_value, "is_called": is_called})

            constraints = connection.execute(text("""
                SELECT c.conrelid::regclass::text, c.conname, c.contype,
                       pg_get_constraintdef(c.oid, true), c.convalidated
                FROM pg_constraint AS c JOIN pg_namespace AS n ON n.oid=c.connamespace
                WHERE n.nspname='public' ORDER BY 1,2
            """)).all()
            indexes = connection.execute(text("""
                SELECT schemaname, tablename, indexname, indexdef
                FROM pg_indexes WHERE schemaname='public' ORDER BY tablename,indexname
            """)).all()
            triggers = connection.execute(text("""
                SELECT t.tgrelid::regclass::text, t.tgname, pg_get_triggerdef(t.oid, true)
                FROM pg_trigger AS t JOIN pg_class AS c ON c.oid=t.tgrelid
                JOIN pg_namespace AS n ON n.oid=c.relnamespace
                WHERE n.nspname='public' AND NOT t.tgisinternal ORDER BY 1,2
            """)).all()
            if any(not row[4] for row in constraints if row[2] == "f"):
                raise RuntimeError("PostgreSQL has an unvalidated foreign key")

            table_grants = connection.execute(text("""
                SELECT table_schema, table_name, privilege_type, is_grantable
                FROM information_schema.role_table_grants
                WHERE grantee=:app AND table_schema='public'
                ORDER BY table_schema,table_name,privilege_type
            """), {"app": app}).all()
            sequence_grants = connection.execute(text("""
                SELECT object_schema, object_name, privilege_type, is_grantable
                FROM information_schema.role_usage_grants
                WHERE grantee=:app AND object_type='SEQUENCE' AND object_schema='public'
                ORDER BY object_schema,object_name,privilege_type
            """), {"app": app}).all()
            database_privileges = connection.execute(text("""
                SELECT has_database_privilege(:app, current_database(), 'CONNECT'),
                       has_schema_privilege(:app, 'public', 'USAGE'),
                       has_schema_privilege(:app, 'public', 'CREATE'),
                       has_database_privilege(:app, CASE WHEN current_database()='sports_data'
                           THEN 'video_service' ELSE 'sports_data' END, 'CONNECT')
            """), {"app": app}).one()
            app_role = connection.execute(text("""
                SELECT rolsuper, rolcreatedb, rolcreaterole, rolcanlogin
                FROM pg_roles WHERE rolname=:app
            """), {"app": app}).one()
            memberships = connection.execute(text("""
                SELECT count(*) FROM pg_auth_members AS m
                JOIN pg_roles AS r ON r.oid=m.member
                WHERE r.rolname=:app
            """), {"app": app}).scalar_one()
            owners = connection.execute(text("""
                SELECT count(*) FROM pg_class AS c JOIN pg_namespace AS n ON n.oid=c.relnamespace
                JOIN pg_roles AS r ON r.oid=c.relowner
                WHERE n.nspname='public' AND c.relkind IN ('r','p','S','v','m','f')
                  AND r.rolname <> :owner
            """), {"owner": owner}).scalar_one()
            default_privileges = connection.execute(text("""
                SELECT pg_get_userbyid(d.defaclrole), COALESCE(n.nspname, ''), d.defaclobjtype,
                       pg_get_userbyid(expanded.grantor),
                       CASE WHEN expanded.grantee=0 THEN 'PUBLIC'
                            ELSE pg_get_userbyid(expanded.grantee) END,
                       expanded.privilege_type, expanded.is_grantable
                FROM pg_default_acl AS d
                LEFT JOIN pg_namespace AS n ON n.oid=d.defaclnamespace
                CROSS JOIN LATERAL aclexplode(d.defaclacl) AS expanded
                ORDER BY 1,2,3,4,5,6,7
            """)).all()
            role_summary = {"application_role": app, "flags": list(app_role),
                            "memberships": int(memberships),
                            "database_privileges": list(database_privileges),
                            "table_grants": [list(row) for row in table_grants],
                            "sequence_grants": [list(row) for row in sequence_grants],
                            "wrong_object_owners": int(owners)}
            if (not app_role[3] or any(app_role[:3]) or memberships or owners
                    or not database_privileges[0] or not database_privileges[1]
                    or database_privileges[2] or database_privileges[3]):
                raise RuntimeError("PostgreSQL runtime role permissions or object ownership are invalid")

            return {
                "database": database,
                "owner_role": owner,
                "server_version_num": int(server_num),
                "schema_revision": revision,
                "table_counts": counts,
                "all_tables": table_manifest,
                "sequences": sequences,
                "constraints": [list(row) for row in constraints],
                "indexes": [list(row) for row in indexes],
                "triggers": [list(row) for row in triggers],
                "runtime_permissions": role_summary,
                "default_privileges": [list(row) for row in default_privileges],
                **extra,
            }
    finally:
        engine.dispose()


def _postgres_identity(kind: str) -> dict:
    """Authenticate with the prepared owner secret without requiring a restored schema."""
    try:
        from sqlalchemy import create_engine, text
        from sqlalchemy.engine import make_url
    except Exception:
        raise RuntimeError("PostgreSQL connection support is unavailable") from None
    if kind == "sports":
        variable, owner, database, app = (
            "SPORTS_DATA_MIGRATION_DATABASE_URL_FILE", "sports_data_owner", "sports_data", "sports_data_app")
    else:
        variable, owner, database, app = (
            "VIDEO_SERVICE_MIGRATION_DATABASE_URL_FILE", "video_service_owner", "video_service", "video_service_app")
    credential = Path(os.environ[variable])
    _safe_path(credential)
    url = make_url(credential.read_text(encoding="utf-8").strip())
    if url.username != owner or url.database != database or url.host != "postgresql" or url.port != 5432:
        raise RuntimeError("PostgreSQL credential targets an unexpected role or database")
    if url.drivername not in {"postgresql", "postgresql+psycopg"}:
        raise RuntimeError("Psycopg 3 connection is required")
    engine = create_engine(url.set(drivername="postgresql+psycopg", query={"sslmode": "disable"}),
                           pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            db, user, version, owner_superuser, app_superuser = connection.execute(text("""
                SELECT current_database(), current_user,
                       current_setting('server_version_num')::integer,
                       (SELECT rolsuper FROM pg_roles WHERE rolname=:owner),
                       (SELECT rolsuper FROM pg_roles WHERE rolname=:app)
            """), {"owner": owner, "app": app}).one()
        if (db, user) != (database, owner) or not 180000 <= version < 190000:
            raise RuntimeError("PostgreSQL owner connection identity is invalid")
        if owner_superuser is not False or app_superuser is not False:
            raise RuntimeError("PostgreSQL runtime or owner role is unexpectedly privileged")
        return {"database": database, "owner_role": owner, "server_version_num": int(version)}
    finally:
        engine.dispose()


def _acquisition_check(root: Path) -> dict:
    if root.is_symlink() or not root.is_dir():
        raise RuntimeError("unsafe Acquisition jobs root")
    with _sqlite_open(root / "jobs.sqlite") as connection:
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("Acquisition SQLite integrity check failed")
        states = dict(connection.execute("SELECT state,count(*) FROM job GROUP BY state").fetchall())
    active = sum(states.get(state, 0) for state in ACTIVE_ACQUISITION)
    return {"active_jobs": active, "jobs_total": sum(states.values()), "states": states}


def _empty_check(root: Path, *, writable: bool = False) -> str:
    if root.is_symlink() or not root.is_dir():
        raise RuntimeError("unsafe volume directory")
    if writable and not os.access(root, os.W_OK | os.X_OK):
        raise RuntimeError("volume is not writable by the current user")
    return "empty" if not any(root.iterdir()) else "nonempty"


def _marker_check(root: Path, name: str) -> str:
    if root.is_symlink() or not root.is_dir() or Path(name).name != name:
        raise RuntimeError("unsafe marker path")
    marker = root / name
    if marker.is_symlink():
        raise RuntimeError("marker path is a symlink")
    return "present" if marker.exists() else "absent"


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="mode", required=True)
    snapshot = sub.add_parser("snapshot")
    snapshot.add_argument("database", choices=("sports", "video"))
    identity = sub.add_parser("identity")
    identity.add_argument("database", choices=("sports", "video"))
    acq_check = sub.add_parser("acquisition-check")
    acq_check.add_argument("--root", type=Path, required=True)
    empty_check = sub.add_parser("empty-check")
    empty_check.add_argument("--root", type=Path, required=True)
    empty_check.add_argument("--writable", action="store_true")
    marker_check = sub.add_parser("marker-check")
    marker_check.add_argument("--root", type=Path, required=True)
    marker_check.add_argument("--name", required=True)
    export = sub.add_parser("acquisition-export")
    export.add_argument("--root", type=Path, required=True)
    export.add_argument("--sqlite-copy", type=Path, required=True)
    validate = sub.add_parser("acquisition-validate")
    validate.add_argument("--root", type=Path, required=True)
    restore = sub.add_parser("acquisition-restore")
    restore.add_argument("--root", type=Path, required=True)
    fence = sub.add_parser("fence")
    fence.add_argument("action", choices=("create", "clear"))
    fence.add_argument("--root", type=Path, required=True)
    fence.add_argument("--token", required=True)
    args = parser.parse_args()

    try:
        if args.mode == "snapshot":
            print(json.dumps(_postgres_snapshot(args.database), sort_keys=True, separators=(",", ":")))
        elif args.mode == "identity":
            print(json.dumps(_postgres_identity(args.database), sort_keys=True, separators=(",", ":")))
        elif args.mode == "acquisition-check":
            print(json.dumps(_acquisition_check(args.root), sort_keys=True))
        elif args.mode == "empty-check":
            print(_empty_check(args.root, writable=args.writable))
        elif args.mode == "marker-check":
            print(_marker_check(args.root, args.name))
        elif args.mode == "acquisition-export":
            _snapshot_acquisition(args.root, args.sqlite_copy)
        elif args.mode == "acquisition-validate":
            summary = _validate_archive(sys.stdin.buffer, args.root, write=False)
            print(json.dumps(summary, sort_keys=True))
        elif args.mode == "acquisition-restore":
            summary = _validate_archive(sys.stdin.buffer, args.root, write=True)
            print(json.dumps(summary, sort_keys=True))
        else:
            _fence(args.root, args.action, args.token)
            print("ok")
    except Exception:
        print("PostgreSQL backup helper rejected the operation", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
