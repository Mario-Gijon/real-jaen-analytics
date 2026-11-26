#!/usr/bin/env python3
"""Read-only PostgreSQL checks used by the SINBAD² deployment preflight."""

from __future__ import annotations

import os
from pathlib import Path
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.engine import URL, make_url


CHECKS = {
    "sports-schema": (
        "SPORTS_DATA_MIGRATION_DATABASE_URL_FILE",
        "sports_data_owner",
        "sports_data",
        "pg0005",
        "alembic_version",
    ),
    "video-schema": (
        "VIDEO_SERVICE_MIGRATION_DATABASE_URL_FILE",
        "video_service_owner",
        "video_service",
        "vg0002",
        "video_service_alembic_version",
    ),
}
APP_CHECKS = {
    "sports-app": ("SPORTS_DATA_DATABASE_URL_FILE", "sports_data_app", "sports_data", "video_service",
                   "alembic_version"),
    "video-app": ("VIDEO_SERVICE_DATABASE_URL_FILE", "video_service_app", "video_service", "sports_data",
                  "video_service_alembic_version"),
}


def database_url(variable: str, username: str, database: str) -> URL:
    path = Path(os.environ[variable])
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("migration credential file is unavailable")
    url = make_url(path.read_text(encoding="utf-8").strip())
    if (url.username != username or url.database != database
            or url.host != "postgresql" or url.port != 5432):
        raise RuntimeError("migration credential targets an unexpected role or database")
    if url.drivername not in {"postgresql", "postgresql+psycopg"}:
        raise RuntimeError("Psycopg 3 is required")
    if set(url.query) - {"sslmode", "sslrootcert", "channel_binding"}:
        raise RuntimeError("migration credential contains unsupported connection options")
    # Both server and client are attached to the dedicated internal Docker network.
    return url.set(drivername="postgresql+psycopg", query={"sslmode": "disable"})


def run() -> None:
    mode = sys.argv[1] if len(sys.argv) == 2 else ""
    if mode in APP_CHECKS:
        variable, username, database, other_database, version_table = APP_CHECKS[mode]
        url = database_url(variable, username, database)
        engine = create_engine(url, pool_pre_ping=True)
        try:
            with engine.connect() as connection:
                actual_db, actual_user, is_superuser, can_create_db, can_create_role, can_create_public, \
                    has_migration_table_access, can_connect_other_db = connection.execute(text(f"""
                    SELECT current_database(), current_user,
                           role.rolsuper, role.rolcreatedb, role.rolcreaterole,
                           has_schema_privilege(current_user, 'public', 'CREATE'),
                           has_table_privilege(current_user, 'public.{version_table}', 'SELECT'),
                           has_database_privilege(current_user, :other_database, 'CONNECT')
                    FROM pg_roles AS role WHERE role.rolname=current_user
                """), {"other_database": other_database}).one()
            if (actual_db, actual_user) != (database, username):
                raise RuntimeError("application connection identity does not match its service")
            if any((is_superuser, can_create_db, can_create_role, can_create_public,
                    has_migration_table_access, can_connect_other_db)):
                raise RuntimeError("application role has an unexpected PostgreSQL privilege")
        finally:
            engine.dispose()

        other_engine = create_engine(url.set(database=other_database), pool_pre_ping=True)
        try:
            try:
                with other_engine.connect() as connection:
                    connection.execute(text("SELECT 1")).scalar_one()
            except DBAPIError:
                pass
            else:
                raise RuntimeError("application role can connect to the other service database")
        finally:
            other_engine.dispose()
        print("ok")
        return

    if mode in CHECKS:
        variable, username, database, revision, version_table = CHECKS[mode]
        url = database_url(variable, username, database)
        engine = create_engine(url, pool_pre_ping=True)
        try:
            with engine.connect() as connection:
                connection.execute(text("SET TRANSACTION READ ONLY"))
                actual_db, actual_user, pg_version = connection.execute(text("""
                    SELECT current_database(), current_user, current_setting('server_version_num')::integer
                """)).one()
                actual_revision = connection.execute(text(
                    f"SELECT version_num FROM {version_table}"
                )).scalars().all()
            if (actual_db, actual_user) != (database, username):
                raise RuntimeError("database identity does not match the configured owner role")
            if not 180000 <= pg_version < 190000:
                raise RuntimeError("PostgreSQL 18 is required")
            if actual_revision != [revision]:
                raise RuntimeError("the prepared PostgreSQL schema revision is not supported")
            print("ok")
        finally:
            engine.dispose()
        return

    if mode == "sports-active":
        variable, username, database, _, _table = CHECKS["sports-schema"]
        query = "SELECT count(*) FROM sports_data_update_job WHERE status IN ('queued','running')"
    elif mode == "video-active":
        variable, username, database, _, _table = CHECKS["video-schema"]
        query = "SELECT count(*) FROM video_service_job WHERE status IN ('pending','downloading','calibrating','saving')"
    else:
        raise RuntimeError("unknown read-only deployment check")

    url = database_url(variable, username, database)
    engine = create_engine(url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            count = connection.execute(text(query)).scalar_one()
        print(int(count))
    finally:
        engine.dispose()


if __name__ == "__main__":
    try:
        run()
    except Exception:
        # Do not print DBAPI exceptions: driver diagnostics may contain a connection URL.
        print("PostgreSQL deployment preflight failed", file=sys.stderr)
        raise SystemExit(1)
