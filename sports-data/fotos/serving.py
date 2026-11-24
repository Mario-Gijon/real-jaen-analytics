"""Read side of the photo store for the HTTP API: source selection, batch lookup, versioned URLs, verified bytes.

Read-only: the index is opened with SQLite `mode=ro` and nothing is created. A missing store directory or index is the
normal "no photos yet" state; an index that cannot be read, a referenced blob that is missing or whose bytes do not
match its SHA-256 is a store fault (PhotoStoreFault), never silently reported as absence by serve().

Selection: among a player's active photos, the first source in an explicit priority list
(SPORTS_DATA_PHOTO_SOURCES, comma separated, `df11` by default). Sources not listed are never served. There is at most
one active photo per (player, source), so the choice is fully determined by the list.

The version in a photo URL is the full SHA-256 of the served bytes: a new photo has a new URL, and an old URL keeps
answering 404 instead of returning other bytes. URLs are relative to this API (`/api/v1/players/{id}/photo/{sha}`); the
dashboard's BFF has to expose them to the browser (Sports Data is on an internal network).
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Iterable

from . import bundle, images

DEFAULT_SOURCES = ('df11',)
VERSION = re.compile(r'^[0-9a-f]{64}$')
PLAYER_ID = re.compile(r'^[1-9][0-9]{0,11}$')
BATCH = 500  # well below SQLite's bound-parameter limit


class PhotoStoreFault(RuntimeError):
    """The photo store exists but cannot be trusted (unreadable index, missing or altered blob)."""


@dataclass(frozen=True)
class Selected:
    player_id: int
    source_id: str
    sha256: str
    media_type: str

    @property
    def url(self) -> str:
        return photo_url(self.player_id, self.sha256)


def photo_url(player_id: int, sha256: str) -> str:
    return f'/api/v1/players/{player_id}/photo/{sha256}'


def source_priority(environ=None) -> tuple[str, ...]:
    raw = (environ if environ is not None else os.environ).get('SPORTS_DATA_PHOTO_SOURCES')
    if raw is None:
        return DEFAULT_SOURCES
    sources = tuple(part.strip() for part in raw.split(','))
    if not sources or any(not bundle.LABEL.match(s) for s in sources) or len(set(sources)) != len(sources):
        raise ValueError('SPORTS_DATA_PHOTO_SOURCES debe ser una lista de fuentes separadas por comas, sin repetir')
    return sources


class PhotoReader:
    def __init__(self, root: Path | str, sources: Iterable[str] = DEFAULT_SOURCES):
        self.root = Path(root)
        self.db_path = self.root / 'fotos.sqlite'
        self.sources = tuple(sources)
        if not self.sources:
            raise ValueError('Se necesita al menos una fuente de fotografías')

    def _connect(self) -> sqlite3.Connection | None:
        if self.root.is_symlink() or self.db_path.is_symlink():
            raise PhotoStoreFault('ruta del almacén de fotografías no segura')
        if not self.db_path.exists():
            return None  # no store yet: no photos, nothing created
        try:
            connection = sqlite3.connect(f'{self.db_path.resolve().as_uri()}?mode=ro', uri=True, timeout=5)
        except sqlite3.Error as error:
            raise PhotoStoreFault(f'índice de fotografías ilegible: {type(error).__name__}') from None
        return connection

    def select(self, player_ids: Iterable[int]) -> dict[int, Selected]:
        """The photo to serve for each player that has one, in one query per BATCH players."""
        wanted = sorted({p for p in player_ids if type(p) is int and p > 0})
        connection = self._connect() if wanted else None
        if connection is None:
            return {}
        rank = {source: n for n, source in enumerate(self.sources)}
        chosen: dict[int, tuple[int, Selected]] = {}
        try:
            with closing(connection):
                for start in range(0, len(wanted), BATCH):
                    chunk = wanted[start:start + BATCH]
                    rows = connection.execute(
                        'SELECT p.player_id, p.source_id, p.sha256, b.media_type FROM photo p '
                        'JOIN blob b ON b.sha256 = p.sha256 '
                        f"WHERE p.status = 'active' AND p.player_id IN ({','.join('?' * len(chunk))}) "
                        f"AND p.source_id IN ({','.join('?' * len(self.sources))})",
                        (*chunk, *self.sources)).fetchall()
                    for player_id, source_id, sha256, media_type in rows:
                        if media_type not in images.FORMATS or not VERSION.match(sha256):
                            raise PhotoStoreFault(f'fila de fotografía no válida para {player_id}')
                        candidate = (rank[source_id], Selected(player_id, source_id, sha256, media_type))
                        if player_id not in chosen or candidate[0] < chosen[player_id][0]:
                            chosen[player_id] = candidate
        except sqlite3.Error as error:
            raise PhotoStoreFault(f'índice de fotografías ilegible: {type(error).__name__}') from None
        return {player_id: selected for player_id, (_, selected) in chosen.items()}

    def urls(self, player_ids: Iterable[int]) -> dict[int, str]:
        return {player_id: s.url for player_id, s in self.select(player_ids).items()}

    def inventory(self, source_id: str) -> dict:
        """Read the current source inventory without creating the catalog or changing its files."""
        connection = self._connect()
        if connection is None:
            return bundle.inventory(source_id, {})
        try:
            with closing(connection):
                active = dict(connection.execute(
                    "SELECT player_id, sha256 FROM photo WHERE source_id = ? AND status = 'active'", (source_id,)
                ).fetchall())
        except sqlite3.Error as error:
            raise PhotoStoreFault(f'índice de fotografías ilegible: {type(error).__name__}') from None
        return bundle.inventory(source_id, active)

    def imported_bundle(self, request_id: str) -> tuple[str, dict] | None:
        """Read the idempotency receipt without opening the writable store."""
        connection = self._connect()
        if connection is None:
            return None
        try:
            with closing(connection):
                row = connection.execute(
                    'SELECT bundle_sha256, summary_json FROM photo_import WHERE request_id = ?', (request_id,)
                ).fetchone()
        except sqlite3.Error as error:
            raise PhotoStoreFault(f'índice de fotografías ilegible: {type(error).__name__}') from None
        if row is None:
            return None
        try:
            return row[0], json.loads(row[1])
        except (TypeError, ValueError):
            raise PhotoStoreFault('recibo de importación de fotografías no válido') from None

    def serve(self, player_id: int, version: str) -> tuple[bytes, Selected] | None:
        """Bytes of the selected photo if `version` is its SHA-256; None if the player has no selected photo or the
        version is not the current one. The file is located only from the index (never from the request) and is
        re-hashed before being returned."""
        if not VERSION.match(version or ''):
            return None
        selected = self.select([player_id]).get(player_id)
        if selected is None or selected.sha256 != version:
            return None
        blob_dir = self.root / 'blobs'
        if blob_dir.is_symlink() or not blob_dir.is_dir():
            raise PhotoStoreFault('ruta de blobs no segura')
        path = blob_dir / bundle.file_name(selected.sha256, selected.media_type)
        try:
            if path.is_symlink() or not path.is_file():
                raise PhotoStoreFault(f'falta el archivo de la fotografía de {player_id}')
            data = path.read_bytes()
        except OSError:
            raise PhotoStoreFault(f'no se pudo leer la fotografía de {player_id}') from None
        if hashlib.sha256(data).hexdigest() != selected.sha256:
            raise PhotoStoreFault(f'la fotografía de {player_id} no coincide con su hash')
        return data, selected


# ─── photo_url in the sporting JSON responses ────────────────────────────────────────────────────────────────────

_PLAYER = re.compile(r'^/api/v1/players/[^/]+$')
_TEAM_PLAYERS = re.compile(r'^/api/v1/teams/[^/]+/players$')
_TEAM_STATS = re.compile(r'^/api/v1/teams/[^/]+/player-stats$')


def _ids(rows) -> list[int]:
    return [row['player_id'] for row in rows if isinstance(row, dict) and type(row.get('player_id')) is int]


def _with(row, urls):
    return dict(row, photo_url=urls.get(row.get('player_id'))) if isinstance(row, dict) else row


def with_photo_urls(path: str, status: int, body, lookup) -> object:
    """A copy of a successful player response with `photo_url` (a relative URL or null) on every player object; any
    other response is returned unchanged. `lookup(ids) -> {id: url}` is called once. The input body is never mutated
    (outside control mode it is the API's own in-memory data)."""
    if status != 200 or not isinstance(body, dict) or 'data' not in body:
        return body
    path = '/' + path.strip('/')  # the same normalization queries.consultar applies to the route
    data = body['data']
    if _PLAYER.match(path) and isinstance(data, dict):
        urls = lookup(_ids([data]))
        return dict(body, data=_with(data, urls))
    if _TEAM_PLAYERS.match(path) and isinstance(data, list):
        urls = lookup(_ids(data))
        return dict(body, data=[_with(row, urls) for row in data])
    if _TEAM_STATS.match(path) and isinstance(data, dict) and isinstance(data.get('competitions'), list):
        competitions = data['competitions']
        urls = lookup([i for c in competitions if isinstance(c, dict) for i in _ids(c.get('players') or [])])
        return dict(body, data=dict(data, competitions=[
            dict(c, players=[_with(row, urls) for row in c['players']])
            if isinstance(c, dict) and isinstance(c.get('players'), list) else c
            for c in competitions]))
    return body
