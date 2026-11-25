"""Persist deterministic enrichment for the Spanish captures currently selected in SQLite or PostgreSQL.

This module is deliberately separate from Acquisition and from the API. Persistence functions accept a caller-owned
connection and leave its transaction lifecycle to the caller. The original comment and capture rows remain immutable.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .pipeline import enrich_comment_pipeline


DEFAULT_ENRICHMENT_VERSION = "rules-v1"
_SPANISH_PATH = "'/event/' || capture.source_event_id || '/comments/es-la'"
SELECTED_SPANISH_CAPTURE_PREDICATE = f"""
    selection.capture_id IS NOT NULL
    AND selection.selected_spanish_sha256 = capture.response_sha256
    AND capture.provider = 'sofascore'
    AND capture.availability IN ('available', 'empty')
    AND (capture.source_path = {_SPANISH_PATH}
         OR selection.last_successful_source_path = {_SPANISH_PATH})
"""
_SELECTED_CAPTURE = f"""
    JOIN narrative_comment_capture AS capture
      ON capture.id = selection.capture_id AND capture.match_id = selection.match_id
    WHERE {SELECTED_SPANISH_CAPTURE_PREDICATE}
"""


class EnrichmentPersistenceError(RuntimeError):
    """The database or pipeline output cannot be safely persisted."""


class EnrichmentConflict(EnrichmentPersistenceError):
    """An annotation for this comment and version already exists with different contents."""


def _canonical_json(value: Any, field: str) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as error:
        raise EnrichmentPersistenceError(f"{field} is not JSON-serializable") from error


def _source_id_literal(value: Any) -> str | None:
    """Keep valid SofaScore IDs as typed JSON literals (e.g. 42 differs from \"42\")."""
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return _canonical_json(value, "participant.source_id")
    if (isinstance(value, str) and value.isascii() and value.isdecimal()
            and any(character != "0" for character in value)):
        return _canonical_json(value, "participant.source_id")
    raise EnrichmentPersistenceError("pipeline returned an invalid participant source_id")


def _optional_text(value: Any, field: str, *, required: bool = False) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        raise EnrichmentPersistenceError(f"pipeline returned an invalid {field}")
    return value


def _participant_rows(participants: Any) -> list[tuple[int, str, str | None, str | None, str | None]]:
    if not isinstance(participants, list):
        raise EnrichmentPersistenceError("pipeline participants must be a list")
    result = []
    seen_source_roles = set()
    for position, participant in enumerate(participants):
        if not isinstance(participant, Mapping):
            raise EnrichmentPersistenceError(f"pipeline participant {position} must be an object")
        source_role = _optional_text(participant.get("source_role"), "participant.source_role", required=True)
        if source_role in seen_source_roles:
            raise EnrichmentPersistenceError("pipeline returned a duplicate participant source_role")
        seen_source_roles.add(source_role)
        role = _optional_text(participant.get("role"), "participant.role")
        normalizer_role = _optional_text(participant.get("normalizer_role"), "participant.normalizer_role")
        source_id_json = _source_id_literal(participant.get("source_id"))
        result.append((position, source_role, role, source_id_json, normalizer_role))
    return result


def _canonical_saved_attributes(value: str) -> str | None:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None
    try:
        return _canonical_json(parsed, "stored attributes_json")
    except EnrichmentPersistenceError:
        return None


def _canonical_saved_source_id(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        parsed = json.loads(value)
        return _source_id_literal(parsed)
    except (TypeError, json.JSONDecodeError, EnrichmentPersistenceError):
        return None


def _is_postgresql_connection(connection: Any) -> bool:
    return getattr(getattr(connection, "dialect", None), "name", None) == "postgresql"


def _execute(connection: Any, statement: str, parameters: Any = None):
    if _is_postgresql_connection(connection):
        from sqlalchemy import text

        return connection.execute(text(statement), parameters or {})
    if parameters is None:
        return connection.execute(statement)
    return connection.execute(statement, parameters)


def _executemany(connection: Any, statement: str, parameters: list[dict[str, Any]]) -> None:
    if not parameters:
        return
    if _is_postgresql_connection(connection):
        from sqlalchemy import text

        connection.execute(text(statement), parameters)
    else:
        connection.executemany(statement, parameters)


def _fetchall(connection: Any, statement: str, parameters: Any = None):
    return _execute(connection, statement, parameters).fetchall()


def _check_schema(connection: Any) -> None:
    required_columns = {
        "narrative_comment_selection": {"capture_id", "selected_spanish_sha256", "last_successful_source_path"},
        "narrative_comment_capture": {"match_id", "provider", "source_event_id", "source_path", "availability",
                                      "response_sha256"},
        "narrative_comment": {"capture_id", "source_comment_id", "text", "type", "participants_json"},
        "narrative_comment_enrichment": {"capture_id", "source_comment_id", "enrichment_version", "category",
                                          "attributes_json"},
        "narrative_comment_enrichment_participant": {"capture_id", "source_comment_id", "enrichment_version",
                                                      "participant_position", "source_role", "role",
                                                      "source_id_json", "normalizer_role"},
    }
    if _is_postgresql_connection(connection):
        table_names = ", ".join(f"'{table}'" for table in required_columns)
        rows = _fetchall(connection, f"""
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name IN ({table_names})
        """)
        actual_columns: dict[str, set[str]] = {}
        for row in rows:
            actual_columns.setdefault(row[0], set()).add(row[1])
    elif isinstance(connection, sqlite3.Connection):
        actual_columns = {}
        for table in required_columns:
            try:
                actual_columns[table] = {
                    row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')
                }
            except sqlite3.Error as error:
                raise EnrichmentPersistenceError("SQLite schema is unavailable") from error
    else:
        raise TypeError("connection must be a SQLite or PostgreSQL connection")

    for table, expected in required_columns.items():
        if not expected.issubset(actual_columns.get(table, set())):
            raise EnrichmentPersistenceError(f"schema for {table} is incomplete; migration 0008 is required")


def _require_revision_0008(connection: sqlite3.Connection) -> None:
    try:
        revisions = {row[0] for row in connection.execute("SELECT version_num FROM alembic_version")}
    except sqlite3.Error as error:
        raise EnrichmentPersistenceError("Alembic revision 0008 is required for enrichment") from error
    if revisions != {"0008"}:
        raise EnrichmentPersistenceError("Alembic revision 0008 is required for enrichment")


def _selected_annotations(connection: Any, enrichment_version: str):
    """Load current-version annotations and participants in two grouped queries."""
    annotations = {}
    rows = _fetchall(connection, """
        SELECT enrichment.capture_id, enrichment.source_comment_id,
               enrichment.category, enrichment.attributes_json
        FROM narrative_comment_enrichment AS enrichment
        JOIN narrative_comment_selection AS selection ON selection.capture_id = enrichment.capture_id
        """ + _SELECTED_CAPTURE + " AND enrichment.enrichment_version = :version",
        {"version": enrichment_version},
    )
    for row in rows:
        key = (row[0], row[1])
        annotations[key] = (row[2], _canonical_saved_attributes(row[3]), [])

    participant_rows = _fetchall(connection, """
        SELECT enrichment.capture_id, enrichment.source_comment_id,
               participant.participant_position, participant.source_role, participant.role,
               participant.source_id_json, participant.normalizer_role
        FROM narrative_comment_enrichment AS enrichment
        JOIN narrative_comment_enrichment_participant AS participant
          ON participant.capture_id = enrichment.capture_id
         AND participant.source_comment_id = enrichment.source_comment_id
         AND participant.enrichment_version = enrichment.enrichment_version
        JOIN narrative_comment_selection AS selection ON selection.capture_id = enrichment.capture_id
        """ + _SELECTED_CAPTURE + " AND enrichment.enrichment_version = :version"
        " ORDER BY enrichment.capture_id, enrichment.source_comment_id, participant.participant_position",
        {"version": enrichment_version},
    )
    for row in participant_rows:
        key = (row[0], row[1])
        saved = annotations.get(key)
        if saved is not None:
            saved[2].append((row[2], row[3], row[4],
                             _canonical_saved_source_id(row[5]), row[6]))
    return annotations


def _validate_enrichment_version(enrichment_version: str) -> None:
    if not isinstance(enrichment_version, str) or not enrichment_version.strip():
        raise ValueError("enrichment_version must be non-empty text")
    if enrichment_version != enrichment_version.strip():
        raise ValueError("enrichment_version must not have surrounding whitespace")


def _persist_selected_enrichments_in_savepoint(connection: Any, enrichment_version: str) -> dict[str, int | str]:
    eligible_captures = _execute(
        connection,
        "SELECT count(*) FROM narrative_comment_selection AS selection " + _SELECTED_CAPTURE,
    ).fetchone()[0]
    comments = _fetchall(connection, """
        SELECT comment.capture_id, comment.source_comment_id, comment.text, comment.type,
               comment.participants_json
        FROM narrative_comment AS comment
        JOIN narrative_comment_selection AS selection ON selection.capture_id = comment.capture_id
        """ + _SELECTED_CAPTURE + " AND capture.availability = 'available' "
        "ORDER BY comment.capture_id, comment.source_comment_id",
    )

    prepared = []
    for capture_id, source_comment_id, comment_text, original_type, participants_json in comments:
        output = enrich_comment_pipeline({
            "type": original_type,
            "text": comment_text,
            "participants_json": participants_json,
        })
        category = _optional_text(output.get("category"), "category")
        attributes = output.get("attributes")
        if not isinstance(attributes, Mapping):
            raise EnrichmentPersistenceError("pipeline attributes must be an object")
        attributes_json = _canonical_json(attributes, "attributes")
        expected_participants = _participant_rows(output.get("participants"))
        prepared.append((capture_id, source_comment_id, category, attributes_json, expected_participants))

    saved_annotations = _selected_annotations(connection, enrichment_version)
    enrichment_inserts = []
    participant_inserts = []
    unchanged = 0
    for capture_id, source_comment_id, category, attributes_json, expected_participants in prepared:
        key = (capture_id, source_comment_id)
        saved = saved_annotations.get(key)
        expected = (category, attributes_json, expected_participants)
        if saved is not None:
            if saved != expected:
                raise EnrichmentConflict(
                    f"annotation differs for capture={capture_id}, comment={source_comment_id}, "
                    f"version={enrichment_version}"
                )
            unchanged += 1
            continue
        enrichment_inserts.append({
            "capture_id": capture_id,
            "source_comment_id": source_comment_id,
            "enrichment_version": enrichment_version,
            "category": category,
            "attributes_json": attributes_json,
        })
        participant_inserts.extend({
            "capture_id": capture_id,
            "source_comment_id": source_comment_id,
            "enrichment_version": enrichment_version,
            "participant_position": position,
            "source_role": source_role,
            "role": role,
            "source_id_json": source_id_json,
            "normalizer_role": normalizer_role,
        } for position, source_role, role, source_id_json, normalizer_role in expected_participants)

    _executemany(connection, """
        INSERT INTO narrative_comment_enrichment
            (capture_id, source_comment_id, enrichment_version, category, attributes_json)
        VALUES (:capture_id, :source_comment_id, :enrichment_version, :category, :attributes_json)
    """, enrichment_inserts)
    _executemany(connection, """
        INSERT INTO narrative_comment_enrichment_participant
            (capture_id, source_comment_id, enrichment_version, participant_position, source_role, role,
             source_id_json, normalizer_role)
        VALUES (:capture_id, :source_comment_id, :enrichment_version, :participant_position, :source_role,
                :role, :source_id_json, :normalizer_role)
    """, participant_inserts)

    return {
        "enrichment_version": enrichment_version,
        "eligible_captures": eligible_captures,
        "comments_seen": len(comments),
        "inserted": len(enrichment_inserts),
        "unchanged": unchanged,
        "participants_inserted": len(participant_inserts),
    }


def persist_selected_enrichments(
    connection: Any,
    enrichment_version: str = DEFAULT_ENRICHMENT_VERSION,
) -> dict[str, int | str]:
    """Persist selected Spanish comment enrichments using SQLite or PostgreSQL.

    ``capture_id`` and ``source_comment_id`` always identify the immutable source row. Re-running the same version
    verifies existing rows and reports them unchanged; it never replaces a differing annotation. A later rules
    version creates parallel rows, leaving earlier versions intact. Empty selected captures have no comments to save.
    """
    if _is_postgresql_connection(connection):
        return persist_selected_enrichments_postgresql(connection, enrichment_version)
    if not isinstance(connection, sqlite3.Connection):
        raise TypeError("connection must be a SQLite or PostgreSQL connection")
    _validate_enrichment_version(enrichment_version)
    if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise EnrichmentPersistenceError("SQLite foreign keys must be enabled on the connection")
    _check_schema(connection)

    savepoint = f"enrichment_{uuid.uuid4().hex}"
    connection.execute(f"SAVEPOINT {savepoint}")
    try:
        report = _persist_selected_enrichments_in_savepoint(connection, enrichment_version)
        connection.execute(f"RELEASE SAVEPOINT {savepoint}")
        return report
    except BaseException:
        connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        connection.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise


def persist_selected_enrichments_postgresql(
    connection: Any,
    enrichment_version: str = DEFAULT_ENRICHMENT_VERSION,
) -> dict[str, int | str]:
    """Persist enrichments inside a caller-owned PostgreSQL transaction and shared write lock."""
    if not _is_postgresql_connection(connection):
        raise TypeError("connection must be a PostgreSQL SQLAlchemy connection")
    _validate_enrichment_version(enrichment_version)
    if not connection.in_transaction():
        raise ValueError("PostgreSQL transaction must be open and managed by the caller")

    from persistencia.sincronizacion import bloquear_escritura_postgresql

    bloquear_escritura_postgresql(connection)
    _check_schema(connection)
    with connection.begin_nested():
        return _persist_selected_enrichments_in_savepoint(connection, enrichment_version)


def ensure_selected_enrichments_complete(
    connection: Any,
    enrichment_version: str = DEFAULT_ENRICHMENT_VERSION,
) -> None:
    """Reject a candidate if a selected Spanish comment lacks the requested annotation version.

    Selected empty captures are complete: only existing comment rows participate in this check.
    """
    if _is_postgresql_connection(connection):
        return ensure_selected_enrichments_complete_postgresql(connection, enrichment_version)
    if not isinstance(connection, sqlite3.Connection):
        raise TypeError("connection must be a SQLite or PostgreSQL connection")
    _validate_enrichment_version(enrichment_version)
    _check_schema(connection)
    pending = _execute(connection,
        """SELECT 1
           FROM narrative_comment AS comment
           JOIN narrative_comment_selection AS selection ON selection.capture_id = comment.capture_id
           """ + _SELECTED_CAPTURE + " AND capture.availability = 'available' "
        """AND NOT EXISTS (
               SELECT 1
               FROM narrative_comment_enrichment AS enrichment
               WHERE enrichment.capture_id = comment.capture_id
                 AND enrichment.source_comment_id = comment.source_comment_id
                 AND enrichment.enrichment_version = :version
           )
           LIMIT 1""",
        {"version": enrichment_version},
    ).fetchone()
    if pending is not None:
        raise EnrichmentPersistenceError(
            f"selected Spanish comments are missing {enrichment_version} annotations"
        )


def ensure_selected_enrichments_complete_postgresql(
    connection: Any,
    enrichment_version: str = DEFAULT_ENRICHMENT_VERSION,
) -> None:
    """Check completeness on an existing PostgreSQL transaction without reading Alembic metadata."""
    if not _is_postgresql_connection(connection):
        raise TypeError("connection must be a PostgreSQL SQLAlchemy connection")
    _validate_enrichment_version(enrichment_version)
    if not connection.in_transaction():
        raise ValueError("PostgreSQL transaction must be open and managed by the caller")
    _check_schema(connection)
    pending = _execute(connection, """SELECT 1
        FROM narrative_comment AS comment
        JOIN narrative_comment_selection AS selection ON selection.capture_id = comment.capture_id
        """ + _SELECTED_CAPTURE + " AND capture.availability = 'available' " + """
        AND NOT EXISTS (
            SELECT 1 FROM narrative_comment_enrichment AS enrichment
            WHERE enrichment.capture_id = comment.capture_id
              AND enrichment.source_comment_id = comment.source_comment_id
              AND enrichment.enrichment_version = :version
        ) LIMIT 1""",
        {"version": enrichment_version},
    ).fetchone()
    if pending is not None:
        raise EnrichmentPersistenceError(
            f"selected Spanish comments are missing {enrichment_version} annotations"
        )


def persist_candidate_enrichments(
    database_path: str | Path,
    enrichment_version: str = DEFAULT_ENRICHMENT_VERSION,
) -> dict[str, int | str]:
    """Persist and verify enrichment in an unpublished candidate database.

    The caller must pass a coordinator candidate, never the active database. This boundary owns the SQLite
    connection and transaction so the caller can calculate the candidate description only after a durable commit.
    """
    connection = None
    try:
        path = Path(database_path).expanduser().resolve()
        connection = sqlite3.connect(f"{path.as_uri()}?mode=rw", uri=True, timeout=10)
        connection.execute("PRAGMA foreign_keys = ON")
        if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            raise EnrichmentPersistenceError("SQLite foreign keys are unavailable for enrichment")
        _check_schema(connection)
        _require_revision_0008(connection)
        connection.execute("BEGIN IMMEDIATE")
        report = persist_selected_enrichments(connection, enrichment_version)
        ensure_selected_enrichments_complete(connection, enrichment_version)
        foreign_key_error = connection.execute("PRAGMA foreign_key_check").fetchone()
        if foreign_key_error is not None:
            raise EnrichmentPersistenceError("SQLite foreign key check failed after enrichment")
        connection.commit()
        return report
    except BaseException:
        if connection is not None:
            connection.rollback()
        raise
    finally:
        if connection is not None:
            connection.close()


__all__ = [
    "DEFAULT_ENRICHMENT_VERSION",
    "EnrichmentConflict",
    "EnrichmentPersistenceError",
    "SELECTED_SPANISH_CAPTURE_PREDICATE",
    "ensure_selected_enrichments_complete",
    "ensure_selected_enrichments_complete_postgresql",
    "persist_candidate_enrichments",
    "persist_selected_enrichments",
    "persist_selected_enrichments_postgresql",
]
