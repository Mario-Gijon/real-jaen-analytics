"""Multi-sample, position-independent television clock localization."""
from __future__ import annotations

import json
import math
from pathlib import Path
import statistics
import time
from typing import Iterable

import cv2
import numpy as np

from .clock import ROI, clock_text, parse_clock, preprocess, read_clock_crop, tesseract_read


class ClockDetector:
    """Find a stable clock region using visual proposals and temporal evidence.

    The first pass looks for compact horizontal panels at any image position.
    If it cannot confirm a clock, a connected-component text-line search runs
    over the full frame. No absolute image coordinates are used.
    """

    def __init__(self, *, min_samples: int = 2, offset_tolerance_seconds: float = 8.0,
                 max_tracks: int = 12, min_confidence: float = 0.60):
        self.min_samples = min_samples
        self.offset_tolerance_seconds = offset_tolerance_seconds
        self.max_tracks = max_tracks
        self.min_confidence = min_confidence

    def detect(self, samples: Iterable[tuple[float, np.ndarray]]) -> dict:
        started = time.perf_counter()
        ordered = sorted(samples, key=lambda item: item[0])
        if len(ordered) < self.min_samples:
            return self._empty_result(ordered, started, "insufficient_samples")
        if any(frame is None or frame.ndim < 2 for _, frame in ordered):
            return self._empty_result(ordered, started, "invalid_frame")

        frame_proposals = [self._panel_proposals(frame) for _, frame in ordered]
        diagnostic_proposals = frame_proposals
        panel_tracks = self._spatial_tracks(frame_proposals, ordered)
        evaluated = self._evaluate_tracks(panel_tracks, ordered)
        stage = "panel_regions"
        if not any(track["coherent_reading_count"] >= self.min_samples for track in evaluated):
            line_proposals = [self._textline_proposals(frame) for _, frame in ordered]
            diagnostic_proposals = [
                sorted(panel + lines, key=lambda proposal: (
                    proposal["proposal_support"], proposal["edge_priority"], proposal["quality"]
                ), reverse=True)
                for panel, lines in zip(frame_proposals, line_proposals)
            ]
            line_tracks = self._spatial_tracks(line_proposals, ordered)
            line_evaluated = self._evaluate_tracks(line_tracks, ordered)
            evaluated = self._merge_track_diagnostics(evaluated, line_evaluated)
            stage = "panel_regions_then_text_lines"

        evaluated.sort(key=lambda track: (
            track["confidence"], track["coherent_reading_count"],
            track["proposal_support"], track["edge_priority"],
        ), reverse=True)
        supported = [track for track in evaluated
                     if track["coherent_reading_count"] >= self.min_samples
                     and track["confidence"] >= self.min_confidence]

        selected = supported[0] if supported else None
        competing = []
        if selected:
            for track in supported[1:]:
                if self._region_distance(selected["region_normalized"],
                                         track["region_normalized"]) > 0.035:
                    if selected["confidence"] - track["confidence"] <= 0.10:
                        competing.append(track)

        if selected is None:
            status = "not_confirmed"
            fragmented_readings = sum(track["valid_reading_count"] for track in evaluated)
            if any(track["coherent_reading_count"] >= self.min_samples for track in evaluated):
                reason = "temporal_candidate_below_confidence_threshold"
            elif fragmented_readings >= self.min_samples:
                reason = "candidate_evidence_fragmented_across_regions"
            else:
                reason = "no_spatially_stable_temporally_coherent_clock"
            chosen_roi = None
        elif competing:
            status = "ambiguous"
            reason = "multiple_regions_have_similar_temporal_evidence"
            chosen_roi = None
        else:
            status = "confirmed"
            reason = "repeated_region_and_temporal_progression_agree"
            chosen_roi = selected["roi"]
            selected["decision"] = "selected"

        for track in evaluated:
            if status == "ambiguous" and (track is selected or track in competing):
                track["decision"] = "competing"
                track["reason"] = "multiple_regions_have_similar_temporal_evidence"
                continue
            if track.get("decision") != "selected":
                if track["coherent_reading_count"] < self.min_samples:
                    track["decision"] = "rejected"
                    if track["proposal_support"] < self.min_samples:
                        track["reason"] = "region_seen_in_too_few_samples"
                    elif not track["valid_reading_count"]:
                        track["reason"] = "no_unambiguous_clock_ocr"
                    else:
                        track["reason"] = "readings_not_temporally_coherent"
                elif status == "ambiguous" and track in competing:
                    track["decision"] = "competing"
                    track["reason"] = "another_stable_clock_candidate_has_similar_support"
                else:
                    track["decision"] = "rejected"
                    track["reason"] = "lower_confidence_than_selected_region"

        evidence_tracks = ([selected] + competing) if selected else [
            track for track in evaluated if track["valid_reading_count"]
        ]
        sample_evidence = []
        for sample_index, (video_second, _) in enumerate(ordered):
            observations_here = [observation for track in evidence_tracks
                                 for observation in track.get("observations", [])
                                 if observation["sample_index"] == sample_index]
            proposal_here = any(sample_index in track.get("proposal_sample_indices", [])
                                for track in evidence_tracks)
            if observations_here and status == "confirmed":
                sample_status = "valid_reading"
                sample_reason = "clock_ocr_and_track_confirmed"
            elif observations_here:
                sample_status = "candidate_unconfirmed"
                sample_reason = "ocr_candidate_lacks_unique_track_confirmation"
            elif proposal_here:
                sample_status = "ambiguous_invalid"
                sample_reason = "region_proposed_but_no_valid_clock_consensus"
            else:
                sample_status = "marker_not_visible"
                sample_reason = "no_clock_region_proposal_at_candidate_location"
            sample_evidence.append({
                "sample_index": sample_index,
                "video_second": round(float(video_second), 3),
                "status": sample_status,
                "reason": sample_reason,
            })

        result = {
            "version": 1,
            "status": status,
            "reason": reason,
            "roi": chosen_roi,
            "relative_roi": selected["roi_normalized"] if selected and status == "confirmed" else None,
            "reference_resolution": self._resolution(ordered[0][1]) if ordered else None,
            "confidence": round(selected["confidence"], 4) if selected else 0.0,
            "search_stage": stage,
            "sample_count": len(ordered),
            "candidate_tracks": evaluated[:self.max_tracks],
            "sample_evidence": sample_evidence,
            "frame_proposals": [
                {"sample_index": i, "video_second": round(ordered[i][0], 3),
                 "resolution": self._resolution(ordered[i][1]),
                 "regions": proposals[:20]}
                for i, proposals in enumerate(diagnostic_proposals)
            ],
            "processing_seconds": round(time.perf_counter() - started, 3),
            "confidence_basis": {
                "minimum_temporal_references": self.min_samples,
                "offset_tolerance_seconds": self.offset_tolerance_seconds,
                "best_candidate_valid_readings": selected["coherent_reading_count"] if selected else 0,
                "best_candidate_temporal_segments": len(selected["temporal_segments"]) if selected else 0,
                "region_confirmed": status == "confirmed",
            },
        }
        return result

    def _empty_result(self, samples: list, started: float, reason: str) -> dict:
        return {
            "version": 1, "status": "not_confirmed", "reason": reason,
            "roi": None, "relative_roi": None, "reference_resolution": None,
            "confidence": 0.0, "search_stage": "not_run", "sample_count": len(samples),
            "candidate_tracks": [], "frame_proposals": [], "sample_evidence": [],
            "processing_seconds": round(time.perf_counter() - started, 3),
            "confidence_basis": {"minimum_temporal_references": self.min_samples},
        }

    @staticmethod
    def _resolution(frame: np.ndarray) -> dict[str, int]:
        return {"width": int(frame.shape[1]), "height": int(frame.shape[0])}

    @staticmethod
    def _edge_priority(box: tuple[int, int, int, int], width: int, height: int) -> float:
        x, y, box_width, box_height = box
        cx = (x + box_width / 2) / width
        cy = (y + box_height / 2) / height
        return round(max(0.0, min(1.0, 1 - 2 * min(cx, 1 - cx, cy, 1 - cy))), 4)

    @staticmethod
    def _iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
        ax, ay, aw, ah = a
        bx, by, bw, bh = b
        x0, y0 = max(ax, bx), max(ay, by)
        x1, y1 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
        intersection = max(0, x1 - x0) * max(0, y1 - y0)
        union = aw * ah + bw * bh - intersection
        return intersection / union if union else 0.0

    def _component_proposals(self, gray: np.ndarray, masks: list[tuple[str, np.ndarray]],
                             source: str) -> list[dict]:
        height, width = gray.shape
        raw = []
        min_width = max(18, int(width * 0.025))
        min_height = max(7, int(height * 0.012))
        for label, mask in masks:
            count, _, stats, _ = cv2.connectedComponentsWithStats(
                mask.astype(np.uint8), connectivity=8)
            for x, y, box_width, box_height, area in stats[1:count]:
                aspect = box_width / max(1, box_height)
                fill = area / max(1, box_width * box_height)
                if not (min_width <= box_width <= width * 0.40
                        and min_height <= box_height <= height * 0.14
                        and 1.35 <= aspect <= 12 and fill >= 0.18):
                    continue
                box = (int(x), int(y), int(box_width), int(box_height))
                raw.append({"box": box, "source": f"{source}:{label}",
                            "fill": float(fill),
                            "edge_priority": self._edge_priority(box, width, height)})

        groups: list[list[dict]] = []
        for proposal in sorted(raw, key=lambda item: item["fill"], reverse=True):
            box = proposal["box"]
            group = next((g for g in groups if max(self._iou(box, item["box"]) for item in g) >= 0.48), None)
            if group is None:
                groups.append([proposal])
            else:
                group.append(proposal)

        merged = []
        for group in groups:
            boxes = [item["box"] for item in group]
            box = tuple(int(round(statistics.median(b[i] for b in boxes))) for i in range(4))
            merged.append({
                "box": box, "source": source,
                "source_variants": sorted({item["source"] for item in group}),
                "proposal_support": len(group),
                "quality": round(statistics.mean(item["fill"] for item in group), 4),
                "edge_priority": self._edge_priority(box, width, height),
            })
        return merged

    def _panel_proposals(self, frame: np.ndarray) -> list[dict]:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        masks = []
        for threshold in (175, 200, 225):
            masks.append((f"bright_{threshold}", gray >= threshold))
        for threshold in (30, 55, 80):
            masks.append((f"dark_{threshold}", gray <= threshold))
        otsu_threshold, _ = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        masks.extend([
            ("otsu_bright", gray >= otsu_threshold),
            ("otsu_dark", gray < otsu_threshold),
        ])
        proposals = self._component_proposals(gray, masks, "panel")
        proposals.sort(key=lambda item: (
            item["proposal_support"], item["edge_priority"], item["quality"]
        ), reverse=True)
        return proposals[:60]

    def _textline_proposals(self, frame: np.ndarray) -> list[dict]:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        height, width = gray.shape
        _, otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        proposals = []
        for polarity, mask in (("dark", cv2.bitwise_not(otsu)), ("light", otsu)):
            count, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
            components = []
            for x, y, box_width, box_height, area in stats[1:count]:
                if (max(4, int(height * 0.008)) <= box_height <= height * 0.07
                        and box_width <= width * 0.08 and area >= 5):
                    components.append((int(x), int(y), int(box_width), int(box_height), int(area)))
            groups: list[dict] = []
            for component in sorted(components, key=lambda item: item[0]):
                x, y, box_width, box_height, _ = component
                cy = y + box_height / 2
                match = next((group for group in groups
                              if abs(group["cy"] - cy) <= max(box_height, group["max_height"]) * 0.55
                              and x - (group["x"] + group["width"]) <= max(box_height, group["max_height"]) * 1.8
                              and x >= group["x"] - max(box_height, group["max_height"]) * 0.5), None)
                if match is None:
                    groups.append({"x": x, "y": y, "width": box_width, "height": box_height,
                                   "cy": cy, "max_height": box_height, "components": [component]})
                else:
                    right = max(match["x"] + match["width"], x + box_width)
                    bottom = max(match["y"] + match["height"], y + box_height)
                    match["x"] = min(match["x"], x)
                    match["y"] = min(match["y"], y)
                    match["width"] = right - match["x"]
                    match["height"] = bottom - match["y"]
                    match["components"].append(component)
                    match["max_height"] = max(match["max_height"], box_height)
                    match["cy"] = statistics.mean(c[1] + c[3] / 2 for c in match["components"])
            for group in groups:
                x, y, box_width, box_height = (group[k] for k in ("x", "y", "width", "height"))
                aspect = box_width / max(1, box_height)
                if (len(group["components"]) < 3 or not width * 0.03 <= box_width <= width * 0.22
                        or not height * 0.008 <= box_height <= height * 0.06
                        or not 2 <= aspect <= 10):
                    continue
                box = (int(x), int(y), int(box_width), int(box_height))
                proposals.append({
                    "box": box, "source": f"textline_{polarity}",
                    "source_variants": [f"textline_{polarity}"],
                    "proposal_support": len(group["components"]),
                    "quality": round(min(1.0, len(group["components"]) / 5), 4),
                    "edge_priority": self._edge_priority(box, width, height),
                })
        proposals.sort(key=lambda item: (
            item["proposal_support"], item["edge_priority"], item["quality"]
        ), reverse=True)
        return proposals[:60]

    @staticmethod
    def _normalized_box(proposal: dict, frame: np.ndarray) -> tuple[float, float, float, float]:
        x, y, width, height = proposal["box"]
        frame_height, frame_width = frame.shape[:2]
        return (x / frame_width, y / frame_height,
                width / frame_width, height / frame_height)

    @staticmethod
    def _region_distance(a: dict, b: dict) -> float:
        acx, acy = a["x"] + a["width"] / 2, a["y"] + a["height"] / 2
        bcx, bcy = b["x"] + b["width"] / 2, b["y"] + b["height"] / 2
        return math.hypot(acx - bcx, acy - bcy)

    def _spatial_tracks(self, proposals_by_frame: list[list[dict]],
                        samples: list[tuple[float, np.ndarray]]) -> list[dict]:
        tracks: list[dict] = []
        for sample_index, proposals in enumerate(proposals_by_frame):
            frame = samples[sample_index][1]
            normalized = [(proposal, self._normalized_box(proposal, frame)) for proposal in proposals]
            for proposal, box in normalized:
                x, y, width, height = box
                candidate = {"x": x, "y": y, "width": width, "height": height}
                compatible = []
                for track in tracks:
                    if any(entry["sample_index"] == sample_index for entry in track["entries"]):
                        continue
                    region = track["region_normalized"]
                    center_distance = self._region_distance(candidate, region)
                    size_ratio = max(width / max(region["width"], 1e-6),
                                     region["width"] / max(width, 1e-6),
                                     height / max(region["height"], 1e-6),
                                     region["height"] / max(height, 1e-6))
                    if center_distance <= 0.035 and size_ratio <= 2.2:
                        compatible.append((center_distance, track))
                if compatible:
                    _, track = min(compatible, key=lambda item: item[0])
                    track["entries"].append({"sample_index": sample_index, "proposal": proposal,
                                             "box_normalized": candidate})
                    boxes = [entry["box_normalized"] for entry in track["entries"]]
                    track["region_normalized"] = {
                        key: round(statistics.median(box[key] for box in boxes), 6)
                        for key in ("x", "y", "width", "height")
                    }
                else:
                    tracks.append({
                        "track_id": f"T{len(tracks) + 1:03d}",
                        "entries": [{"sample_index": sample_index, "proposal": proposal,
                                     "box_normalized": candidate}],
                        "region_normalized": candidate,
                    })
        for track in tracks:
            edges = [entry["proposal"]["edge_priority"] for entry in track["entries"]]
            qualities = [entry["proposal"]["quality"] for entry in track["entries"]]
            track["proposal_support"] = len(track["entries"])
            track["edge_priority"] = round(statistics.mean(edges), 4)
            track["proposal_quality"] = round(statistics.mean(qualities), 4)
        return sorted(tracks, key=lambda track: (
            track["proposal_support"], track["edge_priority"], track["proposal_quality"]
        ), reverse=True)

    @staticmethod
    def _reading_roi(frame: np.ndarray, proposal: dict, width_factor: float = 0.88) -> ROI:
        frame_height, frame_width = frame.shape[:2]
        x, y, box_width, box_height = proposal["box"]
        if proposal["source"].startswith("panel"):
            roi_width = max(8, int(round(box_width * width_factor)))
            roi_height = max(8, int(round(box_height * 1.30)))
        else:
            roi_width = max(int(round(box_width * 1.25)), int(round(box_height * 7.6)))
            roi_height = max(12, int(round(box_height * 3.0)))
        center_x, center_y = x + box_width / 2, y + box_height / 2
        left = max(0, min(frame_width - 1, int(round(center_x - roi_width / 2))))
        top = max(0, min(frame_height - 1, int(round(center_y - roi_height / 2))))
        roi_width = min(roi_width, frame_width - left)
        roi_height = min(roi_height, frame_height - top)
        return ROI(left, top, max(1, roi_width), max(1, roi_height))

    @staticmethod
    def _quick_read(crop: np.ndarray) -> tuple[str, int | None]:
        image = preprocess(crop)["gray"]
        text = tesseract_read(image)
        return text, parse_clock(text)

    def _evaluate_track(self, track: dict, samples: list[tuple[float, np.ndarray]],
                        max_gap_seconds: float) -> dict:
        observations = []
        diagnostics = []
        entries = track["entries"]
        ratio_options = lambda proposal: (0.88, 0.76, 0.98) if proposal["source"].startswith("panel") else (1.0,)

        def probe(entry: dict, full_consensus: bool) -> tuple[dict | None, dict]:
            sample_index = entry["sample_index"]
            video_second, frame = samples[sample_index]
            proposal = entry["proposal"]
            attempts = []
            ratios = ratio_options(proposal)
            if not full_consensus:
                ratios = ratios[:1]
            for ratio in ratios:
                roi = self._reading_roi(frame, proposal, ratio)
                crop = roi.crop(frame)
                text, quick_seconds = self._quick_read(crop)
                attempt = {"roi": roi.as_dict(), "text": text, "candidate_seconds": quick_seconds}
                attempts.append(attempt)
                if quick_seconds is None:
                    continue
                if not full_consensus:
                    return {"sample_index": sample_index, "video_second": round(float(video_second), 3),
                            "clock_seconds": quick_seconds, "clock": clock_text(quick_seconds),
                            "roi": roi.as_dict(), "quick_text": text}, {"attempts": attempts, "roi": roi}
                reading = read_clock_crop(crop, min_votes=2, require_marker_evidence=False)
                attempt["consensus_status"] = reading["status"]
                attempt["variants"] = reading["variants"]
                if reading["status"] == "valid_reading":
                    return {
                        "sample_index": sample_index,
                        "video_second": round(float(video_second), 3),
                        "clock_seconds": reading["clock_seconds"],
                        "clock": reading["clock"],
                        "votes": reading["votes"],
                        "status": "valid_reading",
                        "roi": roi.as_dict(),
                        "ocr_text": text,
                        "variants": reading["variants"],
                    }, {"attempts": attempts, "roi": roi}
            return None, {"attempts": attempts, "roi": None}

        # A couple of cheap exact-token probes discard most static signs and
        # unrelated number panels before the full three-variant OCR consensus.
        # Probe several evenly spaced entries so a temporary replay/graphic
        # absence at the beginning of the track does not hide later evidence.
        seed_indices = sorted(set(round(i * (len(entries) - 1) / 4) for i in range(5)))
        seeded = {}
        has_clock_token = False
        for index in seed_indices:
            quick, info = probe(entries[index], full_consensus=False)
            seeded[index] = (quick, info)
            if quick:
                has_clock_token = True
        if not has_clock_token:
            for index, (quick, info) in seeded.items():
                entry = entries[index]
                diagnostics.append({
                    "sample_index": entry["sample_index"],
                    "video_second": round(float(samples[entry["sample_index"]][0]), 3),
                    "status": "ambiguous_invalid",
                    "attempts": info["attempts"],
                })
            return self._track_result(track, samples, observations, diagnostics)

        for index, entry in enumerate(entries):
            quick, quick_info = seeded.get(index, (None, None))
            if quick and quick_info and quick_info["attempts"]:
                # Reuse the successful crop choice and spend extra OCR only
                # after an exact clock token has been observed.
                roi = quick_info["roi"]
                frame = samples[entry["sample_index"]][1]
                reading = read_clock_crop(roi.crop(frame), min_votes=2,
                                          require_marker_evidence=False)
                attempt = quick_info["attempts"][-1]
                attempt["consensus_status"] = reading["status"]
                attempt["variants"] = reading["variants"]
                accepted = None
                if reading["status"] == "valid_reading":
                    accepted = {
                        "sample_index": entry["sample_index"],
                        "video_second": round(float(samples[entry["sample_index"]][0]), 3),
                        "clock_seconds": reading["clock_seconds"], "clock": reading["clock"],
                        "votes": reading["votes"], "status": "valid_reading",
                        "roi": roi.as_dict(), "ocr_text": quick["quick_text"],
                        "variants": reading["variants"],
                    }
                info = quick_info
            else:
                accepted, info = probe(entry, full_consensus=True)
            diagnostics.append({
                "sample_index": entry["sample_index"],
                "video_second": round(float(samples[entry["sample_index"]][0]), 3),
                "status": "valid_reading" if accepted else "ambiguous_invalid",
                "attempts": info["attempts"],
            })
            if accepted:
                observations.append(accepted)

        return self._track_result(track, samples, observations, diagnostics)

    def _track_result(self, track: dict, samples: list[tuple[float, np.ndarray]],
                      observations: list[dict], diagnostics: list[dict]) -> dict:
        spacing = [samples[i][0] - samples[i - 1][0] for i in range(1, len(samples))]
        positive_spacing = [gap for gap in spacing if gap > 0]
        max_gap_seconds = max(420.0, statistics.median(positive_spacing) * 3.2) if positive_spacing else 420.0

        temporal_segments = self._temporal_segments(observations, max_gap_seconds)
        coherent = [point for segment in temporal_segments for point in segment["observations"]]
        valid_count = len(observations)
        stability = self._stability_score([entry["box_normalized"] for entry in track["entries"]])
        mean_votes = statistics.mean(point["votes"] for point in coherent) if coherent else 0
        temporal_quality = len(coherent) / valid_count if valid_count else 0
        support_strength = min(1.0, len(coherent) / 6)
        confidence = (0.35 * support_strength + 0.25 * (mean_votes / 3)
                      + 0.25 * stability + 0.15 * temporal_quality)
        roi_normalized = self._median_roi(coherent or observations, samples)
        region = track["region_normalized"]
        return {
            "track_id": track["track_id"],
            "source": sorted({entry["proposal"]["source"] for entry in track["entries"]}),
            "region_normalized": region,
            "roi_normalized": roi_normalized,
            "roi": self._pixel_roi(roi_normalized, self._resolution(samples[0][1])) if roi_normalized else None,
            "proposal_support": track["proposal_support"],
            "proposal_sample_indices": [entry["sample_index"] for entry in track["entries"]],
            "proposal_quality": track["proposal_quality"],
            "edge_priority": track["edge_priority"],
            "spatial_stability": round(stability, 4),
            "valid_reading_count": valid_count,
            "coherent_reading_count": len(coherent),
            "temporal_segments": temporal_segments,
            "confidence": round(min(1.0, confidence), 4),
            "observations": observations,
            "sample_diagnostics": diagnostics,
        }

    def _evaluate_tracks(self, tracks: list[dict], samples: list[tuple[float, np.ndarray]]) -> list[dict]:
        spacing = [samples[i][0] - samples[i - 1][0] for i in range(1, len(samples))]
        positive_spacing = [gap for gap in spacing if gap > 0]
        max_gap = max(420.0, statistics.median(positive_spacing) * 3.2) if positive_spacing else 420.0
        result = []
        for track in tracks[:self.max_tracks]:
            if track["proposal_support"] < self.min_samples:
                entry = track["entries"][0]
                sample_index = entry["sample_index"]
                frame = samples[sample_index][1]
                proposal = entry["proposal"]
                roi = self._reading_roi(frame, proposal)
                relative = {
                    "x": roi.x / frame.shape[1], "y": roi.y / frame.shape[0],
                    "width": roi.width / frame.shape[1], "height": roi.height / frame.shape[0],
                }
                result.append({
                    "track_id": track["track_id"],
                    "source": sorted({e["proposal"]["source"] for e in track["entries"]}),
                    "region_normalized": track["region_normalized"],
                    "roi_normalized": relative,
                    "roi": roi.as_dict(),
                    "proposal_support": track["proposal_support"],
                    "proposal_sample_indices": [entry["sample_index"] for entry in track["entries"]],
                    "proposal_quality": track["proposal_quality"],
                    "edge_priority": track["edge_priority"],
                    "spatial_stability": 0.0,
                    "valid_reading_count": 0,
                    "coherent_reading_count": 0,
                    "temporal_segments": [],
                    "confidence": 0.0,
                    "observations": [],
                    "sample_diagnostics": [],
                    "reason": "region_seen_in_too_few_samples",
                })
                continue
            evaluated = self._evaluate_track(track, samples, max_gap)
            if evaluated["proposal_support"] < self.min_samples:
                evaluated["reason"] = "region_seen_in_too_few_samples"
            elif not evaluated["valid_reading_count"]:
                evaluated["reason"] = "no_unambiguous_clock_ocr"
            elif evaluated["coherent_reading_count"] < self.min_samples:
                evaluated["reason"] = "readings_not_temporally_coherent"
            else:
                evaluated["reason"] = "temporal_evidence_available"
            result.append(evaluated)
        return result

    def _temporal_segments(self, observations: list[dict], max_gap_seconds: float) -> list[dict]:
        clusters: list[list[dict]] = []
        for point in sorted(observations, key=lambda item: item["video_second"] - item["clock_seconds"]):
            offset = point["video_second"] - point["clock_seconds"]
            closest = min(clusters,
                          key=lambda cluster: abs(offset - statistics.median(
                              item["video_second"] - item["clock_seconds"] for item in cluster)),
                          default=None)
            if (closest is not None and abs(offset - statistics.median(
                    item["video_second"] - item["clock_seconds"] for item in closest))
                    <= self.offset_tolerance_seconds):
                closest.append(point)
            else:
                clusters.append([point])
        segments = []
        for cluster in clusters:
            chronological = sorted(cluster, key=lambda item: item["video_second"])
            spans: list[list[dict]] = []
            for point in chronological:
                if not spans or point["video_second"] - spans[-1][-1]["video_second"] > max_gap_seconds:
                    spans.append([point])
                else:
                    spans[-1].append(point)
            for span in spans:
                if len(span) < self.min_samples:
                    continue
                if any(b["clock_seconds"] < a["clock_seconds"] for a, b in zip(span, span[1:])):
                    continue
                offsets = [item["video_second"] - item["clock_seconds"] for item in span]
                offset = float(statistics.median(offsets))
                segments.append({
                    "video_start": span[0]["video_second"],
                    "video_end": span[-1]["video_second"],
                    "clock_start": span[0]["clock"],
                    "clock_end": span[-1]["clock"],
                    "offset_seconds": round(offset, 3),
                    "reference_count": len(span),
                    "max_offset_residual_seconds": round(max(abs(value - offset) for value in offsets), 3),
                    "observations": span,
                })
        segments.sort(key=lambda segment: segment["video_start"])
        return segments

    @staticmethod
    def _stability_score(boxes: list[dict]) -> float:
        if len(boxes) < 2:
            return 0.0
        centers = [(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2) for box in boxes]
        center_x = statistics.mean(point[0] for point in centers)
        center_y = statistics.mean(point[1] for point in centers)
        spread = statistics.mean(math.hypot(x - center_x, y - center_y) for x, y in centers)
        return max(0.0, min(1.0, 1 - spread / 0.035))

    @staticmethod
    def _median_roi(observations: list[dict], samples: list[tuple[float, np.ndarray]]) -> dict | None:
        if not observations:
            return None
        reference_width, reference_height = samples[0][1].shape[1], samples[0][1].shape[0]
        normalized = []
        for observation in observations:
            roi = observation["roi"]
            width, height = samples[observation["sample_index"]][1].shape[1], samples[observation["sample_index"]][1].shape[0]
            normalized.append((roi["x"] / width, roi["y"] / height,
                               roi["width"] / width, roi["height"] / height))
        values = [statistics.median(item[i] for item in normalized) for i in range(4)]
        return {"x": values[0], "y": values[1], "width": values[2], "height": values[3],
                "reference_width": reference_width, "reference_height": reference_height}

    @staticmethod
    def _pixel_roi(relative_roi: dict | None, resolution: dict) -> dict | None:
        if not relative_roi:
            return None
        width, height = resolution["width"], resolution["height"]
        x = max(0, min(width - 1, int(round(relative_roi["x"] * width))))
        y = max(0, min(height - 1, int(round(relative_roi["y"] * height))))
        roi_width = max(1, min(width - x, int(round(relative_roi["width"] * width))))
        roi_height = max(1, min(height - y, int(round(relative_roi["height"] * height))))
        return ROI(x, y, roi_width, roi_height).as_dict()

    @staticmethod
    def _merge_track_diagnostics(first: list[dict], second: list[dict]) -> list[dict]:
        merged = first + second
        for index, track in enumerate(merged, 1):
            track["track_id"] = f"T{index:03d}"
        return merged

    def _empty_result_with_tracks(self, samples: list[tuple[float, np.ndarray]], started: float,
                                  reason: str, tracks: list[dict]) -> dict:
        result = self._empty_result(samples, started, reason)
        result["candidate_tracks"] = tracks[:self.max_tracks]
        return result

    def write_visual_report(self, result: dict, samples: list[tuple[float, np.ndarray]],
                            output_dir: Path) -> tuple[Path, Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        json_path = output_dir / "detection.json"
        image_path = output_dir / "detection_report.jpg"
        json_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

        if not samples:
            return json_path, image_path
        count = min(6, len(samples))
        indices = sorted(set(round(i * (len(samples) - 1) / max(1, count - 1)) for i in range(count)))
        cards = []
        candidates = result.get("candidate_tracks", [])[:5]
        chosen_id = next((track["track_id"] for track in candidates if track.get("decision") == "selected"), None)
        selected_norm = result.get("relative_roi") if result.get("status") == "confirmed" else None

        for sample_index in indices:
            video_second, frame = samples[sample_index]
            canvas = frame.copy()
            frame_height, frame_width = canvas.shape[:2]
            has_selected_reading = False
            for track in candidates:
                has_reading = any(entry["sample_index"] == sample_index
                                  for entry in track.get("observations", []))
                if track.get("decision") == "selected":
                    has_selected_reading = has_reading
                    if not has_reading:
                        continue
                if not has_reading:
                    # Draw rejected region tracks at their normalized proposal region for context.
                    normalized = track.get("region_normalized")
                    if not normalized or track.get("proposal_support", 0) < 2:
                        continue
                normalized = track.get("roi_normalized") or track.get("region_normalized")
                if not normalized:
                    continue
                x = int(normalized["x"] * frame_width)
                y = int(normalized["y"] * frame_height)
                w = int(normalized["width"] * frame_width)
                h = int(normalized["height"] * frame_height)
                color = (40, 220, 40) if track.get("decision") == "selected" else (0, 150, 255)
                cv2.rectangle(canvas, (x, y), (x + w, y + h), color, 2)
                reason_labels = {
                    "region_seen_in_too_few_samples": "poca presencia",
                    "no_unambiguous_clock_ocr": "sin OCR valido",
                    "readings_not_temporally_coherent": "tiempo incoherente",
                    "lower_confidence_than_selected_region": "menor confianza",
                    "multiple_regions_have_similar_temporal_evidence": "ambiguo",
                }
                reason = reason_labels.get(track.get("reason"), track.get("decision", "candidato"))
                cv2.putText(canvas, f"{track['track_id']} {reason}",
                            (x, max(16, y - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.48, color, 1, cv2.LINE_AA)
            if selected_norm and has_selected_reading:
                x = int(selected_norm["x"] * frame_width)
                y = int(selected_norm["y"] * frame_height)
                w = int(selected_norm["width"] * frame_width)
                h = int(selected_norm["height"] * frame_height)
                cv2.rectangle(canvas, (x, y), (x + w, y + h), (30, 255, 30), 3)
            resized = cv2.resize(canvas, (480, 270), interpolation=cv2.INTER_AREA)
            card = cv2.copyMakeBorder(resized, 0, 30, 0, 0, cv2.BORDER_CONSTANT, value=(32, 32, 32))
            reading = chosen_id if has_selected_reading else "sin evidencia OCR"
            cv2.putText(card, f"{clock_text(video_second)} s | {reading}", (8, 290),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.48, (255, 255, 255), 1, cv2.LINE_AA)
            cards.append(card)

        rows = []
        for start in range(0, len(cards), 2):
            row = cards[start:start + 2]
            if len(row) == 1:
                row.append(np.full_like(row[0], 32))
            rows.append(cv2.hconcat(row))
        montage = cv2.vconcat(rows)
        header = np.full((120, montage.shape[1], 3), 28, dtype=np.uint8)
        cv2.putText(header, f"ClockDetector: {result['status']} | confianza {result['confidence']:.2f}",
                    (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2, cv2.LINE_AA)
        cv2.putText(header, f"Razon: {result['reason']} | busqueda: {result['search_stage']}",
                    (12, 58), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (220, 220, 220), 1, cv2.LINE_AA)
        cv2.putText(header, "Verde: region elegida | Naranja: candidatos rechazados o alternativos",
                    (12, 88), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (220, 220, 220), 1, cv2.LINE_AA)
        cv2.imwrite(str(image_path), cv2.vconcat([header, montage]))
        return json_path, image_path
