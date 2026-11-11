"""Strict, side-effect-free validation of public YouTube watch URLs."""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit


VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
YOUTUBE_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com",
    "youtu.be", "www.youtu.be", "youtube-nocookie.com", "www.youtube-nocookie.com",
}


class InvalidYouTubeUrl(ValueError):
    """The supplied URL is not a recognized public YouTube video URL."""


def youtube_video_id(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 2048:
        raise InvalidYouTubeUrl("Pega una URL pública de YouTube válida.")
    try:
        parsed = urlsplit(value.strip())
        host = (parsed.hostname or "").lower().rstrip(".")
        port = parsed.port
    except ValueError as exc:
        raise InvalidYouTubeUrl("La URL de YouTube no es válida.") from exc
    if (parsed.scheme != "https" or host not in YOUTUBE_HOSTS or port not in (None, 443)
            or parsed.username is not None or parsed.password is not None):
        raise InvalidYouTubeUrl("Solo se admiten enlaces HTTPS reconocidos de YouTube.")

    video_id = None
    if host in {"youtu.be", "www.youtu.be"}:
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) == 1:
            video_id = parts[0]
    elif parsed.path == "/watch":
        values = parse_qs(parsed.query).get("v", [])
        if len(values) == 1:
            video_id = values[0]
    else:
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) == 2 and parts[0] in {"embed", "live", "shorts"}:
            video_id = parts[1]

    if video_id is None or VIDEO_ID_RE.fullmatch(video_id) is None:
        raise InvalidYouTubeUrl("La URL no contiene un identificador de vídeo de YouTube válido.")
    return video_id
