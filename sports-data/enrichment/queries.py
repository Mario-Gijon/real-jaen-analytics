"""Read-only, paginated queries for versioned narrative-comment enrichment.

Callers should open the database with :func:`open_readonly_connection`. Query
results include only the Spanish capture currently selected for each match and
whose selected response hash still matches the capture. Original comments,
participant payloads, enrichment labels, and source provenance are returned
separately so consumers can retain their distinct meanings.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .store import DEFAULT_ENRICHMENT_VERSION, SELECTED_SPANISH_CAPTURE_PREDICATE


DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100

_ATTRIBUTE_FIELDS = frozenset({
    "goal_status",
    "goal_kind",
    "goal_origin",
    "surface",
    "area_zone",
    "sector",
    "prior_pass",
    "assistant_name_text",
    "contexts",
    "infraction_type",
    "location_context",
    "flank",
    "benefiting_team_text",
    "conceding_player_text",
})

_FROM_WHERE = f"""
FROM narrative_comment AS comment
JOIN narrative_comment_enrichment AS enrichment
  ON enrichment.capture_id = comment.capture_id
 AND enrichment.source_comment_id = comment.source_comment_id
JOIN narrative_comment_selection AS selection
  ON selection.capture_id = comment.capture_id
JOIN narrative_comment_capture AS capture
  ON capture.id = selection.capture_id
 AND capture.match_id = selection.match_id
WHERE {SELECTED_SPANISH_CAPTURE_PREDICATE}
  AND enrichment.enrichment_version = ?
"""


class EnrichmentQueryError(ValueError):
    """A query filter or stored enrichment value is invalid."""


def open_readonly_connection(database_path: str | Path) -> sqlite3.Connection:
    """Open SQLite in read-only mode and prohibit writes on this connection."""
    path = Path(database_path).expanduser().resolve()
    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only = ON")
    return connection


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise EnrichmentQueryError(f"{field} must be non-empty text without surrounding whitespace")
    return value


def _positive_id(value: Any, field: str) -> str:
    if isinstance(value, bool):
        raise EnrichmentQueryError(f"{field} must be a positive integer ID")
    if isinstance(value, int):
        if value <= 0:
            raise EnrichmentQueryError(f"{field} must be a positive integer ID")
        return str(value)
    if isinstance(value, str) and value.isascii() and value.isdecimal() and any(c != "0" for c in value):
        return value
    raise EnrichmentQueryError(f"{field} must be a positive integer ID or its decimal string")


def _player_id_literals(value: Any) -> tuple[str, ...]:
    """Return typed JSON literals for IDs stored as either integer or decimal text.

    Canonical decimal IDs such as ``42`` match both JSON ``42`` and ``"42"``.
    A string with leading zeroes matches only that exact string, so ``"0042"``
    is not silently conflated with a different source identifier.
    """
    text = _positive_id(value, "player_id")
    string_literal = json.dumps(text, ensure_ascii=False)
    if not text.startswith("0"):
        return (text, string_literal)
    return (string_literal,)


def _decode_json(value: str, field: str) -> Any:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError) as error:
        raise EnrichmentQueryError(f"stored {field} is not valid JSON") from error


def _filters(
    *,
    match_id: Any,
    source_event_id: Any,
    player_id: Any,
    category: Any,
    original_type: Any,
    role: Any,
    attributes: Any,
) -> tuple[list[str], list[Any]]:
    clauses: list[str] = []
    parameters: list[Any] = []

    if match_id is not None:
        if isinstance(match_id, bool) or not isinstance(match_id, int) or match_id <= 0:
            raise EnrichmentQueryError("match_id must be a positive internal SQLite match ID")
        clauses.append("capture.match_id = ?")
        parameters.append(match_id)

    if source_event_id is not None:
        clauses.append("capture.source_event_id = ?")
        parameters.append(_positive_id(source_event_id, "source_event_id"))

    id_literals = _player_id_literals(player_id) if player_id is not None else None
    role_text = _required_text(role, "role") if role is not None else None

    if id_literals is not None and role_text is not None:
        placeholders = ", ".join("?" for _ in id_literals)
        clauses.append(
            "EXISTS (SELECT 1 FROM narrative_comment_enrichment_participant AS matched_participant "
            "WHERE matched_participant.capture_id = comment.capture_id "
            "AND matched_participant.source_comment_id = comment.source_comment_id "
            "AND matched_participant.enrichment_version = enrichment.enrichment_version "
            f"AND matched_participant.source_id_json IN ({placeholders}) "
            "AND matched_participant.role = ?)"
        )
        parameters.extend(id_literals)
        parameters.append(role_text)
    elif id_literals is not None:
        placeholders = ", ".join("?" for _ in id_literals)
        clauses.append(
            "EXISTS (SELECT 1 FROM narrative_comment_enrichment_participant AS player_participant "
            "WHERE player_participant.capture_id = comment.capture_id "
            "AND player_participant.source_comment_id = comment.source_comment_id "
            "AND player_participant.enrichment_version = enrichment.enrichment_version "
            f"AND player_participant.source_id_json IN ({placeholders}))"
        )
        parameters.extend(id_literals)

    for value, field, sql_field in (
        (category, "category", "enrichment.category"),
        (original_type, "original_type", "comment.type"),
    ):
        if value is None:
            continue
        text = _required_text(value, field)
        clauses.append(f"{sql_field} = ?")
        parameters.append(text)

    if role_text is not None and id_literals is None:
        clauses.append(
            "EXISTS (SELECT 1 FROM narrative_comment_enrichment_participant AS role_participant "
            "WHERE role_participant.capture_id = comment.capture_id "
            "AND role_participant.source_comment_id = comment.source_comment_id "
            "AND role_participant.enrichment_version = enrichment.enrichment_version "
            "AND role_participant.role = ?)"
        )
        parameters.append(role_text)

    if attributes is not None:
        if not isinstance(attributes, Mapping):
            raise EnrichmentQueryError("attributes must be a mapping of supported field names to text or null")
        for field, value in attributes.items():
            if field not in _ATTRIBUTE_FIELDS:
                raise EnrichmentQueryError(f"unsupported enrichment attribute: {field}")
            if field == "contexts":
                if not isinstance(value, str) or not value.strip():
                    raise EnrichmentQueryError("contexts filters require a non-empty text value")
                clauses.append(
                    "EXISTS (SELECT 1 FROM json_each(enrichment.attributes_json, '$.contexts') AS context_item "
                    "WHERE context_item.value = ?)"
                )
                parameters.append(value)
            elif value is None:
                clauses.append(f"json_type(enrichment.attributes_json, '$.{field}') = 'null'")
            elif isinstance(value, str) and value.strip():
                clauses.append(
                    f"json_type(enrichment.attributes_json, '$.{field}') = 'text' "
                    f"AND json_extract(enrichment.attributes_json, '$.{field}') = ?"
                )
                parameters.append(value)
            else:
                raise EnrichmentQueryError(f"attribute {field} must be non-empty text or null")

    return clauses, parameters


def query_enriched_comments(
    connection: sqlite3.Connection,
    *,
    match_id: int | None = None,
    source_event_id: str | int | None = None,
    player_id: str | int | None = None,
    category: str | None = None,
    original_type: str | None = None,
    role: str | None = None,
    attributes: Mapping[str, str | None] | None = None,
    enrichment_version: str = DEFAULT_ENRICHMENT_VERSION,
    limit: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
) -> dict[str, Any]:
    """Return one page of currently selected Spanish comments and their labels.

    ``match_id`` is the internal SQLite match key; ``source_event_id`` is the
    original SofaScore event ID. ``player_id`` matches both integer and
    canonical decimal-string storage while preserving leading-zero IDs exactly.
    When both a player and role are requested, they must belong to the same
    participant row. Role filters use only the effective enriched role:
    ambiguous foul roles and own goals therefore do not appear as confirmed
    foul actions or ordinary scorers. The original ``normalizer_role`` is
    still returned for traceability.
    """
    if not isinstance(connection, sqlite3.Connection):
        raise TypeError("connection must be a sqlite3.Connection")
    if connection.execute("PRAGMA query_only").fetchone()[0] != 1:
        raise EnrichmentQueryError("connection must be read-only; use open_readonly_connection()")
    version = _required_text(enrichment_version, "enrichment_version")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_PAGE_SIZE:
        raise EnrichmentQueryError(f"limit must be between 1 and {MAX_PAGE_SIZE}")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise EnrichmentQueryError("offset must be a non-negative integer")

    clauses, parameters = _filters(
        match_id=match_id,
        source_event_id=source_event_id,
        player_id=player_id,
        category=category,
        original_type=original_type,
        role=role,
        attributes=attributes,
    )
    where = (" AND " + " AND ".join(clauses)) if clauses else ""
    base_parameters = [version, *parameters]

    total = connection.execute(
        "SELECT count(*) " + _FROM_WHERE + where,
        base_parameters,
    ).fetchone()[0]

    rows = connection.execute(
        """SELECT capture.id, capture.match_id, capture.source_event_id, capture.provider,
                  capture.captured_at, capture.http_status, capture.availability,
                  capture.response_sha256, capture.source_path,
                  selection.selected_spanish_sha256, selection.last_successful_source_path,
                  selection.last_attempt_state, selection.last_attempt_at,
                  selection.last_http_status, selection.last_error_code,
                  comment.source_comment_id, comment.source_position, comment.text, comment.type,
                  comment.minute, comment.period_name, comment.is_home, comment.home_score,
                  comment.away_score, comment.incident_id, comment.participants_json, comment.context_json,
                  enrichment.enrichment_version, enrichment.category, enrichment.attributes_json
           """ + _FROM_WHERE + where +
        " ORDER BY capture.match_id, comment.source_position, comment.source_comment_id LIMIT ? OFFSET ?",
        [*base_parameters, limit, offset],
    ).fetchall()

    participant_rows: dict[tuple[int, str], list[dict[str, Any]]] = {}
    if rows:
        keys = [(row[0], row[15]) for row in rows]
        key_clauses = " OR ".join(
            "(capture_id = ? AND source_comment_id = ?)" for _ in keys
        )
        key_parameters = [value for key in keys for value in key]
        participants = connection.execute(
            """SELECT capture_id, source_comment_id, participant_position, source_role,
                      role, source_id_json, normalizer_role
               FROM narrative_comment_enrichment_participant
               WHERE enrichment_version = ? AND (""" + key_clauses + ") "
            "ORDER BY capture_id, source_comment_id, participant_position",
            [version, *key_parameters],
        ).fetchall()
        for participant in participants:
            participant_key = (participant[0], participant[1])
            participant_rows.setdefault(participant_key, []).append({
                "position": participant[2],
                "source_role": participant[3],
                "role": participant[4],
                "source_id": _decode_json(participant[5], "participant source ID")
                if participant[5] is not None else None,
                "normalizer_role": participant[6],
            })

    items = []
    for row in rows:
        capture_id, match_key, event_id = row[0], row[1], row[2]
        source_comment_id = row[15]
        original_participants = _decode_json(row[25], "comment participants")
        original_context = _decode_json(row[26], "comment context")
        participants = participant_rows.get((capture_id, source_comment_id), [])
        for participant in participants:
            source_role = participant["source_role"]
            participant["original"] = (
                original_participants.get(source_role)
                if isinstance(original_participants, dict) else None
            )

        items.append({
            "match_id": match_key,
            "source_event_id": event_id,
            "comment": {
                "source_comment_id": source_comment_id,
                "source_position": row[16],
                "text": row[17],
                "type": row[18],
                "minute": row[19],
                "period_name": row[20],
                "is_home": row[21],
                "home_score": row[22],
                "away_score": row[23],
                "incident_id": row[24],
                "participants": original_participants,
                "context": original_context,
            },
            "enrichment": {
                "version": row[27],
                "category": row[28],
                "attributes": _decode_json(row[29], "enrichment attributes"),
                "participants": participants,
            },
            "provenance": {
                "capture_id": capture_id,
                "provider": row[3],
                "captured_at": row[4],
                "http_status": row[5],
                "availability": row[6],
                "response_sha256": row[7],
                "source_path": row[8],
                "selected_spanish_sha256": row[9],
                "last_successful_source_path": row[10],
                "last_attempt_state": row[11],
                "last_attempt_at": row[12],
                "last_http_status": row[13],
                "last_error_code": row[14],
            },
        })

    return {
        "enrichment_version": version,
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": items,
    }


__all__ = [
    "DEFAULT_PAGE_SIZE",
    "EnrichmentQueryError",
    "MAX_PAGE_SIZE",
    "open_readonly_connection",
    "query_enriched_comments",
]
