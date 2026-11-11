"""Read-only adapter from SofaScore comment captures to sports-events v1.

The synchronization engine consumes only the neutral contract. Provider field
names, acquired JSONL captures, and Spanish-capture inventory metadata stay in
this adapter.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from .sports_events import validate_sports_events


class SofaScoreAdapterError(ValueError):
    """Raised when a comment response or its provenance cannot be trusted."""


_IDENTITY_KEYS = ("match_id", "matchId", "event_id", "eventId")
_PERIOD_NAMES = {"1ST": "first", "2ND": "second"}


def _identity_text(value: Any) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (str, int)):
        text = str(value).strip()
        return text or None
    raise SofaScoreAdapterError(f"Identidad de partido no válida: {value!r}.")


def _check_identities(document: dict, comments: list, expected_match_id: str) -> None:
    expected = _identity_text(expected_match_id)
    if expected is None:
        raise SofaScoreAdapterError("Se requiere un match_id esperado explícito.")
    for key in _IDENTITY_KEYS:
        if key in document and document[key] is not None:
            actual = _identity_text(document[key])
            if actual != expected:
                raise SofaScoreAdapterError(
                    f"La respuesta identifica el partido {actual}, se esperaba {expected}."
                )
    event = document.get("event")
    if isinstance(event, dict) and event.get("id") is not None:
        actual = _identity_text(event["id"])
        if actual != expected:
            raise SofaScoreAdapterError(
                f"La respuesta identifica el partido {actual}, se esperaba {expected}."
            )
    for index, comment in enumerate(comments):
        if not isinstance(comment, dict):
            raise SofaScoreAdapterError(f"comments[{index}] no es un objeto.")
        for key in _IDENTITY_KEYS:
            if key in comment and comment[key] is not None:
                actual = _identity_text(comment[key])
                if actual != expected:
                    raise SofaScoreAdapterError(
                        f"comments[{index}] pertenece al partido {actual}, no a {expected}."
                    )


def _position_number(value: Any, index: int) -> float:
    if isinstance(value, bool):
        raise SofaScoreAdapterError(f"comments[{index}].position no puede ser booleano.")
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError as exc:
            raise SofaScoreAdapterError(
                f"comments[{index}].position debe ser numérico para ordenar cronológicamente."
            ) from exc
    else:
        raise SofaScoreAdapterError(f"comments[{index}].position no es numérico.")
    if not math.isfinite(number):
        raise SofaScoreAdapterError(f"comments[{index}].position debe ser finito.")
    return number


def _chronological_rows(comments: list, *, newest_first: bool) -> tuple[list[tuple[int, dict, Any]], str]:
    has_position = ["position" in comment and comment["position"] is not None
                    for comment in comments]
    if any(has_position) and not all(has_position):
        raise SofaScoreAdapterError(
            "position está presente solo en algunos comentarios; el orden cronológico es ambiguo."
        )
    if all(has_position):
        positioned = [
            (index, comment, comment["position"], _position_number(comment["position"], index))
            for index, comment in enumerate(comments)
        ]
        numeric_positions = [row[3] for row in positioned]
        if len(set(numeric_positions)) != len(numeric_positions):
            raise SofaScoreAdapterError("position contiene duplicados; el orden cronológico es ambiguo.")
        # The provider's position is inverse to chronology: larger positions are older.
        positioned.sort(key=lambda row: -row[3])
        return [(index, comment, position) for index, comment, position, _ in positioned], \
            "provider_position_descending_inverse_chronology"

    rows = [(index, comment, index) for index, comment in enumerate(comments)]
    if newest_first:
        rows.reverse()
        policy = "provider_array_reversed_newest_first"
    else:
        policy = "provider_array_preserved_chronological"
    return rows, policy


def _source_minute(comment: dict, index: int) -> str | int | None:
    value = comment.get("minute")
    if value is None:
        value = comment.get("time")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise SofaScoreAdapterError(f"comments[{index}].minute/time debe ser texto, entero o null.")
    return value


def _period_and_type(comment: dict, original_type: str) -> tuple[str | None, str, str | None]:
    period_name = comment.get("periodName")
    if period_name is not None and not isinstance(period_name, str):
        raise SofaScoreAdapterError("periodName debe ser texto o null.")
    source_period = (_PERIOD_NAMES.get(period_name.strip().upper(), "unknown")
                     if period_name else None)
    period: str | None = source_period
    normalized = "other"
    note = None

    if original_type == "matchStarted":
        if source_period == "first":
            normalized, period = "match_start", "first"
        elif source_period == "second":
            normalized, period = "period_start", "second"
        else:
            note = "matchStarted_without_recognized_periodName"
    elif original_type == "endFirstHalf":
        if source_period == "second":
            period, note = "unknown", "type_period_conflict"
        else:
            normalized, period = "period_end", "first"
    elif original_type == "endSecondHalf":
        if source_period == "first":
            period, note = "unknown", "type_period_conflict"
        else:
            normalized, period = "period_end", "second"
    elif original_type == "matchEnded":
        if source_period == "first":
            period, note = "unknown", "type_period_conflict"
        else:
            normalized = "match_end"
    elif original_type == "addedTime":
        if source_period in {"first", "second"}:
            normalized = "added_time"
        else:
            period, note = "unknown", "addedTime_without_recognized_periodName"
    return period, normalized, note


def convert_sofascore_response(response: dict, *, expected_match_id: str,
                               language: str | None = None,
                               source_metadata: dict | None = None,
                               newest_first: bool = True) -> dict:
    """Convert a SofaScore-compatible response without inventing video times.

    A raw SofaScore comment uses ``time`` and may omit ``position``. A compatible
    internal export can instead provide ``minute`` and inverse-chronology
    ``position``. The source value is retained; only the ordering index is
    normalized. Minute labels are not promoted to video seconds.
    """
    if not isinstance(response, dict):
        raise SofaScoreAdapterError("La respuesta SofaScore debe ser un objeto JSON.")
    comments = response.get("comments")
    if not isinstance(comments, list) or not comments:
        raise SofaScoreAdapterError("La respuesta no contiene una lista comments no vacía.")
    expected_id = _identity_text(expected_match_id)
    if expected_id is None:
        raise SofaScoreAdapterError("Se requiere un match_id esperado explícito.")
    _check_identities(response, comments, expected_id)
    rows, order_policy = _chronological_rows(comments, newest_first=newest_first)

    events = []
    position_basis = "provider_position" if "provider_position" in order_policy else "provider_array_index"
    for source_order, (input_index, comment, source_position) in enumerate(rows):
        original_type = comment.get("type")
        if not isinstance(original_type, str) or not original_type.strip():
            raise SofaScoreAdapterError(f"comments[{input_index}].type es obligatorio.")
        original_type = original_type.strip()
        period, normalized_type, note = _period_and_type(comment, original_type)
        source_id = comment.get("id")
        if source_id is not None and (isinstance(source_id, bool)
                                      or not isinstance(source_id, (str, int))):
            raise SofaScoreAdapterError(f"comments[{input_index}].id debe ser texto, entero o null.")
        source_text = comment.get("text")
        if source_text is not None and not isinstance(source_text, str):
            raise SofaScoreAdapterError(f"comments[{input_index}].text debe ser texto o null.")
        game_clock_seconds = comment.get("game_clock_seconds")
        if game_clock_seconds is not None and (
                isinstance(game_clock_seconds, bool) or not isinstance(game_clock_seconds, int)
                or game_clock_seconds < 0):
            raise SofaScoreAdapterError(
                f"comments[{input_index}].game_clock_seconds debe ser un entero >= 0 o null."
            )
        event = {
            "source_order": source_order,
            "source_id": source_id,
            "original_type": original_type,
            "normalized_type": normalized_type,
            "period": period,
            "source_minute": _source_minute(comment, input_index),
            "game_clock_seconds": game_clock_seconds,
            "source_period_name": comment.get("periodName"),
            "source_text": source_text,
            "source_position": source_position,
        }
        if note:
            event["normalization_note"] = note
        events.append(event)

    metadata = {
        "provider": "SofaScore",
        "match_id": expected_id,
        "language": language or response.get("language"),
        "comment_count": len(events),
        "source_order_policy": order_policy,
        "source_position_basis": position_basis,
        "video_timestamp_policy": "never_derived_from_source_position_or_source_minute",
    }
    if isinstance(source_metadata, dict):
        metadata.update(source_metadata)
    return validate_sports_events({
        "contract": "video-sync.sports-events",
        "version": 1,
        "source_metadata": metadata,
        "events": events,
    })


def _read_capture_file(path: Path, match_id: str) -> tuple[dict, dict]:
    """Read either a response JSON or one matching row in acquired comments JSONL."""
    try:
        raw_bytes = path.read_bytes()
        raw_text = raw_bytes.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise SofaScoreAdapterError(f"No se pudo leer la captura {path}: {exc}") from exc
    if path.suffix.lower() != ".jsonl":
        try:
            document = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise SofaScoreAdapterError(f"JSON no válido en {path}: {exc}") from exc
        if not isinstance(document, dict):
            raise SofaScoreAdapterError("El archivo JSON de entrada debe contener un objeto.")
        return document, {
            "capture_format": "json",
            "capture_file": str(path.resolve()),
            "capture_response_sha256": hashlib.sha256(raw_bytes).hexdigest(),
        }

    matches = []
    for line_number, line in enumerate(raw_text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SofaScoreAdapterError(
                f"JSONL no válido en {path}, línea {line_number}: {exc}"
            ) from exc
        if not isinstance(row, dict) or _identity_text(row.get("event_id")) != str(match_id):
            continue
        if row.get("status") != 200 or not isinstance(row.get("raw_b64"), str):
            continue
        try:
            response_bytes = base64.b64decode(row["raw_b64"], validate=True)
        except (binascii.Error, ValueError) as exc:
            raise SofaScoreAdapterError(
                f"raw_b64 inválido para event_id={match_id} en línea {line_number}."
            ) from exc
        digest = hashlib.sha256(response_bytes).hexdigest()
        if row.get("raw_sha256") and digest != row["raw_sha256"]:
            raise SofaScoreAdapterError(
                f"raw_sha256 no coincide para event_id={match_id} en línea {line_number}."
            )
        try:
            document = json.loads(response_bytes)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise SofaScoreAdapterError(
                f"El cuerpo capturado de event_id={match_id} no es JSON válido."
            ) from exc
        if not isinstance(document, dict):
            raise SofaScoreAdapterError("El cuerpo capturado debe ser un objeto JSON.")
        matches.append((document, {
            "capture_format": "acquisition_comments_jsonl",
            "capture_file": str(path.resolve()),
            "capture_event_id": str(match_id),
            "capture_status": 200,
            "capture_line": line_number,
            "captured_at": row.get("captured_at"),
            "capture_response_sha256": digest,
        }))
    if not matches:
        raise SofaScoreAdapterError(
            f"No hay respuesta HTTP 200 para el partido {match_id} en {path}."
        )
    distinct_digests = {item[1]["capture_response_sha256"] for item in matches}
    if len(distinct_digests) != 1:
        raise SofaScoreAdapterError(
            f"Hay capturas distintas para el partido {match_id}; seleccione una captura concreta."
        )
    return matches[0]


def _read_selected_inventory(path: Path, match_id: str, response_sha256: str | None) -> dict:
    try:
        inventory = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SofaScoreAdapterError(f"No se pudo leer inventario de selección {path}: {exc}") from exc
    if not isinstance(inventory, dict) or not isinstance(inventory.get("comments"), list):
        raise SofaScoreAdapterError("El inventario no contiene la lista comments esperada.")
    rows = [row for row in inventory["comments"]
            if isinstance(row, dict) and _identity_text(row.get("event_id")) == str(match_id)]
    if not rows:
        raise SofaScoreAdapterError(
            f"El inventario no incluye captura de comentarios para el partido {match_id}."
        )
    identities = {(row.get("source_path"), row.get("response_sha256"),
                   row.get("availability"), row.get("spanish_complete")) for row in rows}
    if len(identities) != 1:
        raise SofaScoreAdapterError(
            f"El inventario contiene metadatos de selección contradictorios para {match_id}."
        )
    row = rows[0]
    if row.get("availability") != "available" or row.get("spanish_complete") is not True:
        raise SofaScoreAdapterError("La captura no está marcada como comentarios españoles completos.")
    inventory_hash = row.get("response_sha256")
    if response_sha256 and inventory_hash != response_sha256:
        raise SofaScoreAdapterError("El hash de la respuesta no coincide con la captura española seleccionada.")
    source_path = row.get("source_path")
    if not isinstance(source_path, str) or not source_path.endswith("/comments/es-la"):
        raise SofaScoreAdapterError("La selección no acredita la ruta española es-la.")
    return {
        "inventory_file": str(path.resolve()),
        "inventory_generation_id": inventory.get("generation_id"),
        "inventory_sha256": inventory.get("sha256"),
        "inventory_acquisition_id": path.parent.name,
        "source_path": source_path,
        "availability": row.get("availability"),
        "spanish_complete": row.get("spanish_complete"),
        "response_sha256": inventory_hash,
    }


def convert_capture_file(path: Path, *, expected_match_id: str,
                         language: str | None = None,
                         selection_inventory: Path | None = None,
                         newest_first: bool = True) -> dict:
    """Load an explicit offline capture and convert it to the neutral contract."""
    response, capture_metadata = _read_capture_file(path, str(expected_match_id))
    selected_metadata = None
    response_hash = capture_metadata.get("capture_response_sha256")
    if selection_inventory is not None:
        selected_metadata = _read_selected_inventory(
            selection_inventory, str(expected_match_id), response_hash)
        if language is None:
            language = "es-la"
    metadata = dict(capture_metadata)
    if selected_metadata:
        metadata["selected_capture"] = selected_metadata
    return convert_sofascore_response(
        response,
        expected_match_id=str(expected_match_id),
        language=language,
        source_metadata=metadata,
        newest_first=newest_first,
    )


def write_contract_new(path: Path, document: dict) -> None:
    """Write a new document without replacing an existing diagnostic artifact."""
    validate_sports_events(document)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as stream:
            json.dump(document, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
    except FileExistsError as exc:
        raise SofaScoreAdapterError(f"No se sobrescribe el archivo existente: {path}") from exc
