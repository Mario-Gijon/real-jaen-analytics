"""Autonomous, sparse video clock survey with bounded adaptive refinement."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
import time

import cv2
import numpy as np

from .clock import ROI, clock_text, read_clock_crop
from .detector import ClockDetector
from .media import extract_frame_with_timestamp, probe
from .sports_events import (EVENT_CLOCK_CONFLICT_THRESHOLD_SECONDS,
                            EVENT_CLOCK_MATCH_TOLERANCE_SECONDS,
                            sports_events_fingerprint, transition_event_evidence,
                            validate_sports_events)
from .sync import Observation, build_segments


def video_report_paths(video_path: Path, output_root: Path = Path("output"),
                       sports_events: dict | None = None) -> tuple[Path, Path, str]:
    """Return stable paths per video and, when supplied, per event document."""
    resolved = video_path.expanduser().resolve()
    slug = re.sub(r"[^a-z0-9]+", "-", resolved.stem.lower()).strip("-")[:48] or "video"
    route_hash = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:12]
    video_id = f"{slug}-{route_hash}"
    folder = output_root / "auto_calibration" / video_id / "v5"
    if sports_events is not None:
        event_fingerprint = sports_events_fingerprint(sports_events)
        folder = folder / f"events-{event_fingerprint}"
    return folder / "auto_calibration.json", folder, video_id


def infer_periods(scans: list[dict], *, sample_interval: float,
                  tolerance_seconds: float = 8.0,
                  sports_events: dict | None = None) -> tuple[list[dict], str | None]:
    """Find supported period changes; long mid-half pauses alone are insufficient."""
    valid = sorted((row for row in scans if row.get("ocr_status", row.get("status")) == "valid_reading"
                    and row.get("clock_seconds") is not None), key=lambda row: row["video_second"])
    possible = []
    for index, (before, after) in enumerate(zip(valid, valid[1:]), start=1):
        video_gap = after["video_second"] - before["video_second"]
        clock_delta = after["clock_seconds"] - before["clock_seconds"]
        offset_delta = video_gap - clock_delta
        reset = (before["clock_seconds"] >= 120 and
                 after["clock_seconds"] < before["clock_seconds"] * 0.5)
        paused = (not reset and video_gap >= max(2 * sample_interval, 300.0)
                  and video_gap - max(0.0, clock_delta) >= max(sample_interval, 180.0)
                  and abs(offset_delta) > tolerance_seconds)
        if not (reset or paused):
            continue

        after_rows = valid[index:]
        post_offsets = [row["video_second"] - row["clock_seconds"] for row in after_rows[:4]]
        post_progresses = all(b["clock_seconds"] >= a["clock_seconds"]
                              for a, b in zip(after_rows, after_rows[1:4]))
        post_supported = (len(post_offsets) >= 2 and post_progresses and
                          max(post_offsets) - min(post_offsets) <= max(tolerance_seconds * 2, 24.0))
        before_rows = valid[max(0, index - 3):index]
        pre_offsets = [row["video_second"] - row["clock_seconds"] for row in before_rows]
        pre_supported = len(pre_offsets) >= 2 and max(pre_offsets) - min(pre_offsets) <= max(
            tolerance_seconds * 2, 24.0)
        before_clock = before["clock_seconds"]
        after_clock = after["clock_seconds"]
        reset_style = reset
        restart_base_style = (not reset and after_clock < before_clock
                              and after_clock >= 40 * 60
                              and after_clock >= 0.8 * before_clock)
        continuous_style = (not reset and after_clock >= before_clock
                            and before_clock >= 40 * 60
                            and after_clock >= 45 * 60
                            and after_clock - before_clock <= 5 * 60)
        clock_context_reason = ("clock_reset_from_late_first_period"
                                if reset_style and before_clock >= 40 * 60 else
                                "restart_near_match_minute_base" if restart_base_style else
                                "continuous_clock_past_period_boundary" if continuous_style else
                                "clock_does_not_support_period_change")
        candidate = {
            "from_video_second": round(before["video_second"], 3),
            "to_video_second": round(after["video_second"], 3),
            "raw_clock_before": before["clock"], "raw_clock_after": after["clock"],
            "clock_before_seconds": before["clock_seconds"],
            "clock_after_seconds": after["clock_seconds"],
            "video_gap_seconds": round(video_gap, 3),
            "clock_gap_seconds": clock_delta,
            "offset_change_seconds": round(offset_delta, 3),
            "kind": "clock_reset" if reset else "long_clock_pause_and_offset_change",
            "pre_segment_supported": pre_supported,
            "post_segment_supported": post_supported,
            "temporal_change_supported": bool(post_supported and (pre_supported or reset)),
            "clock_context_compatible": bool(
                before_clock >= 40 * 60 and
                (reset_style or restart_base_style or continuous_style)),
            "clock_context_reason": clock_context_reason,
        }
        candidate["sports_event_evidence"] = transition_event_evidence(
            candidate, sports_events, clock_context=candidate["clock_context_compatible"])
        possible.append(candidate)

    eligible = [event for event in possible
                if event["temporal_change_supported"]
                and event["sports_event_evidence"]["status"] != "conflict"
                and (event["clock_context_compatible"]
                     or event["sports_event_evidence"]["status"] == "compatible")]
    # A minute-bearing event can disambiguate multiple OCR candidates only when
    # its normalized match-clock reading is close to exactly one candidate.
    if len(eligible) > 1:
        event_matched = [event for event in eligible if any(
            "ocr_before_delta_seconds" in match
            for match in event["sports_event_evidence"].get("matched_events", []))]
        if len(event_matched) == 1:
            eligible = event_matched

    chosen = eligible[0] if len(eligible) == 1 else None
    mode = None
    if chosen:
        before_clock = chosen["clock_before_seconds"]
        after_clock = chosen["clock_after_seconds"]
        if chosen["kind"] == "clock_reset":
            mode = "reset"
        elif after_clock >= before_clock:
            mode = "continuous"
        elif after_clock >= 0.8 * before_clock:
            # Some broadcasts return to the match-minute base after stoppage
            # time. This differs from a second-period clock reset to 00:00.
            mode = "restart_at_match_minute_base"
        sports_status = chosen["sports_event_evidence"]["status"]
        event_order_support = chosen["sports_event_evidence"].get("period_order_consistent", False)
        video_confidence = "high" if mode == "reset" else "medium" if mode else "low"
        period_confidence = ("high" if sports_status == "compatible" and event_order_support
                             else "medium" if mode else "low")
        chosen.update({
            "period_boundary": "second" if mode else "uncertain",
            "clock_mode": mode or "uncertain",
            "classification": "second" if mode else "uncertain",
            "confirmed": True,
            "confidence": period_confidence,
            "period_confidence": period_confidence,
            "ocr_transition_confidence": video_confidence,
            "evidence_sources": (["video_ocr"] + (["sports_events"] if sports_status == "compatible" else [])),
            "uncertain_video_interval": [chosen["from_video_second"], chosen["to_video_second"]],
        })

    events = []
    for item in possible:
        if item is chosen:
            events.append(item)
        else:
            if item["sports_event_evidence"]["status"] == "conflict":
                item["classification"] = "uncertain"
                item["period_boundary"] = "uncertain"
                item["confidence"] = "low"
                item["reason"] = "sports_event_clock_conflicts_with_ocr"
            elif len(eligible) > 1 and item in eligible:
                item["classification"] = "uncertain"
                item["period_boundary"] = "uncertain"
                item["confidence"] = "low"
                item["reason"] = "multiple_ocr_candidates_cannot_be_disambiguated"
            else:
                item["classification"] = "uncertain"
                item["period_boundary"] = "uncertain"
                item["confidence"] = "unconfirmed"
                item["reason"] = ("period_transition_clock_context_not_supported"
                                   if item["temporal_change_supported"] else
                                   "no_sustained_temporal_evidence_after_candidate_boundary")
            item["confirmed"] = False
            item["clock_mode"] = "uncertain"
            item["period_confidence"] = "uncertain"
            item["ocr_transition_confidence"] = (
                "medium" if item["temporal_change_supported"] else "low")
            item["evidence_sources"] = (["video_ocr"] if item["temporal_change_supported"] else [])
            events.append(item)
    return events, mode


def prepare_observations(scans: list[dict], *, sample_interval: float,
                         tolerance_seconds: float, max_gap_seconds: float,
                         sports_events: dict | None = None) -> tuple[list[dict], list[dict], str | None]:
    """Infer football periods separately from temporal segment IDs."""
    events, second_mode = infer_periods(scans, sample_interval=sample_interval,
                                        tolerance_seconds=tolerance_seconds,
                                        sports_events=sports_events)
    boundary_event = next((event for event in events if event.get("confirmed")), None)
    boundary = boundary_event["to_video_second"] if boundary_event else None
    period_confidence = (boundary_event.get("period_confidence", "uncertain")
                         if boundary_event else "uncertain")
    observations = []
    row_periods = {}
    for row in scans:
        votes = int(row.get("votes", 0))
        if row.get("ocr_status", row.get("status")) == "valid_reading":
            row["ocr_confidence"] = ("high" if votes >= 3 else "medium" if votes == 2
                                     else "low" if votes == 1 else "uncertain")
        elif row.get("ocr_status", row.get("status")) == "marker_not_visible":
            row["ocr_confidence"] = "not_applicable"
        else:
            row["ocr_confidence"] = "uncertain"
    valid_scans = sorted((row for row in scans if row.get("ocr_status", row.get("status")) == "valid_reading"
                          and row.get("clock_seconds") is not None), key=lambda row: row["video_second"])
    for row in valid_scans:
        raw = int(row["clock_seconds"])
        votes = int(row.get("votes", 0))
        if boundary is None:
            period = "uncertain"
        elif row["video_second"] < boundary:
            period = "first"
        elif second_mode is None:
            period = "uncertain"
        else:
            period = "second"
        game_clock = raw + 45 * 60 if period == "second" and second_mode == "reset" else raw
        row["period"] = period
        row["football_period"] = period
        row["period_confidence"] = period_confidence if period != "uncertain" else "uncertain"
        row["game_clock_seconds"] = game_clock
        row["offset_seconds"] = round(row["video_second"] - game_clock, 3)
        # V2's builder accepts first/second buckets. Temporarily group uncertain
        # readings by offset, then restore their explicit uncertainty below.
        builder_period = period if period in {"first", "second"} else "first"
        row_periods[float(row["video_second"])] = period
        observations.append(Observation(builder_period, float(row["video_second"]), game_clock,
                                        int(row.get("votes", 0))))

    segments = build_segments(observations, tolerance_seconds=tolerance_seconds,
                              max_gap_seconds=max_gap_seconds, min_points=2)
    segments.sort(key=lambda segment: segment["video_start"])
    for segment in segments:
        point_periods = {row_periods.get(float(point["video_second"]), "uncertain")
                         for point in segment["reference_points"]}
        football_period = next(iter(point_periods)) if len(point_periods) == 1 else "uncertain"
        segment["period"] = football_period
        segment["football_period"] = football_period
        segment["period_confidence"] = (period_confidence if football_period in {"first", "second"}
                                         else "uncertain")
    supported_times = {point["video_second"] for segment in segments
                       for point in segment["reference_points"]}
    by_period = {period: [row for row in valid_scans if row.get("period") == period]
                 for period in ("first", "second", "uncertain")}
    for rows in by_period.values():
        for row in rows:
            if row["video_second"] not in supported_times:
                row["status"] = "suspicious_discontinuity"
                row["status_reason"] = "reading_has_no_second_compatible_reference_in_segment"

    for index, segment in enumerate(segments, start=1):
        refs = segment["reference_points"]
        gaps = [b["video_second"] - a["video_second"] for a, b in zip(refs, refs[1:])]
        score = min(1.0, 0.58 + 0.08 * max(0, len(refs) - 2)
                    + 0.12 * (1 - min(1.0, segment["max_residual_seconds"] /
                                      max(tolerance_seconds, 1e-6)))
                    + 0.08 * min(1.0, 3 / max(gaps, default=3.0)))
        segment["segment_id"] = f"S{index:02d}"
        segment["temporal_segment_id"] = segment["segment_id"]
        segment["max_reference_gap_seconds"] = round(max(gaps, default=0.0), 3)
        segment["confidence"] = round(score, 3)
        segment["confidence_level"] = ("high" if len(refs) >= 4 and
                                        segment["max_residual_seconds"] <= tolerance_seconds
                                        else "medium")
        ref_rows = [row for row in valid_scans
                    if any(abs(float(row["video_second"]) - float(point["video_second"])) < 1e-6
                           for point in refs)]
        high_ocr_ratio = (sum(row.get("ocr_confidence") == "high" for row in ref_rows) /
                          max(1, len(ref_rows)))
        segment["ocr_confidence"] = ("high" if high_ocr_ratio >= 0.8 else "medium"
                                     if high_ocr_ratio >= 0.5 else "low")
        segment["location_confidence"] = segment["confidence_level"]
        segment["interpolation_policy"] = "compatible_references_only_within_this_segment"
    return segments, events, second_mode


def uncertainty_zones(duration: float, segments: list[dict], events: list[dict]) -> list[dict]:
    """Return every uncalibrated interval; never bridge segment gaps."""
    spans = sorted((max(0.0, float(s["video_start"])),
                    min(duration, float(s["video_end"])), s["segment_id"])
                   for s in segments if s["video_end"] >= 0 and s["video_start"] <= duration)
    zones = []
    cursor = 0.0
    for start, end, _ in spans:
        if start > cursor:
            zones.append({"video_start": round(cursor, 3), "video_end": round(start, 3),
                          "reason": "outside_supported_reference_segment"})
        cursor = max(cursor, end)
    if cursor < duration:
        zones.append({"video_start": round(cursor, 3), "video_end": round(duration, 3),
                      "reason": "outside_supported_reference_segment"})
    for event in events:
        if event.get("confirmed"):
            start, end = event["uncertain_video_interval"]
            overlapping = max((zone for zone in zones
                               if min(end, zone["video_end"]) > max(start, zone["video_start"])),
                              key=lambda zone: min(end, zone["video_end"]) -
                              max(start, zone["video_start"]), default=None)
            overlap = (min(end, overlapping["video_end"]) - max(start, overlapping["video_start"])) \
                if overlapping else 0.0
            if overlapping and overlap >= 0.8 * max(1.0, end - start):
                overlapping["reason"] = "unsupported_gap_with_bracketed_period_boundary"
                overlapping["period_boundary_interval"] = [start, end]
            else:
                zones.append({"video_start": start, "video_end": end,
                              "reason": "period_boundary_location_is_bracketed_not_exact"})
    for segment in segments:
        if segment.get("period") == "uncertain":
            zones.append({"video_start": segment["video_start"], "video_end": segment["video_end"],
                          "reason": "offset_supported_but_football_period_unclassified",
                          "segment_id": segment["segment_id"]})
    return sorted(zones, key=lambda zone: (zone["video_start"], zone["video_end"]))


def refinement_gaps(scans: list[dict], *, min_gap_seconds: float,
                    tolerance_seconds: float, max_gap_seconds: float) -> list[dict]:
    ordered = sorted(scans, key=lambda row: row["video_second"])
    gaps = []
    for before, after in zip(ordered, ordered[1:]):
        start, end = float(before["video_second"]), float(after["video_second"])
        width = end - start
        if width <= min_gap_seconds:
            continue
        before_valid = before.get("ocr_status", before.get("status")) == "valid_reading"
        after_valid = after.get("ocr_status", after.get("status")) == "valid_reading"
        reason = None
        if before_valid != after_valid:
            reason = "clock_visibility_transition"
        elif before_valid and after_valid:
            offset_before = start - before["clock_seconds"]
            offset_after = end - after["clock_seconds"]
            clock_delta = after["clock_seconds"] - before["clock_seconds"]
            if clock_delta < 0 or abs(offset_after - offset_before) > tolerance_seconds:
                reason = "possible_clock_or_offset_discontinuity"
            elif width > max_gap_seconds:
                reason = "reference_gap_exceeds_interpolation_limit"
        if reason:
            gaps.append({"start": start, "end": end, "midpoint": round((start + end) / 2, 3),
                         "width_seconds": width, "reason": reason})
    return sorted(gaps, key=lambda gap: (-gap["width_seconds"], gap["start"]))


def _ocr_status(reading: dict) -> str:
    if reading["status"] == "valid_reading":
        return "valid_reading"
    texts = [variant.get("text", "") for variant in reading.get("variants", [])]
    any_numeric_signal = any(any(character.isdigit() or character == ":" for character in text)
                             for text in texts)
    return "ambiguous_invalid" if any_numeric_signal else "marker_not_visible"


def _normalized_center_distance(first: ROI, second: ROI, width: int, height: int) -> float:
    first_center = ((first.x + first.width / 2) / width, (first.y + first.height / 2) / height)
    second_center = ((second.x + second.width / 2) / width, (second.y + second.height / 2) / height)
    return math.hypot(first_center[0] - second_center[0], first_center[1] - second_center[1])


def _read_record(frame: np.ndarray, video_second: float, requested: float,
                 timestamp: float | None, timestamp_verified: bool, roi: ROI,
                 sample_id: str, roi_epoch: int) -> dict:
    reading = read_clock_crop(roi.crop(frame), min_votes=2, require_marker_evidence=False)
    status = _ocr_status(reading)
    return {
        "sample_id": sample_id,
        "requested_video_second": round(requested, 3),
        "video_second": round(video_second, 3),
        "actual_pts_seconds": round(timestamp, 6) if timestamp is not None else None,
        "timestamp_source": "ffmpeg_showinfo_pts" if timestamp is not None else "requested_seek_fallback",
        "timestamp_error_seconds": round(timestamp - requested, 6) if timestamp is not None else None,
        "timestamp_verified": bool(timestamp_verified),
        "clock_seconds": reading["clock_seconds"], "clock": reading["clock"],
        "votes": reading["votes"], "candidate_clock_seconds": reading["candidate_clock_seconds"],
        "status": status, "ocr_status": status,
        "status_reason": reading.get("status_reason") or ("no_clock_text_in_detected_region"
                                                              if status == "marker_not_visible" else None),
        "variants": reading["variants"],
        "marker_evidence": reading.get("marker_evidence"),
        "roi_epoch": roi_epoch,
    }


def _apply_detector_readings(records: list[dict], result: dict) -> None:
    tracks = result.get("candidate_tracks", [])
    selected = next((track for track in tracks if track.get("decision") == "selected"), None)
    observations = {entry["sample_index"]: entry for entry in selected.get("observations", [])} if selected else {}
    for index, record in enumerate(records):
        evidence = result.get("sample_evidence", [])
        sample_evidence = evidence[index] if index < len(evidence) else {"status": "ambiguous_invalid"}
        observation = observations.get(index)
        if observation:
            record.update({"clock_seconds": observation["clock_seconds"], "clock": observation["clock"],
                           "votes": observation.get("votes", 0), "candidate_clock_seconds": observation["clock_seconds"],
                           "status": "valid_reading", "ocr_status": "valid_reading",
                           "status_reason": "confirmed_by_clock_detector", "variants": observation.get("variants", [])})
        else:
            status = sample_evidence.get("status", "ambiguous_invalid")
            if status == "candidate_unconfirmed":
                status = "ambiguous_invalid"
            record.update({"clock_seconds": None, "clock": None, "votes": 0,
                           "candidate_clock_seconds": None, "status": status, "ocr_status": status,
                           "status_reason": sample_evidence.get("reason"), "variants": []})


def _sample_plan(duration: float, initial_count: int, interval: float,
                 max_samples: int) -> tuple[list[float], list[float]]:
    initial = [round(duration * (index + 1) / (initial_count + 1), 3)
               for index in range(initial_count)]
    coarse = [round(float(second), 3) for second in np.arange(0, duration, interval)]
    planned = sorted(set(initial + coarse))
    if len(planned) > max_samples:
        initial_set = set(initial)
        remaining = [point for point in planned if point not in initial_set]
        slots = max(0, max_samples - len(initial))
        if slots:
            chosen = sorted(set(remaining[round(i * (len(remaining) - 1) / max(1, slots - 1))]
                                for i in range(slots)))
        else:
            chosen = []
        planned = sorted(initial + chosen)[:max_samples]
    return initial[:max_samples], planned


def _draw_timeline(report: dict, path: Path) -> None:
    width, height = 1500, 780
    canvas = np.full((height, width, 3), 250, dtype=np.uint8)
    duration = max(1.0, report["video"]["duration_seconds"])
    left, right = 110, width - 45
    upper = (95, 385)
    lower = (485, 705)
    for y0, y1, title in ((upper[0], upper[1], "Reloj de partido vs tiempo de video"),
                          (lower[0], lower[1], "Desfase video - reloj normalizado (s)")):
        cv2.rectangle(canvas, (left, y0), (right, y1), (255, 255, 255), -1)
        cv2.rectangle(canvas, (left, y0), (right, y1), (80, 80, 80), 1)
        cv2.putText(canvas, title, (left, y0 - 15), cv2.FONT_HERSHEY_SIMPLEX, .68,
                    (30, 30, 30), 2, cv2.LINE_AA)
    def x_of(value: float) -> int:
        return int(left + max(0, min(duration, value)) / duration * (right - left))

    valid = [row for row in report["scans"] if row.get("game_clock_seconds") is not None]
    if valid:
        min_clock = min(row["game_clock_seconds"] for row in valid)
        max_clock = max(row["game_clock_seconds"] for row in valid)
        if max_clock == min_clock:
            max_clock += 1
        offsets = [row["offset_seconds"] for row in valid]
        min_offset, max_offset = min(offsets), max(offsets)
        padding = max(10.0, (max_offset - min_offset) * .08)
        min_offset -= padding
        max_offset += padding
        for tick in range(5):
            fraction = tick / 4
            clock_value = min_clock + (max_clock - min_clock) * fraction
            clock_y = upper[1] - int(fraction * (upper[1] - upper[0]))
            offset_value = min_offset + (max_offset - min_offset) * fraction
            offset_y = lower[1] - int(fraction * (lower[1] - lower[0]))
            cv2.line(canvas, (left, clock_y), (right, clock_y), (232, 232, 232), 1)
            cv2.line(canvas, (left, offset_y), (right, offset_y), (232, 232, 232), 1)
            cv2.putText(canvas, clock_text(clock_value), (8, clock_y + 5),
                        cv2.FONT_HERSHEY_SIMPLEX, .43, (45, 45, 45), 1, cv2.LINE_AA)
            cv2.putText(canvas, f"{offset_value:.0f}s", (8, offset_y + 5),
                        cv2.FONT_HERSHEY_SIMPLEX, .43, (45, 45, 45), 1, cv2.LINE_AA)
        colors = {"first": (210, 100, 30), "second": (30, 140, 210)}
        supported_reference_times = set()
        for segment in report["segments"]:
            refs = [ref for ref in segment["reference_points"]]
            color = colors.get(segment["period"], (80, 130, 80))
            previous = None
            for ref in refs:
                supported_reference_times.add(ref["video_second"])
                x = x_of(ref["video_second"])
                game_y = upper[1] - int((ref["game_clock_second"] - min_clock) /
                                        (max_clock - min_clock) * (upper[1] - upper[0]))
                reference_offset = ref["video_second"] - ref["game_clock_second"]
                off_y = lower[1] - int((reference_offset - min_offset) /
                                        max(1e-6, max_offset - min_offset) * (lower[1] - lower[0]))
                if previous:
                    cv2.line(canvas, previous[0], (x, game_y), color, 2, cv2.LINE_AA)
                    cv2.line(canvas, previous[1], (x, off_y), color, 2, cv2.LINE_AA)
                cv2.circle(canvas, (x, game_y), 6, color, -1, cv2.LINE_AA)
                cv2.circle(canvas, (x, off_y), 6, color, -1, cv2.LINE_AA)
                previous = ((x, game_y), (x, off_y))
        for row in valid:
            if row["video_second"] in supported_reference_times:
                continue
            x = x_of(row["video_second"])
            game_y = upper[1] - int((row["game_clock_seconds"] - min_clock) /
                                    (max_clock - min_clock) * (upper[1] - upper[0]))
            reference_offset = row["video_second"] - row["game_clock_seconds"]
            off_y = lower[1] - int((reference_offset - min_offset) /
                                   max(1e-6, max_offset - min_offset) * (lower[1] - lower[0]))
            cv2.drawMarker(canvas, (x, game_y), (180, 60, 180), cv2.MARKER_TILTED_CROSS,
                           12, 2, cv2.LINE_AA)
            cv2.drawMarker(canvas, (x, off_y), (180, 60, 180), cv2.MARKER_TILTED_CROSS,
                           12, 2, cv2.LINE_AA)
        for row in report["scans"]:
            x = x_of(row["video_second"])
            if row.get("status") == "marker_not_visible":
                cv2.line(canvas, (x, upper[1] - 5), (x, upper[1] + 5), (140, 140, 140), 1)
            elif row.get("status") == "ambiguous_invalid":
                cv2.drawMarker(canvas, (x, upper[1] - 7), (0, 130, 230), cv2.MARKER_TILTED_CROSS,
                               10, 2, cv2.LINE_AA)
        cv2.putText(canvas, f"Rango de referencias: {min_offset + padding:.1f} a {max_offset - padding:.1f} s",
                    (left + 8, lower[0] + 22), cv2.FONT_HERSHEY_SIMPLEX, .48, (50, 50, 50), 1, cv2.LINE_AA)

    for tick in range(0, 6):
        second = duration * tick / 5
        x = x_of(second)
        cv2.line(canvas, (x, upper[1]), (x, upper[1] + 6), (40, 40, 40), 1)
        cv2.line(canvas, (x, lower[1]), (x, lower[1] + 6), (40, 40, 40), 1)
        whole = int(second)
        label = (f"{whole // 3600:02d}:{(whole // 60) % 60:02d}"
                 if whole >= 3600 else clock_text(whole))
        cv2.putText(canvas, label, (x - 30, lower[1] + 28),
                    cv2.FONT_HERSHEY_SIMPLEX, .45, (35, 35, 35), 1, cv2.LINE_AA)
    summary = (f"AutoCalibrate: {len(valid)} referencias | {len(report['segments'])} segmentos | "
               f"cobertura interpolable {report['coverage']['percent']:.1f}% | "
               f"confianza {report['confidence']['level']}")
    cv2.putText(canvas, summary, (left, 40), cv2.FONT_HERSHEY_SIMPLEX, .7,
                (25, 25, 25), 2, cv2.LINE_AA)
    cv2.line(canvas, (1000, 64), (1030, 64), (210, 100, 30), 3, cv2.LINE_AA)
    cv2.putText(canvas, "1a parte", (1037, 69), cv2.FONT_HERSHEY_SIMPLEX, .42,
                (40, 40, 40), 1, cv2.LINE_AA)
    cv2.line(canvas, (1120, 64), (1150, 64), (30, 140, 210), 3, cv2.LINE_AA)
    cv2.putText(canvas, "2a parte", (1157, 69), cv2.FONT_HERSHEY_SIMPLEX, .42,
                (40, 40, 40), 1, cv2.LINE_AA)
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), canvas, [cv2.IMWRITE_JPEG_QUALITY, 92])


def run_auto_calibration(video_path: Path, *, output_path: Path,
                         report_dir: Path, initial_samples: int = 12,
                         sample_interval: float = 300.0, max_samples: int = 80,
                         max_processing_seconds: float = 180.0,
                         adaptive_min_gap_seconds: float = 30.0,
                         tolerance_seconds: float = 8.0,
                         max_interpolation_gap_seconds: float = 600.0,
                         sports_events: dict | None = None,
                         sports_events_source: str | None = None) -> dict:
    """Calibrate a video using sparse uniform samples plus bounded refinement."""
    started = time.perf_counter()
    if sports_events is not None:
        sports_events = validate_sports_events(sports_events)
    info = probe(video_path)
    _, _, video_id = video_report_paths(video_path)
    if initial_samples < 4 or max_samples < initial_samples:
        raise ValueError("Usa al menos 4 muestras iniciales y max-samples >= initial-samples.")
    if sample_interval < 30 or adaptive_min_gap_seconds <= 0 or max_processing_seconds <= 0:
        raise ValueError("Intervalos y presupuesto de procesamiento deben ser positivos (intervalo >=30s).")
    initial_times, planned_times = _sample_plan(info["duration_seconds"], initial_samples,
                                                sample_interval, max_samples)
    cache: dict[float, dict] = {}
    scans_by_requested: dict[float, dict] = {}
    detector_passes = []
    region_events = []
    roi_history: list[dict] = []
    roi: ROI | None = None
    detection_result = None
    stop_reason = None

    fps_text = info.get("avg_frame_rate") or "30/1"
    try:
        numerator, denominator = (float(part) for part in fps_text.split("/", 1))
        frame_tolerance = max(0.10, 2 * denominator / max(numerator, 1))
    except (ValueError, ZeroDivisionError):
        frame_tolerance = 0.15

    def budget_available() -> bool:
        return time.perf_counter() - started < max_processing_seconds

    def extract(requested: float) -> dict | None:
        key = round(float(requested), 3)
        if key in cache:
            return cache[key]
        if len(cache) >= max_samples:
            return None
        if not budget_available():
            return None
        bounded = min(key, max(0.0, info["duration_seconds"] - 0.05))
        frame, actual = extract_frame_with_timestamp(video_path, bounded)
        verified = actual is not None and abs(actual - bounded) <= frame_tolerance
        video_second = actual if actual is not None else bounded
        record = {"requested_video_second": key, "video_second": round(video_second, 3),
                  "actual_pts_seconds": round(actual, 6) if actual is not None else None,
                  "timestamp_source": "ffmpeg_showinfo_pts" if actual is not None else "requested_seek_fallback",
                  "timestamp_error_seconds": round(actual - bounded, 6) if actual is not None else None,
                  "timestamp_verified": bool(verified), "frame": frame}
        cache[key] = record
        return record

    def write_detection_pass(records: list[dict], pass_index: int) -> dict:
        nonlocal roi, detection_result
        ordered = sorted(records, key=lambda row: row["video_second"])
        detector = ClockDetector()
        result = detector.detect([(row["video_second"], row["frame"]) for row in ordered])
        pass_dir = report_dir / f"detector_pass_{pass_index:02d}"
        detector.write_visual_report(result,
                                     [(row["video_second"], row["frame"]) for row in ordered],
                                     pass_dir)
        detector_passes.append({"pass": pass_index, "status": result["status"],
                                "confidence": result["confidence"],
                                "processing_seconds": result["processing_seconds"],
                                "sample_count": result["sample_count"],
                                "report_json": str(pass_dir / "detection.json"),
                                "report_image": str(pass_dir / "detection_report.jpg")})
        detection_result = result
        if result["status"] == "confirmed":
            roi = ROI(**result["roi"])
            _apply_detector_readings(ordered, result)
            for row in ordered:
                row["roi_epoch"] = len(roi_history)
                scans_by_requested[row["requested_video_second"]] = {
                    key: value for key, value in row.items() if key != "frame"
                }
            if not roi_history:
                roi_history.append({"at_video_second": 0.0, "roi": roi.as_dict(), "epoch": 0,
                                    "reason": "initial_clock_detector_confirmation"})
        return result

    # Search in evenly spaced seeds first. If the overlays hide the clock in
    # those seeds, add further coarse samples in bounded batches and retry.
    initial_records = []
    for requested in initial_times:
        row = extract(requested)
        if row:
            initial_records.append(row)
    if initial_records:
        detection_result = write_detection_pass(initial_records, 1)
    next_times = [point for point in planned_times if round(point, 3) not in cache]
    pass_index = 2
    while (detection_result and detection_result["status"] != "confirmed"
           and next_times and len(cache) < max_samples and budget_available()):
        batch_times = next_times[:min(8, max_samples - len(cache))]
        next_times = next_times[len(batch_times):]
        batch = [row for point in batch_times if (row := extract(point)) is not None]
        if not batch:
            break
        detection_result = write_detection_pass(list(cache.values()), pass_index)
        pass_index += 1

    if roi is None:
        stop_reason = "clock_region_not_confirmed"
        unresolved = []
        for row in sorted(cache.values(), key=lambda item: item["video_second"]):
            unresolved.append({key: value for key, value in row.items() if key != "frame"} | {
                "sample_id": f"A{len(unresolved) + 1:03d}", "clock_seconds": None, "clock": None,
                "votes": 0, "candidate_clock_seconds": None, "status": "ambiguous_invalid",
                "ocr_status": "ambiguous_invalid", "period": None,
                "status_reason": stop_reason,
            })
        scans = unresolved
    else:
        # Complete the coarse grid while reusing initial detector OCR results.
        invalid_streak = 0
        last_search_sample_count = 0
        coarse_rows = sorted(cache.values(), key=lambda item: item["video_second"])
        for requested in planned_times:
            key = round(requested, 3)
            row = extract(requested)
            if row is None:
                stop_reason = "sample_or_time_budget_exhausted"
                break
            if key not in scans_by_requested:
                active_roi = roi
                active_epoch = len(roi_history) - 1
                scan = _read_record(row["frame"], row["video_second"], row["requested_video_second"],
                                    row["actual_pts_seconds"], row["timestamp_verified"],
                                    active_roi, f"A{len(scans_by_requested) + 1:03d}", active_epoch)
                scans_by_requested[key] = scan
                coarse_rows.append(row)
            scan = scans_by_requested[key]
            if scan.get("ocr_status") == "valid_reading":
                invalid_streak = 0
            else:
                invalid_streak += 1

            if (invalid_streak >= 3 and len(scans_by_requested) - last_search_sample_count >= 5
                    and budget_available()):
                recent = sorted((cache[round(value["requested_video_second"], 3)]
                                 for value in sorted(scans_by_requested.values(),
                                                     key=lambda item: item["video_second"])[-6:]),
                                key=lambda item: item["video_second"])
                if len(recent) >= 3:
                    search = ClockDetector().detect([(item["video_second"], item["frame"]) for item in recent])
                    event = {"video_second": scan["video_second"], "status": search["status"],
                             "reason": search["reason"], "confidence": search["confidence"],
                             "processing_seconds": search["processing_seconds"]}
                    if search["status"] == "confirmed":
                        candidate_roi = ROI(**search["roi"])
                        old_roi = roi
                        frame_height, frame_width = row["frame"].shape[:2]
                        moved = _normalized_center_distance(old_roi, candidate_roi, frame_width, frame_height) > 0.015
                        if moved:
                            roi = candidate_roi
                            epoch = len(roi_history)
                            selected_track = next((track for track in search["candidate_tracks"]
                                                   if track.get("decision") == "selected"), {})
                            selected_observations = selected_track.get("observations", [])
                            change_at = min((point["video_second"] for point in selected_observations),
                                            default=scan["video_second"])
                            roi_history.append({"at_video_second": change_at, "roi": roi.as_dict(),
                                                "epoch": epoch, "reason": "persistent_ocr_loss_and_new_region_confirmed"})
                            event["region_change"] = {"from_roi": old_roi.as_dict(),
                                                       "to_roi": roi.as_dict(),
                                                       "effective_video_second": change_at}
                            for sample in recent:
                                old_scan = scans_by_requested.get(round(sample["requested_video_second"], 3), {})
                                reread = _read_record(sample["frame"], sample["video_second"],
                                                      sample["requested_video_second"],
                                                      sample["actual_pts_seconds"],
                                                      sample["timestamp_verified"], roi,
                                                      old_scan.get("sample_id", "A000"), epoch)
                                scans_by_requested[round(sample["requested_video_second"], 3)] = reread
                        else:
                            event["region_change"] = None
                    report_path = report_dir / f"detector_recheck_{len(region_events) + 1:02d}"
                    ClockDetector().write_visual_report(search,
                        [(item["video_second"], item["frame"]) for item in recent], report_path)
                    event["report_json"] = str(report_path / "detection.json")
                    event["report_image"] = str(report_path / "detection_report.jpg")
                    region_events.append(event)
                last_search_sample_count = len(scans_by_requested)

        # Refine state changes and suspicious temporal jumps by bisecting their
        # intervals; stop on a 30s bracket or either configured budget.
        while budget_available() and len(cache) < max_samples:
            current_scans = sorted(scans_by_requested.values(), key=lambda item: item["video_second"])
            candidates = refinement_gaps(current_scans, min_gap_seconds=adaptive_min_gap_seconds,
                                         tolerance_seconds=tolerance_seconds,
                                         max_gap_seconds=max_interpolation_gap_seconds)
            fresh = [gap for gap in candidates
                     if all(abs(gap["midpoint"] - existing["video_second"]) > 1
                            for existing in current_scans)]
            if not fresh:
                break
            capacity = min(8, max_samples - len(cache))
            selected_gaps = fresh[:capacity]
            for gap in selected_gaps:
                requested = gap["midpoint"]
                row = extract(requested)
                if row is None:
                    stop_reason = "sample_or_time_budget_exhausted"
                    break
                history = [event for event in roi_history if event["at_video_second"] <= row["video_second"]]
                active = history[-1] if history else roi_history[0]
                active_roi = ROI(**active["roi"])
                scans_by_requested[round(requested, 3)] = _read_record(
                    row["frame"], row["video_second"], row["requested_video_second"],
                    row["actual_pts_seconds"], row["timestamp_verified"], active_roi,
                    f"A{len(scans_by_requested) + 1:03d}", active["epoch"])
            if stop_reason == "sample_or_time_budget_exhausted":
                break
        if stop_reason is None:
            remaining = refinement_gaps(sorted(scans_by_requested.values(),
                                               key=lambda item: item["video_second"]),
                                        min_gap_seconds=adaptive_min_gap_seconds,
                                        tolerance_seconds=tolerance_seconds,
                                        max_gap_seconds=max_interpolation_gap_seconds)
            if remaining and len(cache) >= max_samples:
                stop_reason = "sample_limit_reached_with_remaining_uncertainty"
            elif remaining and not budget_available():
                stop_reason = "time_budget_reached_with_remaining_uncertainty"
        scans = sorted(scans_by_requested.values(), key=lambda item: item["video_second"])

    segments, period_events, second_mode = prepare_observations(
        scans, sample_interval=sample_interval, tolerance_seconds=tolerance_seconds,
        max_gap_seconds=max_interpolation_gap_seconds, sports_events=sports_events)
    supported_points = {point["video_second"] for segment in segments
                        for point in segment["reference_points"]}
    unsupported_reads = [row for row in scans if row.get("ocr_status") == "valid_reading"
                         and row.get("video_second") not in supported_points]
    uncertainty = uncertainty_zones(info["duration_seconds"], segments, period_events)
    coverage_ranges = sorted((segment["video_start"], segment["video_end"]) for segment in segments)
    merged_coverage = []
    for start, end in coverage_ranges:
        if not merged_coverage or start > merged_coverage[-1][1]:
            merged_coverage.append([start, end])
        else:
            merged_coverage[-1][1] = max(merged_coverage[-1][1], end)
    segment_coverage = sum(max(0.0, end - start) for start, end in merged_coverage)
    has_uncertain_boundary = any(event.get("confirmed") and event.get("confidence") != "high"
                                 for event in period_events)
    has_uncertain_period = any(segment.get("period") == "uncertain" for segment in segments)
    has_low_boundary = any(event.get("confirmed") and event.get("confidence") == "low"
                           for event in period_events)
    confidence_level = ("unconfirmed" if not segments else
                        "low" if has_low_boundary or has_uncertain_period else
                        "high" if not has_uncertain_boundary and
                        all(segment["confidence_level"] == "high" for segment in segments) else "medium")
    confidence_score = (statistics.mean(segment["confidence"] for segment in segments)
                        if segments else 0.0)
    if has_uncertain_boundary:
        confidence_score *= 0.85
    if has_low_boundary or has_uncertain_period:
        confidence_score *= 0.65
    status_counts = dict(Counter(row.get("status", "ambiguous_invalid") for row in scans))
    valid_ocr_scans = [row for row in scans if row.get("ocr_status") == "valid_reading"]
    pts_rows = [row for row in scans if row.get("actual_pts_seconds") is not None]
    timestamp_validation = {
        "frames_with_pts": len(pts_rows),
        "verified_frames": sum(bool(row.get("timestamp_verified")) for row in pts_rows),
        "verification_tolerance_seconds": round(frame_tolerance, 6),
        "max_absolute_seek_error_seconds": round(max(
            (abs(row["timestamp_error_seconds"]) for row in pts_rows
             if row.get("timestamp_error_seconds") is not None), default=0.0), 6),
        "source": "FFmpeg showinfo pts_time; requested seek retained as a fallback if absent",
    }
    report = {
        "version": 5,
        "status": ("calibrated" if segments and not has_uncertain_period else
                   "periods_uncertain" if segments else "unconfirmed"),
        "video_id": video_id,
        "video": info,
        "roi": roi.as_dict() if roi else None,
        "clock_detection": detection_result,
        "detector_passes": detector_passes,
        "region_events": region_events,
        "roi_history": roi_history,
        "parameters": {
            "initial_samples": initial_samples, "coarse_interval_seconds": sample_interval,
            "max_samples": max_samples, "max_processing_seconds": max_processing_seconds,
            "adaptive_min_gap_seconds": adaptive_min_gap_seconds,
            "offset_tolerance_seconds": tolerance_seconds,
            "max_interpolation_gap_seconds": max_interpolation_gap_seconds,
            "second_clock_mode": second_mode,
            "sports_events_enabled": sports_events is not None,
        },
        "sports_events": ({
            "status": "validated_and_considered",
            "contract": sports_events["contract"],
            "version": sports_events["version"],
            "fingerprint": sports_events_fingerprint(sports_events),
            "source": sports_events_source,
            "event_count": len(sports_events["events"]),
            "event_clock_match_tolerance_seconds": EVENT_CLOCK_MATCH_TOLERANCE_SECONDS,
            "event_clock_conflict_threshold_seconds": EVENT_CLOCK_CONFLICT_THRESHOLD_SECONDS,
            "events": sports_events["events"],
            "time_axis_note": "Parseable source_minute/game_clock_seconds is match time only; no event is converted to video time.",
        } if sports_events is not None else {
            "status": "not_provided", "contract": None, "version": None,
            "fingerprint": None, "source": None, "event_count": 0, "events": [],
            "event_clock_match_tolerance_seconds": EVENT_CLOCK_MATCH_TOLERANCE_SECONDS,
            "event_clock_conflict_threshold_seconds": EVENT_CLOCK_CONFLICT_THRESHOLD_SECONDS,
            "time_axis_note": "No external event evidence was used.",
        }),
        "periods": [],
        "period_events": period_events,
        "segments": segments,
        "coverage": {
            "video_seconds": round(info["duration_seconds"], 3),
            "interpolable_seconds": round(min(info["duration_seconds"], segment_coverage), 3),
            "percent": round(min(100.0, segment_coverage / info["duration_seconds"] * 100), 2),
            "sampled_frames": len(scans), "valid_ocr_references": sum(
                row.get("ocr_status") == "valid_reading" for row in scans),
            "supported_references": len(supported_points),
            "football_period_classified_seconds": round(sum(
                max(0.0, segment["video_end"] - segment["video_start"])
                for segment in segments if segment.get("period") in {"first", "second"}), 3),
            "coverage_basis": "Only spans bracketed by at least two compatible OCR references are interpolable.",
        },
        "timestamp_validation": timestamp_validation,
        "confidence": {"level": confidence_level, "score": round(confidence_score, 3),
                       "basis": "Legacy aggregate; inspect confidence_dimensions for separate evidence classes."},
        "confidence_dimensions": {
            "ocr": {
                "level": ("high" if valid_ocr_scans and
                         sum(row.get("ocr_confidence") == "high" for row in valid_ocr_scans) /
                         len(valid_ocr_scans) >= 0.7 else "medium" if valid_ocr_scans and
                         sum(row.get("ocr_confidence") in {"high", "medium"} for row in valid_ocr_scans) /
                         len(valid_ocr_scans) >= 0.5 else "low" if valid_ocr_scans else "uncertain"),
                "basis": "Per-scan Tesseract consensus votes; marker absence is not an OCR failure.",
            },
            "period": {
                "level": (next((event.get("period_confidence") for event in period_events
                                if event.get("confirmed")), "uncertain")),
                "evidence_sources": sorted({source for event in period_events
                                             for source in event.get("evidence_sources", [])}),
                "basis": "OCR chronology, with optional compatible normalized period-event evidence.",
            },
            "location": {
                "level": ("high" if segments and all(s["location_confidence"] == "high" for s in segments)
                          else "medium" if segments else "uncertain"),
                "basis": "Compatible-reference count, residual and maximum interpolation gap; duplicate matches return ambiguous.",
            },
        },
        "uncertainty_zones": uncertainty,
        "unsupported_valid_reads": [{key: value for key, value in row.items() if key != "variants"}
                                    for row in unsupported_reads],
        "status_counts": status_counts,
        "stop_reason": stop_reason,
        "processing_seconds": round(time.perf_counter() - started, 3),
        "scans": scans,
        "limitations": [
            "Las muestras prueban solo los instantes extraídos; un corte oculto y compensado entre muestras puede pasar inadvertido.",
            "La continuidad se acepta solo entre referencias compatibles y dentro de max_interpolation_gap_seconds.",
            "Una pausa larga en mitad de parte no confirma un descanso; el contexto del reloj y los eventos compatibles solo complementan la evidencia OCR.",
            "Los eventos deportivos sin minuto pueden aportar semántica y orden, pero nunca reciben una posición de vídeo.",
            "El modo reset normaliza la segunda parte con la convención de minuto 45; la detección de frontera no presupone que la primera dure 45 minutos.",
        ],
    }
    first_segment = next((segment for segment in segments if segment["period"] == "first"), None)
    if first_segment:
        first_anchor = round(first_segment["offset_seconds"], 3)
        report["periods"].append({
            "period": "first", "clock_mode": "raw_clock_from_period_start",
            "first_observed_video_second": first_segment["video_start"],
            "nominal_clock_anchor": {
                "game_clock_second": 0, "video_second": first_anchor,
                "verified_inside_segment": first_segment["game_clock_start"] <= 0,
                "basis": "offset_derived_estimate; may be outside the verified reference span",
            },
        })
    second_segment = next((segment for segment in segments if segment["period"] == "second"), None)
    if second_segment and second_mode:
        second_anchor_clock = 45 * 60
        second_anchor = round(second_segment["offset_seconds"] + second_anchor_clock, 3)
        boundary_event = next((event for event in period_events if event.get("confirmed")), None)
        report["periods"].append({
            "period": "second", "clock_mode": second_mode,
            "first_observed_video_second": second_segment["video_start"],
            "boundary_interval": (boundary_event.get("uncertain_video_interval")
                                  if boundary_event else None),
            "nominal_clock_anchor": {
                "game_clock_second": second_anchor_clock, "video_second": second_anchor,
                "verified_inside_segment": (second_segment["game_clock_start"] <= second_anchor_clock <=
                                             second_segment["game_clock_end"]),
                "basis": "offset_derived_estimate; 45:00 is the match-minute convention, not a detected boundary",
            },
        })
    uncertain_segments = [segment for segment in segments if segment["period"] == "uncertain"]
    if uncertain_segments:
        report["periods"].append({
            "period": "uncertain", "clock_mode": "unresolved",
            "temporal_segment_ids": [segment["segment_id"] for segment in uncertain_segments],
            "reason": "insufficient_temporal_evidence_to_assign_a_football_period",
        })
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    _draw_timeline(report, report_dir / "clock_sync_timeline.jpg")
    return report
