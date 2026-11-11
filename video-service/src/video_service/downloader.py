"""Bounded YouTube download into an isolated job directory."""
from __future__ import annotations

import errno
import importlib.util
import logging
from pathlib import Path
import shutil
import time
from typing import Any

from .youtube import youtube_video_id


DEFAULT_MAX_BYTES = 2_500_000_000
DEFAULT_TIMEOUT_SECONDS = 60 * 60


class DownloadError(RuntimeError):
    def __init__(self, message: str, *, error_code: str = "download_failed",
                 technical_type: str | None = None, exit_code: int | None = None):
        super().__init__(message)
        self.error_code = error_code
        self.technical_type = technical_type
        self.exit_code = exit_code


ERROR_MESSAGES = {
    "video_unavailable": "YouTube indicó que el vídeo no está disponible para este método de descarga. Comprueba el acceso al enlace desde YouTube.",
    "network_error": "No se pudo conectar con YouTube por un problema de red o DNS. Inténtalo de nuevo más tarde.",
    "extractor_unavailable": "El contenedor no tiene compatibles todos los componentes de yt-dlp para YouTube. Contacta con soporte.",
    "format_unavailable": "YouTube no ofrece un formato compatible con esta descarga para el vídeo.",
    "ffmpeg_error": "FFmpeg no pudo combinar o preparar los formatos de vídeo disponibles.",
    "storage_error": "Video Service no pudo escribir en su almacenamiento temporal. Comprueba el espacio y los permisos.",
    "download_failed": "La descarga de YouTube falló por un error no clasificado. Inténtalo de nuevo más tarde.",
}


def classify_ytdlp_failure(detail: str, exception_type: str = "") -> str:
    """Map extractor diagnostics to stable, non-sensitive UI categories."""
    text = f"{exception_type} {detail}".casefold()
    if any(term in text for term in (
        "no supported javascript runtime", "javascript runtime could not be found",
        "yt-dlp-ejs", "ejs challenge", "unsupported deno", "unsupported yt-dlp",
    )):
        return "extractor_unavailable"
    if any(term in text for term in (
        "temporary failure in name resolution", "failed to resolve", "name or service not known",
        "getaddrinfo failed", "network is unreachable", "connection refused", "connection reset",
        "timed out", "timeout", "transporterror", "sslerror", "unable to download api page",
    )):
        return "network_error"
    if any(term in text for term in (
        "requested format is not available", "requested format not available", "no video formats found",
        "requested formats are incompatible", "format is not available",
    )):
        return "format_unavailable"
    if any(term in text for term in (
        "postprocessingerror", "post-processing", "postprocessing", "ffmpeg", "ffprobe",
    )):
        return "ffmpeg_error"
    if any(term in text for term in (
        "private video", "this video is private", "video unavailable", "this video is not available",
        "sign in to confirm", "members-only", "member-only", "video has been removed",
    )):
        return "video_unavailable"
    if any(term in text for term in (
        "no space left on device", "permission denied", "read-only file system", "disk quota exceeded",
    )):
        return "storage_error"
    return "download_failed"


class _YtDlpLogger:
    """Capture extractor output for classification; never persist raw diagnostics."""
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def debug(self, _message: str) -> None:
        pass

    def warning(self, message: str) -> None:
        self.warnings.append(str(message))

    def error(self, message: str) -> None:
        self.errors.append(str(message))


def _failure_details(exc: BaseException, logger: _YtDlpLogger) -> str:
    parts = [str(getattr(exc, "msg", exc))]
    parts.extend(logger.errors)
    parts.extend(logger.warnings)
    cause = exc.__cause__ or exc.__context__
    if cause is not None:
        parts.append(str(cause))
    return " ".join(part for part in parts if part)


def _download_error(code: str, *, technical_type: str | None = None,
                    exit_code: int | None = None) -> DownloadError:
    return DownloadError(ERROR_MESSAGES.get(code, ERROR_MESSAGES["download_failed"]),
                         error_code=code, technical_type=technical_type, exit_code=exit_code)


def download_youtube_video(source_url: str, video_id: str, job_dir: Path, *,
                           max_bytes: int = DEFAULT_MAX_BYTES,
                           timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> Path:
    # Never pass a caller-controlled host/path to yt-dlp. The accepted URL is
    # reduced to its validated ID and rebuilt as a canonical YouTube watch URL.
    if youtube_video_id(source_url) != video_id:
        raise DownloadError("La URL y el identificador de YouTube no coinciden.")
    canonical_url = f"https://www.youtube.com/watch?v={video_id}"
    job_dir = job_dir.resolve()
    job_dir.mkdir(parents=True, exist_ok=False)
    try:
        import yt_dlp
    except ImportError as exc:
        raise _download_error("extractor_unavailable", technical_type=type(exc).__name__) from exc
    if shutil.which("deno") is None or importlib.util.find_spec("yt_dlp_ejs") is None:
        raise _download_error("extractor_unavailable", technical_type="JavaScriptRuntimeUnavailable")

    started_at = time.monotonic()

    def size_guard(data: dict) -> None:
        if time.monotonic() - started_at > timeout_seconds:
            raise DownloadError("La descarga superó el tiempo máximo de una hora.")
        if data.get("status") == "downloading" and int(data.get("downloaded_bytes") or 0) > max_bytes:
            raise DownloadError("La descarga supera el tamaño máximo permitido.")

    captured = _YtDlpLogger()
    options: dict[str, Any] = {
        "format": "bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/best[ext=mp4][height<=1080]",
        "merge_output_format": "mp4",
        "outtmpl": str(job_dir / "broadcast.%(ext)s"),
        "max_filesize": max_bytes,
        "noplaylist": True,
        "playlist_items": "1",
        "nooverwrites": True,
        "continuedl": False,
        "nopart": True,
        "ignoreconfig": True,
        "cachedir": False,
        "quiet": True,
        "no_warnings": False,
        "no_color": True,
        "logger": captured,
        "socket_timeout": 20,
        "retries": 2,
        "fragment_retries": 2,
        "file_access_retries": 1,
        "progress_hooks": [size_guard],
    }
    try:
        with yt_dlp.YoutubeDL(options) as downloader:
            result = downloader.download([canonical_url])
        if result != 0:
            details = " ".join(captured.errors + captured.warnings)
            code = classify_ytdlp_failure(details, "yt-dlp exit code")
            raise _download_error(code, technical_type="yt-dlp exit code", exit_code=int(result))
        candidates = [path for path in job_dir.iterdir() if path.is_file() and path.suffix.lower() == ".mp4"]
        if len(candidates) != 1:
            raise _download_error("format_unavailable", technical_type="OutputFormatMismatch")
        video_path = candidates[0].resolve()
        if video_path.parent != job_dir or video_path.stat().st_size > max_bytes:
            raise DownloadError("El archivo descargado no cumple los límites establecidos.")
        return video_path
    except DownloadError:
        raise
    except yt_dlp.utils.DownloadError as exc:
        code = classify_ytdlp_failure(_failure_details(exc, captured), type(exc).__name__)
        technical_type = f"{type(exc).__module__}.{type(exc).__name__}"
        raise _download_error(code, technical_type=technical_type) from exc
    except OSError as exc:
        details = _failure_details(exc, captured)
        code = classify_ytdlp_failure(details, type(exc).__name__)
        if exc.errno in (errno.ENOSPC, errno.EACCES, errno.EPERM, errno.EROFS, errno.EDQUOT):
            code = "storage_error"
        technical_type = f"{type(exc).__module__}.{type(exc).__name__}"
        raise _download_error(code, technical_type=technical_type) from exc
    except Exception as exc:
        code = classify_ytdlp_failure(_failure_details(exc, captured), type(exc).__name__)
        technical_type = f"{type(exc).__module__}.{type(exc).__name__}"
        raise _download_error(code, technical_type=technical_type) from exc
