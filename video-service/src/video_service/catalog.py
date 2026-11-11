"""Explicit, read-only video registry and calibration loader."""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, parse_qsl


VIDEO_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")
ALLOWED_ORIGINS = {None, "youtube", "local_file", "unknown"}
ALLOWED_SYNC_STATES = {"calibrated", "uncertain", "not_calibrated"}
ALLOWED_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "youtu.be", "www.youtube-nocookie.com"}
SENSITIVE_QUERY_KEYS = {"token", "access_token", "api_key", "key", "auth", "signature", "sig", "secret"}


class CatalogError(ValueError):
    """The configured video registry or one of its calibrations is invalid."""


@dataclass(frozen=True)
class VideoRecord:
    video_id: str
    display_name: str
    match_ref: str
    origin: str | None
    source_url: str | None
    calibration_file: str | None
    synchronization_status: str
    calibration: dict[str, Any] | None


def _safe_url(value: Any, index: int, origin: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise CatalogError(f"videos[{index}].source_url debe ser texto o null.")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise CatalogError(f"videos[{index}].source_url debe ser una URL HTTP(S).")
    if parsed.username is not None or parsed.password is not None:
        raise CatalogError(f"videos[{index}].source_url no puede contener credenciales.")
    if origin != "youtube" or parsed.hostname.lower().rstrip(".") not in ALLOWED_YOUTUBE_HOSTS:
        raise CatalogError(f"videos[{index}].source_url solo puede apuntar a YouTube.")
    if any(key.lower() in SENSITIVE_QUERY_KEYS for key, _ in parse_qsl(parsed.query)):
        raise CatalogError(f"videos[{index}].source_url contiene parámetros sensibles.")
    return value


def _load_calibration(data_dir: Path, relative_name: str, index: int) -> dict[str, Any]:
    relative = Path(relative_name)
    if relative.is_absolute():
        raise CatalogError(f"videos[{index}].calibration_file debe ser relativo a VIDEO_SERVICE_DATA_DIR.")
    root = data_dir.resolve()
    resolved = (root / relative).resolve()
    if not resolved.is_relative_to(root):
        raise CatalogError(f"videos[{index}].calibration_file sale del directorio de datos.")
    try:
        document = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CatalogError(f"No se pudo cargar una calibración registrada (entrada {index}).") from exc
    if not isinstance(document, dict):
        raise CatalogError(f"La calibración registrada en videos[{index}] no es un objeto JSON.")
    if not isinstance(document.get("segments"), list):
        raise CatalogError(f"La calibración registrada en videos[{index}] no contiene segments.")
    for segment_index, segment in enumerate(document["segments"]):
        if not isinstance(segment, dict):
            raise CatalogError(f"segments[{segment_index}] de la calibración {index} debe ser un objeto.")
        for key in (
            "period", "game_clock_start", "game_clock_end", "offset_seconds",
            "reference_count", "max_residual_seconds",
        ):
            if key not in segment:
                raise CatalogError(f"segments[{segment_index}] de la calibración {index} no contiene {key}.")
        if not isinstance(segment["period"], str) or segment["period"] not in {"first", "second", "uncertain"}:
            raise CatalogError(f"segments[{segment_index}] de la calibración {index} tiene un periodo inválido.")
        for key in (
            "game_clock_start", "game_clock_end", "offset_seconds",
            "reference_count", "max_residual_seconds",
        ):
            value = segment[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise CatalogError(f"segments[{segment_index}].{key} debe ser numérico.")
        if isinstance(segment["reference_count"], float) or segment["reference_count"] < 1:
            raise CatalogError(f"segments[{segment_index}].reference_count debe ser entero positivo.")
        if segment["max_residual_seconds"] < 0:
            raise CatalogError(f"segments[{segment_index}].max_residual_seconds no puede ser negativo.")
        if segment["game_clock_start"] > segment["game_clock_end"]:
            raise CatalogError(f"segments[{segment_index}] de la calibración {index} tiene un rango invertido.")
    return document


class VideoCatalog:
    """In-memory snapshot of an explicit JSON manifest and its calibrations."""

    def __init__(self, records: list[VideoRecord]):
        self._records = {record.video_id: record for record in records}

    @classmethod
    def load(cls, data_dir: Path, manifest_name: str = "videos.json") -> "VideoCatalog":
        root = data_dir.expanduser().resolve()
        manifest_path = Path(manifest_name)
        if manifest_path.is_absolute() or ".." in manifest_path.parts:
            raise CatalogError("VIDEO_SERVICE_MANIFEST debe ser relativo a VIDEO_SERVICE_DATA_DIR.")
        resolved_manifest = (root / manifest_path).resolve()
        if not resolved_manifest.is_relative_to(root):
            raise CatalogError("VIDEO_SERVICE_MANIFEST sale del directorio de datos.")
        try:
            document = json.loads(resolved_manifest.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise CatalogError("No se pudo cargar el manifiesto de vídeos configurado.") from exc
        if not isinstance(document, dict) or document.get("schema_version") != 1:
            raise CatalogError("El manifiesto debe declarar schema_version=1.")
        items = document.get("videos")
        if not isinstance(items, list):
            raise CatalogError("El manifiesto debe contener videos como lista.")

        records: list[VideoRecord] = []
        seen: set[str] = set()
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                raise CatalogError(f"videos[{index}] debe ser un objeto.")
            video_id = item.get("video_id")
            if not isinstance(video_id, str) or not VIDEO_ID_RE.fullmatch(video_id):
                raise CatalogError(f"videos[{index}].video_id no es un identificador válido.")
            if video_id in seen:
                raise CatalogError(f"video_id duplicado en el manifiesto: {video_id}.")
            seen.add(video_id)
            display_name = item.get("display_name")
            match_ref = item.get("match_ref")
            if not isinstance(display_name, str) or not display_name.strip():
                raise CatalogError(f"videos[{index}].display_name es obligatorio.")
            if isinstance(match_ref, bool) or not isinstance(match_ref, (str, int)) or not str(match_ref).strip():
                raise CatalogError(f"videos[{index}].match_ref es obligatorio.")
            match_ref = str(match_ref).strip()
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", match_ref):
                raise CatalogError(f"videos[{index}].match_ref no es una referencia opaca válida.")
            origin = item.get("origin")
            if not (origin is None or isinstance(origin, str) and origin in ALLOWED_ORIGINS):
                raise CatalogError(f"videos[{index}].origin no pertenece a los valores admitidos.")
            source_url = _safe_url(item.get("source_url"), index, origin)
            sync_state = item.get("synchronization_status", "not_calibrated")
            if not isinstance(sync_state, str) or sync_state not in ALLOWED_SYNC_STATES:
                raise CatalogError(f"videos[{index}].synchronization_status no está admitido.")
            calibration_name = item.get("calibration_file")
            calibration = None
            if calibration_name is not None:
                if not isinstance(calibration_name, str) or not calibration_name.strip():
                    raise CatalogError(f"videos[{index}].calibration_file debe ser texto o null.")
                calibration = _load_calibration(root, calibration_name, index)
            if sync_state == "calibrated" and calibration is None:
                raise CatalogError(f"videos[{index}] figura calibrado pero no tiene archivo de calibración.")
            if sync_state == "calibrated" and calibration.get("status") != "calibrated":
                raise CatalogError(f"videos[{index}] figura calibrado pero el resultado no está calibrado.")
            if sync_state == "calibrated" and (
                isinstance(calibration.get("version"), bool)
                or not isinstance(calibration.get("version"), int)
                or calibration["version"] < 1
            ):
                raise CatalogError(f"videos[{index}] figura calibrado pero no tiene versión válida.")
            records.append(VideoRecord(
                video_id=video_id,
                display_name=display_name,
                match_ref=match_ref,
                origin=origin,
                source_url=source_url,
                calibration_file=calibration_name,
                synchronization_status=sync_state,
                calibration=calibration,
            ))
        return cls(records)

    def list(self) -> list[VideoRecord]:
        return [self._records[key] for key in sorted(self._records)]

    def get(self, video_id: str) -> VideoRecord | None:
        return self._records.get(video_id)


def public_video(record: VideoRecord) -> dict[str, Any]:
    """Return registry fields only; never disclose calibration or server paths."""
    return {
        "video_id": record.video_id,
        "display_name": record.display_name,
        "match_ref": record.match_ref,
        "origin": record.origin,
        "source_url": record.source_url,
        "synchronization_status": record.synchronization_status,
    }
