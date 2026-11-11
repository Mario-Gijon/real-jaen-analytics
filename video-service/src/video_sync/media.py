"""Probe videos and extract selected frames, without loading a whole MP4 in memory."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

import cv2
import numpy as np


class MediaError(RuntimeError):
    """Video inspection or extraction could not be completed."""


def _run(command: list[str], *, binary_input: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(command, input=binary_input, capture_output=True, check=True, timeout=45)
    except FileNotFoundError as exc:
        raise MediaError(f"No se encuentra {command[0]}. Instala las herramientas externas indicadas en README.") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode("utf-8", errors="replace")[-1000:]
        raise MediaError(f"Fallo ejecutando {command[0]}: {detail}") from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaError(f"Tiempo agotado ejecutando {command[0]}.") from exc


def probe(video_path: Path) -> dict[str, Any]:
    path = video_path.expanduser().resolve()
    if not path.is_file():
        raise MediaError(f"No existe el vídeo: {path}")
    command = ["ffprobe", "-v", "error", "-show_entries",
               "format=duration,size:stream=index,codec_type,codec_name,width,height,r_frame_rate,avg_frame_rate",
               "-of", "json", str(path)]
    output = json.loads(_run(command).stdout)
    video_stream = next((s for s in output.get("streams", []) if s.get("codec_type") == "video"), None)
    if not video_stream:
        raise MediaError("No se ha encontrado ninguna pista de vídeo.")
    duration = float(output.get("format", {}).get("duration", 0))
    if duration <= 0:
        raise MediaError("ffprobe no proporciona una duración válida.")
    return {
        "path": str(path), "duration_seconds": duration,
        "file_bytes": int(output.get("format", {}).get("size", 0)),
        "width": video_stream.get("width"), "height": video_stream.get("height"),
        "codec": video_stream.get("codec_name"),
        "avg_frame_rate": video_stream.get("avg_frame_rate"),
    }


def extract_frame(video_path: Path, at_seconds: float) -> np.ndarray:
    if at_seconds < 0:
        raise MediaError("El instante solicitado debe ser >= 0.")
    # Input-side seeking is fast; FFmpeg still decodes up to the requested time.
    # Output is one PNG frame in RAM. Nothing is written to the video's directory.
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
               "-ss", f"{at_seconds:.3f}", "-i", str(video_path.expanduser().resolve()),
               "-map", "0:v:0", "-frames:v", "1", "-f", "image2pipe",
               "-vcodec", "png", "pipe:1"]
    data = _run(command).stdout
    frame = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise MediaError(f"No se pudo extraer fotograma en t={at_seconds:.2f}s.")
    return frame


def extract_frame_with_timestamp(video_path: Path, at_seconds: float) -> tuple[np.ndarray, float | None]:
    """Extract one frame and read its presentation timestamp from FFmpeg showinfo."""
    if at_seconds < 0:
        raise MediaError("El instante solicitado debe ser >= 0.")
    command = ["ffmpeg", "-hide_banner", "-loglevel", "info", "-nostdin",
               "-ss", f"{at_seconds:.3f}", "-copyts", "-i",
               str(video_path.expanduser().resolve()), "-map", "0:v:0",
               "-vf", "showinfo", "-frames:v", "1", "-f", "image2pipe",
               "-vcodec", "png", "pipe:1"]
    completed = _run(command)
    frame = cv2.imdecode(np.frombuffer(completed.stdout, dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise MediaError(f"No se pudo extraer fotograma en t={at_seconds:.2f}s.")
    match = re.search(rb"pts_time:([-+]?(?:\d+(?:\.\d*)?|\.\d+))", completed.stderr)
    actual = float(match.group(1)) if match else None
    return frame, actual


def save_jpg(path: Path, frame: np.ndarray, quality: int = 91) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ok = cv2.imwrite(str(path), frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise MediaError(f"No se pudo guardar: {path}")
