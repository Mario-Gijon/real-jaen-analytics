"""Isolated FastAPI routes for read-only narrative-comment enrichment."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text as sqlalchemy_text
from sqlalchemy.exc import SQLAlchemyError

from .queries import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    EnrichmentQueryError,
    open_readonly_connection,
    query_enriched_comments,
)
from .store import DEFAULT_ENRICHMENT_VERSION, SELECTED_SPANISH_CAPTURE_PREDICATE


router = APIRouter(tags=["enrichment"])


def get_enrichment_database_path(request: Request) -> Path | None:
    """Resolve the database explicitly configured on this application.

    Tests may override this dependency. In coordinator mode the route instead
    obtains a retained snapshot after validating the query parameters.
    """
    if getattr(request.app.state, "control_mode", False):
        # A coordinator-backed request resolves its path only after the handler
        # has validated filters and atomically retained the current generation.
        return None
    if getattr(request.app.state, "storage", None) == "postgresql":
        # PostgreSQL mode must never fall back to SPORTS_DATA_ENRICHMENT_DB.
        return None
    database_path = getattr(request.app.state, "enrichment_database_path", None)
    if database_path is None:
        raise HTTPException(
            status_code=503,
            detail={"error": "enrichment_unavailable", "reason": "database_not_configured"},
        )
    return Path(database_path)


def _validate_enrichment_database(connection: sqlite3.Connection) -> None:
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    required = {
        "alembic_version",
        "narrative_comment_enrichment",
        "narrative_comment_enrichment_participant",
    }
    if not required.issubset(tables):
        raise HTTPException(
            status_code=503,
            detail={"error": "enrichment_unavailable", "reason": "migration_0008_required"},
        )

    try:
        revisions = {row[0] for row in connection.execute("SELECT version_num FROM alembic_version")}
    except sqlite3.Error as error:
        raise HTTPException(
            status_code=503,
            detail={"error": "enrichment_unavailable", "reason": "migration_0008_required"},
        ) from error
    if "0008" not in revisions:
        raise HTTPException(
            status_code=503,
            detail={"error": "enrichment_unavailable", "reason": "migration_0008_required"},
        )

    try:
        has_version = connection.execute(
            """SELECT 1 FROM narrative_comment_enrichment
               WHERE enrichment_version = ? LIMIT 1""",
            (DEFAULT_ENRICHMENT_VERSION,),
        ).fetchone()
    except sqlite3.Error as error:
        raise HTTPException(
            status_code=503,
            detail={"error": "enrichment_unavailable", "reason": "migration_0008_required"},
        ) from error
    if has_version is None:
        raise HTTPException(
            status_code=503,
            detail={"error": "enrichment_unavailable", "reason": "rules_v1_not_found"},
        )

    try:
        pending = connection.execute(
            f"""SELECT 1
                FROM narrative_comment_selection AS selection
                JOIN narrative_comment_capture AS capture
                  ON capture.id = selection.capture_id AND capture.match_id = selection.match_id
                JOIN narrative_comment AS comment
                  ON comment.capture_id = selection.capture_id
                WHERE {SELECTED_SPANISH_CAPTURE_PREDICATE}
                  AND NOT EXISTS (
                      SELECT 1
                      FROM narrative_comment_enrichment AS enrichment
                      WHERE enrichment.capture_id = comment.capture_id
                        AND enrichment.source_comment_id = comment.source_comment_id
                        AND enrichment.enrichment_version = ?
                  )
                LIMIT 1""",
            (DEFAULT_ENRICHMENT_VERSION,),
        ).fetchone()
    except sqlite3.Error as error:
        raise HTTPException(
            status_code=503,
            detail={"error": "enrichment_unavailable", "reason": "database_unavailable"},
        ) from error
    if pending is not None:
        raise HTTPException(
            status_code=503,
            detail={"error": "enrichment_unavailable", "reason": "enrichment_incomplete"},
        )


_POSTGRESQL_REQUIRED_COLUMNS = {
    ("narrative_comment_selection", column)
    for column in ("capture_id", "match_id", "selected_spanish_sha256", "last_successful_source_path")
} | {
    ("narrative_comment_capture", column)
    for column in ("id", "match_id", "source_event_id", "provider", "source_path", "availability",
                   "response_sha256", "captured_at", "http_status")
} | {
    ("narrative_comment", column)
    for column in ("capture_id", "source_comment_id", "source_position", "text", "type", "minute",
                   "period_name", "is_home", "home_score", "away_score", "incident_id",
                   "participants_json", "context_json")
} | {
    ("narrative_comment_enrichment", column)
    for column in ("capture_id", "source_comment_id", "enrichment_version", "category", "attributes_json")
} | {
    ("narrative_comment_enrichment_participant", column)
    for column in ("capture_id", "source_comment_id", "enrichment_version", "participant_position",
                   "source_role", "role", "source_id_json", "normalizer_role")
}


def _validate_postgresql_enrichment_database(connection) -> None:
    """Check the pg0001 enrichment signature without reading Alembic's private version table."""
    try:
        columns = {
            (row[0], row[1])
            for row in connection.execute(sqlalchemy_text("""
                SELECT table_name, column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name IN (
                      'narrative_comment_selection', 'narrative_comment_capture', 'narrative_comment',
                      'narrative_comment_enrichment', 'narrative_comment_enrichment_participant'
                  )
            """))
        }
    except SQLAlchemyError:
        raise HTTPException(
            status_code=503,
            detail={"error": "enrichment_unavailable", "reason": "migration_0008_required"},
        ) from None
    if not _POSTGRESQL_REQUIRED_COLUMNS.issubset(columns):
        raise HTTPException(
            status_code=503,
            detail={"error": "enrichment_unavailable", "reason": "migration_0008_required"},
        )

    try:
        has_version = connection.execute(sqlalchemy_text(
            "SELECT 1 FROM narrative_comment_enrichment "
            "WHERE enrichment_version = :version LIMIT 1"
        ), {"version": DEFAULT_ENRICHMENT_VERSION}).first()
    except SQLAlchemyError:
        raise HTTPException(
            status_code=503,
            detail={"error": "enrichment_unavailable", "reason": "migration_0008_required"},
        ) from None
    if has_version is None:
        raise HTTPException(
            status_code=503,
            detail={"error": "enrichment_unavailable", "reason": "rules_v1_not_found"},
        )

    try:
        pending = connection.execute(sqlalchemy_text(f"""
            SELECT 1
            FROM narrative_comment_selection AS selection
            JOIN narrative_comment_capture AS capture
              ON capture.id = selection.capture_id AND capture.match_id = selection.match_id
            JOIN narrative_comment AS comment
              ON comment.capture_id = selection.capture_id
            WHERE {SELECTED_SPANISH_CAPTURE_PREDICATE}
              AND NOT EXISTS (
                  SELECT 1
                  FROM narrative_comment_enrichment AS enrichment
                  WHERE enrichment.capture_id = comment.capture_id
                    AND enrichment.source_comment_id = comment.source_comment_id
                    AND enrichment.enrichment_version = :version
              )
            LIMIT 1
        """), {"version": DEFAULT_ENRICHMENT_VERSION}).first()
    except SQLAlchemyError:
        raise HTTPException(
            status_code=503,
            detail={"error": "enrichment_unavailable", "reason": "database_unavailable"},
        ) from None
    if pending is not None:
        raise HTTPException(
            status_code=503,
            detail={"error": "enrichment_unavailable", "reason": "enrichment_incomplete"},
        )


def _parse_attributes(value: str | None) -> dict[str, Any] | None:
    if value is None:
        return None
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as error:
        raise EnrichmentQueryError("attributes must be a JSON object") from error
    if not isinstance(decoded, dict):
        raise EnrichmentQueryError("attributes must be a JSON object")
    return decoded


@router.get("/api/v1/enrichment/comments")
def enriched_comments(
    request: Request,
    match_id: int | None = Query(default=None, gt=0),
    source_event_id: str | None = Query(default=None),
    player_id: str | None = Query(default=None),
    category: str | None = Query(default=None),
    original_type: str | None = Query(default=None),
    role: str | None = Query(default=None),
    attributes: str | None = Query(
        default=None,
        description='Filtro JSON, por ejemplo {"surface":"right_foot"}.',
    ),
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
    database_path: Path | None = Depends(get_enrichment_database_path),
) -> dict[str, Any]:
    """Query one page of selected Spanish comments enriched with rules-v1."""
    try:
        parsed_attributes = _parse_attributes(attributes)
    except EnrichmentQueryError as error:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_filter", "reason": str(error)},
        ) from error

    lease = None
    postgresql_engine = None
    if getattr(request.app.state, "control_mode", False):
        try:
            coordinator = request.app.state.coordinator
            lease = coordinator.capturar(enrichment=True)
            database_path = lease.path
        except Exception as error:
            raise HTTPException(
                status_code=503,
                detail={"error": "enrichment_unavailable", "reason": "generation_unavailable"},
            ) from error
    elif getattr(request.app.state, "storage", None) == "postgresql":
        postgresql_engine = getattr(request.app.state, "postgresql_engine", None)
        if postgresql_engine is None:
            raise HTTPException(
                status_code=503,
                detail={"error": "enrichment_unavailable", "reason": "database_not_configured"},
            )
    elif database_path is None:
        raise HTTPException(
            status_code=503,
            detail={"error": "enrichment_unavailable", "reason": "database_not_configured"},
        )

    try:
        if postgresql_engine is not None:
            try:
                with postgresql_engine.connect() as connection:
                    connection = connection.execution_options(isolation_level="REPEATABLE READ")
                    with connection.begin():
                        connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                        _validate_postgresql_enrichment_database(connection)
                        return query_enriched_comments(
                            connection,
                            match_id=match_id,
                            source_event_id=source_event_id,
                            player_id=player_id,
                            category=category,
                            original_type=original_type,
                            role=role,
                            attributes=parsed_attributes,
                            enrichment_version=DEFAULT_ENRICHMENT_VERSION,
                            limit=limit,
                            offset=offset,
                        )
            except EnrichmentQueryError as error:
                raise HTTPException(
                    status_code=422,
                    detail={"error": "invalid_filter", "reason": str(error)},
                ) from error
            except HTTPException:
                raise
            except SQLAlchemyError:
                # Driver errors can contain connection data; expose only the established API error shape.
                raise HTTPException(
                    status_code=503,
                    detail={"error": "enrichment_unavailable", "reason": "database_unavailable"},
                ) from None

        try:
            connection = open_readonly_connection(database_path)
        except (OSError, sqlite3.Error) as error:
            raise HTTPException(
                status_code=503,
                detail={"error": "enrichment_unavailable", "reason": "database_unavailable"},
            ) from error

        with closing(connection):
            try:
                _validate_enrichment_database(connection)
                return query_enriched_comments(
                    connection,
                    match_id=match_id,
                    source_event_id=source_event_id,
                    player_id=player_id,
                    category=category,
                    original_type=original_type,
                    role=role,
                    attributes=parsed_attributes,
                    enrichment_version=DEFAULT_ENRICHMENT_VERSION,
                    limit=limit,
                    offset=offset,
                )
            except EnrichmentQueryError as error:
                raise HTTPException(
                    status_code=422,
                    detail={"error": "invalid_filter", "reason": str(error)},
                ) from error
            except sqlite3.Error as error:
                raise HTTPException(
                    status_code=503,
                    detail={"error": "enrichment_unavailable", "reason": "database_unavailable"},
                ) from error
    finally:
        if lease is not None:
            lease.release()


__all__ = ["get_enrichment_database_path", "router"]
