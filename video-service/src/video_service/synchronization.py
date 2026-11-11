"""Safe HTTP-facing classification around the unchanged V5.1 locator."""
from __future__ import annotations

from typing import Any, Literal

from video_sync.sync import event_minute_to_clock, locate as v51_locate

from .catalog import VideoRecord, public_video

Period = Literal["first", "second"]
EventKind = Literal["other", "match_start", "period_start"]
OUT_OF_COVERAGE_TEXT = "fuera de los tramos OCR verificados"
DEFAULT_LEAD_SECONDS = 0
MAX_EVENT_EDGE_EXTRAPOLATION_SECONDS = 20
MAX_PERIOD_START_EXTRAPOLATION_SECONDS = 90


def _period_is_classified(calibration: dict[str, Any], period: str) -> bool:
    periods = calibration.get("periods")
    if isinstance(periods, list) and periods:
        return any(isinstance(item, dict) and item.get("period") == period for item in periods)
    return any(
        isinstance(segment, dict)
        and segment.get("period") == period
        and segment.get("football_period", period) == period
        for segment in calibration.get("segments", [])
    )


def synchronization_summary(record: VideoRecord) -> dict[str, Any]:
    calibration = record.calibration
    if calibration is None:
        return {
            "video": public_video(record),
            "status": "uncertain",
            "reason": "calibration_not_available",
            "segments": [],
        }
    video = public_video(record)
    coverage = calibration.get("coverage") or {}
    dimensions = calibration.get("confidence_dimensions") or {}
    return {
        "video": video,
        "status": record.synchronization_status,
        "calibration_version": calibration.get("version"),
        "calibration_status": calibration.get("status"),
        "coverage": {
            "percent": coverage.get("percent"),
            "supported_references": coverage.get("supported_references"),
            "video_seconds": coverage.get("video_seconds"),
            "interpolable_seconds": coverage.get("interpolable_seconds"),
        },
        "confidence": {
            key: (dimensions.get(key) or {}).get("level")
            for key in ("ocr", "period", "location")
        },
        "segments": [
            {
                key: segment.get(key)
                for key in (
                    "segment_id", "temporal_segment_id", "period", "football_period",
                    "game_clock_start", "game_clock_end", "video_start", "video_end",
                    "offset_seconds", "reference_count", "max_residual_seconds",
                    "ocr_confidence", "period_confidence", "location_confidence",
                )
                if key in segment
            }
            for segment in calibration.get("segments", [])
            if isinstance(segment, dict)
        ],
        "uncertainty_zones": [
            {key: zone.get(key) for key in ("video_start", "video_end", "reason") if key in zone}
            for zone in calibration.get("uncertainty_zones", [])
            if isinstance(zone, dict)
        ],
        "sports_events_evidence": {
            key: (calibration.get("sports_events") or {}).get(key)
            for key in ("status", "contract", "version", "fingerprint", "event_count")
            if key in (calibration.get("sports_events") or {})
        },
    }


def _following_minute(minute: str, period: Period) -> str:
    """V5.1's minute parser lets us express a sub-minute clock via next-minute minus seconds."""
    text = minute.strip().replace("′", "").replace("'", "")
    import re
    match = re.fullmatch(r"(\d{1,3})(?:\+(\d{1,2}))?", text)
    if not match:
        raise ValueError("Minuto incorrecto. Ejemplos: 58, 45+2, 90+4")
    base, added = int(match.group(1)), match.group(2)
    if added is not None:
        return f"{base}+{int(added) + 1}"
    if period == "first" and base == 45:
        return "45+1"
    if period == "second" and base == 90:
        return "90+1"
    return str(base + 1)


def locate_video(record: VideoRecord, *, period: Period, minute: str | None,
                 lead_seconds: int = DEFAULT_LEAD_SECONDS,
                 event_kind: EventKind = "other",
                 event_second: int | None = None) -> dict[str, Any]:
    """Resolve a football event while keeping the V5.1 segment mapper unchanged.

    Domain outcomes are returned separately from malformed requests. Ambiguous,
    uncertain and uncovered outcomes deliberately contain no single playback
    position at the top level.
    """
    if lead_seconds < 0 or lead_seconds > 120:
        raise ValueError("El margen previo debe estar entre 0 y 120 segundos.")
    if event_second is not None and not 0 <= event_second <= 59:
        raise ValueError("El segundo del evento debe estar entre 0 y 59.")
    if event_kind not in {"other", "match_start", "period_start"}:
        raise ValueError("Tipo de evento no compatible con localización.")
    request = {"period": period, "minute": minute, "lead_seconds": lead_seconds,
               "event_kind": event_kind, "event_second": event_second}
    calibration = record.calibration
    base = {"video_id": record.video_id, "match_ref": public_video(record)["match_ref"], "request": request}
    if calibration is None or record.synchronization_status != "calibrated" \
            or calibration.get("status") != "calibrated":
        return {**base, "status": "uncertain", "reason": "calibration_not_confirmed"}
    period_confidence = ((calibration.get("confidence_dimensions") or {}).get("period") or {}).get("level")
    if period_confidence in {"low", "uncertain"} or not _period_is_classified(calibration, period):
        return {**base, "status": "uncertain", "reason": "football_period_not_classified"}

    if event_kind in {"match_start", "period_start"}:
        if (event_kind == "match_start" and period != "first") or (event_kind == "period_start" and period != "second"):
            return {**base, "status": "uncertain", "reason": "event_period_conflict"}
        start_minute = "1" if period == "first" else "46"
        event_clock = 0 if period == "first" else 45 * 60
        lookup_minute, lookup_lead = start_minute, 0
        max_extrapolate = MAX_PERIOD_START_EXTRAPOLATION_SECONDS
        method = "boundary_extrapolation"
        event_estimate_clock = event_clock
        warnings = ["Inicio de periodo estimado desde las primeras referencias OCR fiables."]
    else:
        if minute is None:
            raise ValueError("Este evento necesita un minuto futbolístico.")
        event_clock = event_minute_to_clock(minute, period)
        event_offset = event_second if event_second is not None else 40
        event_estimate_clock = event_clock + event_offset
        lookup_minute = _following_minute(minute, period)
        # Asking V5.1 for the next minute minus (60 - event offset + lead)
        # places playback at event-clock + offset - lead without changing OCR offsets.
        lookup_lead = 60 - event_offset + lead_seconds
        max_extrapolate = MAX_EVENT_EDGE_EXTRAPOLATION_SECONDS
        method = "event_second_interpolation" if event_second is not None else "minute_tail"
        warnings = (["El segundo del evento se aportó como dato; su posición en el vídeo sigue siendo una interpolación OCR."]
                    if event_second is not None else [
                        "SofaScore solo aporta el minuto; se usa el segundo 40 como punto de reproducción."
                    ])
    try:
        result = v51_locate(
            calibration.get("segments", []),
            period=period,
            minute=lookup_minute,
            lead_seconds=lookup_lead,
            max_extrapolate_seconds=max_extrapolate,
        )
    except ValueError as exc:
        if OUT_OF_COVERAGE_TEXT in str(exc):
            return {
                **base,
                "status": "out_of_coverage",
                "reason": "event_outside_verified_ocr_segments_or_safe_extrapolation",
            }
        raise

    if result.get("status") == "ambiguous":
        return {
            **base,
            "status": "ambiguous",
            "candidates": result.get("candidates", []),
            "note": warnings[0] if warnings else "Segundo de evento interpolado entre referencias OCR.",
        }
    segment = next((item for item in calibration.get("segments", [])
                    if item.get("segment_id") == result.get("segment_id")), None)
    if segment is None:
        return {**base, "status": "uncertain", "reason": "segment_reference_missing"}
    if (int(segment.get("reference_count", 0)) < 2
            or float(segment.get("max_residual_seconds", 999)) > 5
            or segment.get("ocr_confidence") != "high"
            or segment.get("period_confidence") != "high"
            or segment.get("location_confidence") != "high"):
        return {**base, "status": "uncertain", "reason": "segment_evidence_insufficient"}
    extrapolated = float(result.get("extrapolated_seconds") or 0)
    if extrapolated:
        reference_edge = (float(segment["game_clock_start"]) if result["playback_game_clock_second"] < float(segment["game_clock_start"])
                         else float(segment["game_clock_end"]))
        playback_clock = float(result["playback_game_clock_second"])
        offset = float(segment["offset_seconds"])
        video_target = float(result["video_second"])
        video_edge = reference_edge + offset
        interval_start, interval_end = sorted((video_target, video_edge))
        crossed = any(
            isinstance(zone, dict)
            and any(word in str(zone.get("reason", "")).casefold() for word in ("cut", "discontinu", "offset_change"))
            and float(zone.get("video_start", float("inf"))) <= interval_end
            and float(zone.get("video_end", float("-inf"))) >= interval_start
            for zone in calibration.get("uncertainty_zones", [])
        )
        if (int(segment.get("reference_count", 0)) < 3
                or float(segment.get("max_residual_seconds", 999)) > 5
                or segment.get("ocr_confidence") != "high"
                or segment.get("period_confidence") != "high"
                or segment.get("location_confidence") != "high"
                or crossed
                or abs(reference_edge - playback_clock) > max_extrapolate):
            return {**base, "status": "uncertain", "reason": "edge_extrapolation_evidence_insufficient"}
    if event_kind in {"match_start", "period_start"}:
        if (int(segment.get("reference_count", 0)) < 3
                or float(segment.get("max_residual_seconds", 999)) > 5
                or segment.get("ocr_confidence") != "high"
                or segment.get("period_confidence") != "high"
                or segment.get("location_confidence") != "high"
                or extrapolated > MAX_PERIOD_START_EXTRAPOLATION_SECONDS
                or float(segment.get("game_clock_start", event_clock)) < event_clock):
            return {**base, "status": "uncertain", "reason": "period_start_evidence_insufficient"}
    if extrapolated:
        warnings.append(f"La posición de reproducción se extrapola {extrapolated:g} s desde la referencia OCR más próxima.")
    playback_second = float(result["video_second"])
    return {
        **base,
        "status": "resolved",
        "result": {
            "video_second": playback_second,
            "playback_video_second": playback_second,
            "event_video_second": round(playback_second + (lead_seconds if event_kind == "other" else 0), 3),
            "event_clock_second_start": event_clock,
            "event_clock_second_estimate": event_estimate_clock,
            "playback_game_clock_second": result.get("playback_game_clock_second"),
            "lead_seconds": 0 if event_kind in {"match_start", "period_start"} else lead_seconds,
            "estimated": True,
            "method": method,
            "warnings": warnings,
            "segment_id": result.get("segment_id"),
            "temporal_segment_id": result.get("temporal_segment_id"),
            "football_period": result.get("football_period"),
            "extrapolated_seconds": result.get("extrapolated_seconds"),
            "reference_count": result.get("reference_count"),
            "max_residual_seconds": result.get("max_residual_seconds"),
            "confidence": {
                "ocr": result.get("ocr_confidence"),
                "period": result.get("period_confidence"),
                "location": result.get("location_confidence"),
            },
            "note": ("Se usa el segundo 40 del minuto como punto de reproducción; el segundo exacto de la acción no está disponible."
                     if event_kind == "other" and event_second is None else result.get("note")),
        },
    }
