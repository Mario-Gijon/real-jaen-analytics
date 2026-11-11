"""Neutral, versioned input contract for optional match-event evidence."""
from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Any

from .sync import event_minute_to_clock


CONTRACT = "video-sync.sports-events"
VERSION = 1
NORMALIZED_TYPES = {
    "match_start", "period_start", "period_end", "match_end", "added_time", "other",
}
PERIODS = {"first", "second", "unknown", None}
EVENT_CLOCK_MATCH_TOLERANCE_SECONDS = 300
EVENT_CLOCK_CONFLICT_THRESHOLD_SECONDS = 600


class SportsEventsValidationError(ValueError):
    """Raised when an optional sports-events document violates contract v1."""


def _source_minute_as_match_clock(event: dict) -> int | None:
    minute = event.get("source_minute")
    period = event.get("period")
    if minute is None or period not in {"first", "second"}:
        return None
    try:
        return event_minute_to_clock(str(minute), period)
    except ValueError:
        return None


def validate_sports_events(document: Any) -> dict:
    if not isinstance(document, dict):
        raise SportsEventsValidationError("El documento de eventos debe ser un objeto JSON.")
    if document.get("contract") != CONTRACT:
        raise SportsEventsValidationError(f"contract debe ser {CONTRACT!r}.")
    version = document.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version != VERSION:
        raise SportsEventsValidationError(f"Versión no admitida: {document.get('version')!r}.")
    events = document.get("events")
    if not isinstance(events, list):
        raise SportsEventsValidationError("events debe ser una lista ordenada.")

    previous_order = -1
    for index, event in enumerate(events):
        prefix = f"events[{index}]"
        if not isinstance(event, dict):
            raise SportsEventsValidationError(f"{prefix} debe ser un objeto.")
        if any(key in event for key in ("video_second", "video_position", "video_position_seconds")):
            raise SportsEventsValidationError(
                f"{prefix}: los eventos de partido no pueden declarar posiciones del vídeo."
            )
        order = event.get("source_order")
        if isinstance(order, bool) or not isinstance(order, int) or order < 0 or order <= previous_order:
            raise SportsEventsValidationError("source_order debe crecer estrictamente y conservar el orden original.")
        previous_order = order
        for field in ("original_type", "normalized_type"):
            if not isinstance(event.get(field), str) or not event[field].strip():
                raise SportsEventsValidationError(f"{prefix}.{field} es obligatorio y debe ser texto.")
        if event["normalized_type"] not in NORMALIZED_TYPES:
            raise SportsEventsValidationError(f"{prefix}.normalized_type no pertenece al contrato v1.")
        period = event.get("period")
        if "period" not in event or not (
                period is None or isinstance(period, str) and period in PERIODS):
            raise SportsEventsValidationError(f"{prefix}.period debe ser first, second, unknown o null.")
        if event["normalized_type"] in {"period_start", "period_end", "added_time"} \
                and period not in {"first", "second"}:
            raise SportsEventsValidationError(f"{prefix}: este tipo normalizado requiere un periodo conocido.")
        if "source_id" in event and event["source_id"] is not None \
                and (isinstance(event["source_id"], bool)
                     or not isinstance(event["source_id"], (str, int))):
            raise SportsEventsValidationError(f"{prefix}.source_id debe ser texto, entero o null.")
        if "game_clock_seconds" in event:
            seconds = event["game_clock_seconds"]
            if seconds is not None and (isinstance(seconds, bool) or not isinstance(seconds, int) or seconds < 0):
                raise SportsEventsValidationError(f"{prefix}.game_clock_seconds debe ser entero >= 0 o null.")
        if "source_minute" in event:
            minute = event["source_minute"]
            if minute is not None and (isinstance(minute, bool) or not isinstance(minute, (str, int))):
                raise SportsEventsValidationError(f"{prefix}.source_minute debe ser texto, entero o null.")
            normalized_clock = event.get("game_clock_seconds")
            parsed_source_minute = _source_minute_as_match_clock(event)
            if (minute is not None and normalized_clock is not None and parsed_source_minute is not None
                    and abs(normalized_clock - parsed_source_minute) >= 60):
                raise SportsEventsValidationError(
                    f"{prefix}.source_minute contradice game_clock_seconds en el eje del partido."
                )
        for field in ("source_period_name", "source_text"):
            if field in event and event[field] is not None and not isinstance(event[field], str):
                raise SportsEventsValidationError(f"{prefix}.{field} debe ser texto o null.")
        if "source_position" in event:
            position = event["source_position"]
            if position is not None and (isinstance(position, bool)
                                         or not isinstance(position, (str, int, float))):
                raise SportsEventsValidationError(f"{prefix}.source_position debe ser un valor opaco o null.")

    return document


def load_sports_events(path: Path) -> dict:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SportsEventsValidationError(f"No se pudo leer --events-json {path}: {exc}") from exc
    return validate_sports_events(document)


def sports_events_fingerprint(document: dict) -> str:
    canonical = json.dumps(document, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def event_match_clock(event: dict) -> int | None:
    """Return match-clock seconds only when the contract explicitly provides them.

    The raw provider minute and opaque source_position are never promoted to a
    video timestamp. A supplied normalized game_clock_seconds takes priority;
    otherwise supported football-minute notation is converted only to match
    clock seconds. An absent or unparseable minute has no temporal association.
    """
    value = event.get("game_clock_seconds")
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return _source_minute_as_match_clock(event)


def transition_event_evidence(candidate: dict, sports_events: dict | None,
                              *, clock_context: bool) -> dict:
    """Assess narrative support for an OCR transition without mapping it to video time."""
    if sports_events is None:
        return {"status": "not_provided", "matched_events": [], "conflicting_events": [],
                "period_order_consistent": False}

    events = sports_events["events"]
    first_ends = [event for event in events
                  if event["normalized_type"] == "period_end" and event["period"] == "first"]
    if not first_ends:
        return {"status": "insufficient", "reason": "no_first_period_end_event",
                "matched_events": [], "conflicting_events": [], "period_order_consistent": False}

    matched = []
    conflicting = []
    semantic = []
    before_clock = candidate["clock_before_seconds"]
    for event in first_ends:
        clock = event_match_clock(event)
        summary = {key: event.get(key) for key in
                   ("source_order", "source_id", "original_type", "normalized_type", "period",
                    "game_clock_seconds", "source_minute", "source_period_name") if key in event}
        if clock is None:
            semantic.append(summary)
            continue
        summary["normalized_game_clock_seconds"] = clock
        delta = abs(before_clock - clock)
        summary["ocr_before_delta_seconds"] = delta
        if delta <= EVENT_CLOCK_MATCH_TOLERANCE_SECONDS:
            matched.append(summary)
        elif delta > EVENT_CLOCK_CONFLICT_THRESHOLD_SECONDS:
            conflicting.append(summary)

    period_order_consistent = any(
        event["period"] == "second" and event["source_order"] > end["source_order"]
        for end in first_ends for event in events
    )
    if conflicting:
        status = "conflict"
    elif matched or (semantic and clock_context):
        status = "compatible"
        if semantic:
            matched.extend(semantic)
    else:
        status = "insufficient"
    return {
        "status": status,
        "matched_events": matched,
        "conflicting_events": conflicting,
        "period_order_consistent": period_order_consistent,
        "event_clock_match_tolerance_seconds": EVENT_CLOCK_MATCH_TOLERANCE_SECONDS,
        "event_clock_conflict_threshold_seconds": EVENT_CLOCK_CONFLICT_THRESHOLD_SECONDS,
        "reason": ("first_period_end_and_following_second_period_context"
                   if period_order_consistent else
                   "first_period_end_semantically_supports_ocr_candidate" if status == "compatible"
                   else "event_minute_conflicts_with_ocr_clock" if status == "conflict"
                   else "event_does_not_temporally_support_this_ocr_candidate"),
    }
