"""Piecewise clock<->video calibration and safe event lookup."""
from __future__ import annotations

from dataclasses import dataclass
from statistics import median
import re


@dataclass(frozen=True)
class Observation:
    period: str
    video_second: float
    game_clock_second: int
    votes: int
    status: str = "valid_reading"

    @property
    def offset(self) -> float:
        return self.video_second - self.game_clock_second


def pick_second_mode(raw_clock_seconds: list[int]) -> str:
    """Heuristic only. Explicit --second-mode is preferable if ambiguous."""
    if not raw_clock_seconds:
        raise ValueError("No hay lecturas válidas para decidir el modo de segunda parte.")
    central = median(raw_clock_seconds)
    # Continuous TV clock at the second half should display ~45+ minutes;
    # a reset TV clock should display ~0-45 minutes.
    if central > 45 * 60:
        return "continuous"
    return "reset"


def to_game_clock(raw_clock_seconds: int, period: str, second_mode: str) -> int:
    if period == "first":
        return raw_clock_seconds
    if period == "second" and second_mode == "reset":
        return raw_clock_seconds + 45 * 60
    if period == "second" and second_mode == "continuous":
        return raw_clock_seconds
    raise ValueError(f"Periodo/modo inválido: {period}/{second_mode}")


def observations_from_scans(scans: list[dict], *, second_mode: str,
                            tolerance_seconds: float, max_gap_seconds: float) -> list[Observation]:
    """Keep independently valid OCR, then annotate temporal compatibility.

    A missing marker is skipped without resetting continuity. A clock jump is
    retained as a possible new segment, but explicitly marked suspicious; the
    segment builder will require multiple compatible references to support it.
    """
    observations = []
    previous: dict[str, Observation] = {}
    for scan in sorted(scans, key=lambda item: item["video_second"]):
        scan.setdefault("ocr_status", scan.get("status", "ambiguous_invalid"))
        if scan["ocr_status"] != "valid_reading" or scan.get("period") not in {"first", "second"}:
            continue

        raw_clock = scan.get("clock_seconds")
        if raw_clock is None:
            continue
        period = scan["period"]
        if period == "first":
            if raw_clock >= 60 * 60:
                scan["status"] = "ambiguous_invalid"
                scan["status_reason"] = "clock_outside_first_period_range"
                continue
            game_clock = raw_clock
        else:
            if second_mode == "auto":
                scan["status"] = "ambiguous_invalid"
                scan["status_reason"] = "second_clock_mode_unresolved"
                continue
            if second_mode == "continuous" and raw_clock < 43 * 60:
                scan["status"] = "ambiguous_invalid"
                scan["status_reason"] = "clock_outside_continuous_second_period_range"
                continue
            if second_mode == "reset" and raw_clock > 70 * 60:
                scan["status"] = "ambiguous_invalid"
                scan["status_reason"] = "clock_outside_reset_second_period_range"
                continue
            game_clock = to_game_clock(raw_clock, "second", second_mode)

        observation = Observation(period, float(scan["video_second"]), int(game_clock),
                                  int(scan.get("votes", 0)), "valid_reading")
        prior = previous.get(period)
        if prior is None:
            scan["status"] = "valid_reading"
            scan["temporal_check"] = {"result": "first_reference"}
        else:
            video_gap = observation.video_second - prior.video_second
            clock_gap = observation.game_clock_second - prior.game_clock_second
            offset_change = observation.offset - prior.offset
            check = {
                "result": "compatible" if video_gap <= max_gap_seconds else "gap_too_large_to_compare",
                "previous_video_second": round(prior.video_second, 3),
                "video_gap_seconds": round(video_gap, 3),
                "game_clock_gap_seconds": clock_gap,
                "offset_change_seconds": round(offset_change, 3),
                "tolerance_seconds": tolerance_seconds,
            }
            if video_gap <= max_gap_seconds and (clock_gap < 0 or abs(offset_change) > tolerance_seconds):
                scan["status"] = "suspicious_discontinuity"
                scan["status_reason"] = ("game_clock_moved_backwards" if clock_gap < 0
                                          else "offset_changed_beyond_tolerance")
                check["result"] = "suspicious_discontinuity"
            else:
                scan["status"] = "valid_reading"
                if video_gap > max_gap_seconds:
                    check["reason"] = "sparse_references_are_not_treated_as_continuous"
            scan["temporal_check"] = check

        observation = Observation(observation.period, observation.video_second,
                                  observation.game_clock_second, observation.votes,
                                  scan["status"])
        observations.append(observation)
        previous[period] = observation
    return observations


def build_segments(observations: list[Observation], *, tolerance_seconds: float = 4.0,
                   max_gap_seconds: float = 420.0, min_points: int = 2) -> list[dict]:
    """Group similar offsets, then split distant observations into distinct spans.

    Points that don't belong to a supported cluster are NOT deemed calibrated.
    A missing sample never establishes that the interval between two points is safe.
    """
    segments = []
    for period in ("first", "second"):
        points = sorted((o for o in observations if o.period == period), key=lambda o: o.offset)
        clusters: list[list[Observation]] = []
        for point in points:
            # Compare with each cluster median; avoid treating a large clock jump
            # as valid just because it happens at a later part of the video.
            closest = min(clusters, key=lambda cluster: abs(point.offset - median(o.offset for o in cluster)), default=None)
            if closest is not None and abs(point.offset - median(o.offset for o in closest)) <= tolerance_seconds:
                closest.append(point)
            else:
                clusters.append([point])

        for cluster in clusters:
            chronological = sorted(cluster, key=lambda o: o.video_second)
            spans: list[list[Observation]] = []
            for obs in chronological:
                if not spans or obs.video_second - spans[-1][-1].video_second > max_gap_seconds:
                    spans.append([obs])
                else:
                    spans[-1].append(obs)
            for span in spans:
                if len(span) < min_points:
                    continue
                offset = float(median(o.offset for o in span))
                start = min(o.game_clock_second for o in span)
                end = max(o.game_clock_second for o in span)
                if start >= end:
                    continue
                residuals = [abs(o.offset-offset) for o in span]
                segments.append({
                    "period": period, "game_clock_start": start, "game_clock_end": end,
                    "video_start": round(start + offset, 3), "video_end": round(end + offset, 3),
                    "offset_seconds": round(offset, 3), "reference_count": len(span),
                    "max_residual_seconds": round(max(residuals), 3),
                    "reference_points": [
                        {"video_second": round(o.video_second, 3),
                         "game_clock_second": o.game_clock_second, "votes": o.votes,
                         "status": o.status}
                        for o in span
                    ],
                })
    return sorted(segments, key=lambda s: (s["period"], s["video_start"]))


def event_minute_to_clock(minute: str, period: str) -> int:
    """Football 58' is [57:00, 57:59]; 45+2' is [46:00, 46:59]."""
    text = minute.strip().replace("′", "").replace("'", "")
    match = re.fullmatch(r"(\d{1,3})(?:\+(\d{1,2}))?", text)
    if not match:
        raise ValueError("Minuto incorrecto. Ejemplos: 58, 45+2, 90+4")
    base = int(match.group(1))
    added = int(match.group(2)) if match.group(2) else None
    if added is not None:
        if added < 1 or (period == "first" and base != 45) or (period == "second" and base != 90):
            raise ValueError("El descuento esperado es 45+N (primera) o 90+N (segunda).")
        return (base + added - 1) * 60
    if base < 1 or (period == "first" and base > 45) or (period == "second" and base < 46):
        raise ValueError("Minuto incompatible con la parte seleccionada.")
    return (base - 1) * 60


def locate(segments: list[dict], *, period: str, minute: str,
           lead_seconds: int = 15, max_extrapolate_seconds: float = 0) -> dict:
    if lead_seconds < 0 or max_extrapolate_seconds < 0:
        raise ValueError("Los márgenes deben ser >= 0")
    event_clock = event_minute_to_clock(minute, period)
    # Subtract margin from desired playback clock, not from real-world UTC time.
    target_clock = max(0, event_clock - lead_seconds)
    matching = []
    possible_uncertain = []
    for segment in segments:
        if segment["period"] == "uncertain":
            gap = max(segment["game_clock_start"] - target_clock,
                      target_clock - segment["game_clock_end"], 0)
            if gap == 0:
                possible_uncertain.append((gap, segment))
            continue
        if segment["period"] != period:
            continue
        gap = max(segment["game_clock_start"]-target_clock,
                  target_clock-segment["game_clock_end"], 0)
        if gap <= max_extrapolate_seconds:
            matching.append((gap, segment))
    if not matching:
        raise ValueError(
            "El minuto solicitado está fuera de los tramos OCR verificados. "
            "Amplía la ventana de calibración o usa --max-extrapolate con prudencia."
        )
    matching.extend(possible_uncertain)
    exact = [item for item in matching if item[0] == 0]
    if len(exact) > 1 or (not exact and len(matching) > 1):
        candidates = []
        for gap, segment in sorted(matching,
                                   key=lambda item: target_clock + item[1]["offset_seconds"]):
            candidates.append({
                "segment_id": segment.get("segment_id"),
                "temporal_segment_id": segment.get("temporal_segment_id", segment.get("segment_id")),
                "football_period": segment.get("football_period", segment["period"]),
                "video_second": round(target_clock + segment["offset_seconds"], 3),
                "extrapolated_seconds": round(gap, 3),
                "reference_count": segment["reference_count"],
                "ocr_confidence": segment.get("ocr_confidence", "not_recorded"),
                "period_confidence": segment.get("period_confidence", "not_recorded"),
                "location_confidence": segment.get(
                    "location_confidence", segment.get("confidence_level", "not_recorded")),
            })
        return {
            "status": "ambiguous", "period": period, "event_minute": minute,
            "event_clock_second_start": event_clock,
            "playback_game_clock_second": target_clock,
            "candidates": candidates,
            "note": "Varios segmentos verifican el mismo minuto; hace falta elegir manualmente el directo.",
        }
    gap, segment = exact[0] if exact else matching[0]
    video_at = target_clock + segment["offset_seconds"]
    if video_at < 0:
        raise ValueError("El cálculo genera un tiempo de vídeo negativo.")
    return {
        "status": "located",
        "period": period, "event_minute": minute, "event_clock_second_start": event_clock,
        "playback_game_clock_second": target_clock,
        "video_second": round(video_at, 3), "extrapolated_seconds": round(gap, 3),
        "segment_id": segment.get("segment_id"),
        "temporal_segment_id": segment.get("temporal_segment_id", segment.get("segment_id")),
        "football_period": segment.get("football_period", segment["period"]),
        "ocr_confidence": segment.get("ocr_confidence", "not_recorded"),
        "period_confidence": segment.get("period_confidence", "not_recorded"),
        "location_confidence": segment.get(
            "location_confidence", segment.get("confidence_level", "not_recorded")),
        "reference_count": segment["reference_count"],
        "max_residual_seconds": segment["max_residual_seconds"],
        "note": "Se localiza el comienzo aproximado del minuto del evento, no el segundo exacto de la acción.",
    }
