"""Read broadcast clocks from a manually selected pixel rectangle."""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass

import cv2
import numpy as np


CLOCK_PATTERN = re.compile(r"(\d{1,3}):(\d{2})")


@dataclass(frozen=True)
class ROI:
    x: int
    y: int
    width: int
    height: int

    @classmethod
    def parse(cls, text: str) -> "ROI":
        try:
            data = [int(x.strip()) for x in text.split(",")]
        except ValueError as exc:
            raise ValueError("El ROI debe tener cuatro enteros: x,y,ancho,alto") from exc
        if len(data) != 4 or min(data) < 0 or data[2] < 1 or data[3] < 1:
            raise ValueError("ROI no válido. Ejemplo: --roi 60,25,160,55")
        return cls(*data)

    def crop(self, frame: np.ndarray) -> np.ndarray:
        h, w = frame.shape[:2]
        if self.x + self.width > w or self.y + self.height > h:
            raise ValueError(f"ROI fuera de los límites del fotograma ({w}x{h})")
        return frame[self.y:self.y+self.height, self.x:self.x+self.width].copy()

    def as_dict(self) -> dict[str, int]:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}


def parse_clock(text: str) -> int | None:
    """Parse one complete MM:SS reading; reject surrounding OCR artifacts."""
    # Whitespace around the colon is a layout variation. Other characters are
    # not stripped: e.g. ``01:45 1`` must not become a plausible clock by luck.
    normalized = re.sub(r"\s*:\s*", ":", text.strip())
    match = CLOCK_PATTERN.fullmatch(normalized)
    if match is None:
        return None
    minutes, seconds = int(match.group(1)), int(match.group(2))
    if minutes > 120 or seconds > 59:
        return None
    return minutes * 60 + seconds


def clock_text(seconds: float) -> str:
    sec = int(round(seconds))
    return f"{sec // 60:02d}:{sec % 60:02d}"


def preprocess(crop: np.ndarray) -> dict[str, np.ndarray]:
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    enlarged = cv2.resize(gray, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
    # A gentle local contrast pass keeps the thin digits clear without
    # amplifying the rounded plaque edge into a false extra glyph.
    clahe_soft = cv2.createCLAHE(clipLimit=1.0, tileGridSize=(4, 4)).apply(enlarged)
    _, bw = cv2.threshold(enlarged, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return {"gray": enlarged, "bw": bw, "clahe_soft": clahe_soft}


def marker_evidence(crop: np.ndarray) -> dict[str, float | int | bool]:
    """Detect this broadcast's bright clock plaque independently of OCR text."""
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    bright_rows = (gray >= 205).mean(axis=1) >= 0.65
    center_start = int(gray.shape[1] * 0.15)
    center_end = max(center_start + 1, int(gray.shape[1] * 0.85))
    center = gray[:, center_start:center_end]
    dark_fraction = float((center <= 100).mean())
    bright_row_count = int(bright_rows.sum())
    # The clock is dark lettering on a bright horizontal plaque. Requiring
    # both avoids calling a uniformly bright field/sky a visible scoreboard.
    visible = (bright_row_count >= max(8, int(np.ceil(gray.shape[0] * 0.30)))
               and dark_fraction >= 0.07)
    return {
        "marker_visible": bool(visible),
        "bright_row_count": bright_row_count,
        "central_dark_pixel_fraction": round(dark_fraction, 4),
    }


def tesseract_read(image: np.ndarray) -> str:
    ok, png = cv2.imencode(".png", image)
    if not ok:
        raise RuntimeError("No se pudo codificar la imagen para Tesseract.")
    command = ["tesseract", "stdin", "stdout", "--psm", "7", "--oem", "3",
               "-c", "tessedit_char_whitelist=0123456789:"]
    try:
        completed = subprocess.run(command, input=png.tobytes(), capture_output=True,
                                   check=True, timeout=15)
    except FileNotFoundError as exc:
        raise RuntimeError("Falta el ejecutable tesseract.") from exc
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Tesseract falló: {exc}") from exc
    return completed.stdout.decode("utf-8", errors="replace").strip()


def read_clock_crop(cropped: np.ndarray, *, min_votes: int = 2,
                    require_marker_evidence: bool = True) -> dict:
    if min_votes not in (1, 2, 3):
        raise ValueError("min_votes debe estar entre 1 y 3")
    variants = preprocess(cropped)
    evidence = marker_evidence(cropped) if require_marker_evidence else None
    candidates = []
    for label, image in variants.items():
        text = tesseract_read(image)
        value = parse_clock(text)
        candidates.append({"variant": label, "text": text, "seconds": value})
    values: dict[int, int] = {}
    for candidate in candidates:
        value = candidate["seconds"]
        if value is not None:
            values[value] = values.get(value, 0) + 1

    candidate_seconds = next(iter(values)) if len(values) == 1 else None
    votes = values.get(candidate_seconds, 0) if candidate_seconds is not None else 0
    reason = None
    if evidence is not None and not evidence["marker_visible"]:
        status = "marker_not_visible"
        reason = "no_bright_clock_plaque_with_dark_glyphs"
        result = None
    elif len(values) > 1:
        status = "ambiguous_invalid"
        reason = "ocr_variants_disagree"
        result = None
    elif candidate_seconds is None:
        status = "ambiguous_invalid"
        reason = "no_complete_valid_clock_candidate"
        result = None
    elif votes < min_votes:
        status = "ambiguous_invalid"
        reason = "insufficient_ocr_consensus"
        result = None
    else:
        status = "valid_reading"
        result = candidate_seconds

    return {
        "clock_seconds": result,
        "clock": clock_text(result) if result is not None else None,
        "votes": votes,
        "candidate_clock_seconds": candidate_seconds,
        "status": status,
        "status_reason": reason,
        "marker_evidence": evidence,
        "variants": candidates,
    }


def read_clock(frame: np.ndarray, roi: ROI, *, min_votes: int = 2) -> dict:
    return read_clock_crop(roi.crop(frame), min_votes=min_votes,
                           require_marker_evidence=True)
