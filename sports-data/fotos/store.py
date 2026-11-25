"""Legacy SQLite photo store: content-addressed files plus an independent SQLite index.

This class remains active for JSON and SQLite sporting modes. PostgreSQL mode uses fotos.postgresql and does not open
this directory store.

  <root>/                      /state/player-photos by default (SPORTS_DATA_PHOTOS_DIR overrides it)
    blobs/<sha256>.<ext>       immutable, one file per distinct content, shared by every row that uses it
    tmp/                       private staging; emptied whenever the store opens
    fotos.sqlite               index: blobs, photos per (player, source), imports and their per-entry outcomes

Separate from the sporting SQLite generations: no sporting table is read or written, and the sporting data are only
consulted through a KnownPlayers predicate supplied by the caller.

One import is one SQLite write transaction (BEGIN IMMEDIATE). Inside it: the request id is checked for idempotency,
every entry is classified against the index, the blobs it needs are written to tmp/, verified and renamed into blobs/,
and only then are the index rows written and committed. A failure before the commit leaves the index exactly as it was
(SQLite rolls back, also after a crash) and at most leaves unreferenced blobs, which are harmless and reported by
orphans(). A blob is never rewritten unless its file is missing or does not match its hash.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import threading
from typing import Callable

from . import bundle
from .players import KnownPlayers

DEFAULT_ROOT = '/state/player-photos'
OUTCOMES = ('new', 'changed', 'unchanged', 'invalid', 'unknown_player')
SCHEMA_VERSION = '1'
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS blob (
    sha256 TEXT PRIMARY KEY CHECK (length(sha256) = 64),
    media_type TEXT NOT NULL,
    bytes INTEGER NOT NULL CHECK (bytes > 0),
    width INTEGER NOT NULL CHECK (width > 0),
    height INTEGER NOT NULL CHECK (height > 0),
    first_request_id TEXT NOT NULL,
    stored_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS photo_import (
    request_id TEXT PRIMARY KEY,
    bundle_sha256 TEXT NOT NULL,
    job_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    recipe_id TEXT NOT NULL,
    contract TEXT NOT NULL,
    source_id TEXT NOT NULL,
    collection_id TEXT NOT NULL,
    collection_version TEXT NOT NULL,
    collection_manifest_sha256 TEXT NOT NULL,
    collection_result TEXT NOT NULL CHECK (collection_result IN ('complete', 'partial')),
    imported_at TEXT NOT NULL,
    summary_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS photo (
    id INTEGER PRIMARY KEY,
    player_id INTEGER NOT NULL CHECK (player_id > 0),
    source_id TEXT NOT NULL,
    sha256 TEXT NOT NULL REFERENCES blob(sha256),
    collection_id TEXT NOT NULL,
    collection_version TEXT NOT NULL,
    provenance_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'superseded')),
    request_id TEXT NOT NULL REFERENCES photo_import(request_id),
    superseded_by_request_id TEXT REFERENCES photo_import(request_id),
    imported_at TEXT NOT NULL
);
-- At most one active photo per player and source; several sources per player are allowed.
CREATE UNIQUE INDEX IF NOT EXISTS photo_one_active_per_source ON photo(player_id, source_id) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS photo_by_player ON photo(player_id, status);
CREATE TABLE IF NOT EXISTS photo_import_entry (
    request_id TEXT NOT NULL REFERENCES photo_import(request_id),
    player_id INTEGER NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('new', 'changed', 'unchanged', 'invalid', 'unknown_player')),
    sha256 TEXT,
    reason TEXT,
    PRIMARY KEY (request_id, player_id)
);
"""


class StoreError(RuntimeError):
    pass


class ImportConflict(StoreError):
    """The request id was already imported with a different bundle."""


class InventoryConflict(StoreError):
    """The verified bundle was based on an inventory that changed before commit."""


def default_root() -> Path:
    return Path(os.environ.get('SPORTS_DATA_PHOTOS_DIR', DEFAULT_ROOT))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _fsync_dir(path: Path) -> None:
    if os.name != 'posix':
        return  # directories cannot be opened for fsync on Windows; rename durability is the file system's
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class PhotoStore:
    def __init__(self, root: Path | str | None = None, *, fault: Callable[[str], None] | None = None,
                 clock: Callable[[], str] = utc_now):
        self.root = Path(root) if root is not None else default_root()
        self.blobs = self.root / 'blobs'
        self.tmp = self.root / 'tmp'
        self.db_path = self.root / 'fotos.sqlite'
        self._fault = fault or (lambda point: None)
        self._clock = clock
        self._guard = threading.Lock()
        self._open()

    # ─── Opening and recovery ────────────────────────────────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, isolation_level=None, timeout=30)
        connection.execute('PRAGMA foreign_keys = ON')
        connection.execute('PRAGMA synchronous = FULL')
        return connection

    def _open(self) -> None:
        for path in (self.root, self.blobs, self.tmp):
            if path.is_symlink():
                raise StoreError(f'Ruta del almacén no segura: {path}')
            path.mkdir(parents=True, exist_ok=True)
        if self.db_path.is_symlink():
            raise StoreError(f'Ruta del índice no segura: {self.db_path}')
        # Staging files of an interrupted import are never referenced: the index is only written after every blob
        # it needs has been renamed into blobs/.
        for leftover in self.tmp.iterdir():
            if leftover.is_file() or leftover.is_symlink():
                leftover.unlink()
        with closing(self._connect()) as c:
            c.executescript(SCHEMA)
            c.execute("INSERT OR IGNORE INTO meta VALUES ('schema_version', ?)", (SCHEMA_VERSION,))
            version = c.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0]
            if version != SCHEMA_VERSION:
                raise StoreError(f'Versión de índice no admitida: {version}')
            if c.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise StoreError('El índice de fotografías no supera integrity_check')

    # ─── Blobs ───────────────────────────────────────────────────────────────────────────────────────────────

    def blob_path(self, sha256: str, media_type: str) -> Path:
        return self.blobs / bundle.file_name(sha256, media_type)

    def _blob_ok(self, sha256: str, media_type: str) -> bool:
        path = self.blob_path(sha256, media_type)
        try:
            return path.is_file() and not path.is_symlink() and bundle.sha(path.read_bytes()) == sha256
        except OSError:
            return False

    def _write_blob(self, sha256: str, media_type: str, data: bytes) -> bool:
        """Publish one verified blob atomically. Returns False if a valid file was already there (untouched)."""
        if bundle.sha(data) != sha256:
            raise StoreError('Contenido distinto de su hash')
        if self._blob_ok(sha256, media_type):
            return False
        staging = self.tmp / f'{sha256}.{os.getpid()}.{threading.get_ident()}.partial'
        try:
            with staging.open('xb') as out:
                out.write(data)
                out.flush()
                os.fsync(out.fileno())
            if bundle.sha(staging.read_bytes()) != sha256:
                raise StoreError('La copia temporal no coincide con su hash')
            os.replace(staging, self.blob_path(sha256, media_type))
            _fsync_dir(self.blobs)
        finally:
            staging.unlink(missing_ok=True)
        self._fault('blob_written')
        return True

    # ─── Import ──────────────────────────────────────────────────────────────────────────────────────────────

    def import_bundle(self, verified: bundle.VerifiedBundle, known: KnownPlayers) -> dict:
        """Import one verified bundle. The same request id with the same bundle returns the recorded summary and
        changes nothing; with another bundle it raises ImportConflict."""
        m = verified.manifest
        source = verified.source_id
        with self._guard, closing(self._connect()) as c:
            c.execute('BEGIN IMMEDIATE')
            try:
                previous = c.execute('SELECT bundle_sha256, summary_json FROM photo_import WHERE request_id = ?',
                                     (verified.request_id,)).fetchone()
                if previous is not None:
                    c.execute('ROLLBACK')
                    if previous[0] != verified.sha256:
                        raise ImportConflict('La solicitud ya se importó con otro paquete')
                    return dict(json.loads(previous[1]), reused=True)

                active = dict(c.execute("SELECT player_id, sha256 FROM photo WHERE source_id = ? AND status = 'active'",
                                        (source,)).fetchall())
                known_blobs = {row[0] for row in c.execute('SELECT sha256 FROM blob')}
                plan = [self._classify(e, active, known) for e in verified.entries]

                self._fault('before_blobs')
                stored, reused = [], []
                for sha256, media_type in sorted({(e.sha256, e.media_type) for e, outcome, _ in plan
                                                  if e.transfer == 'included' and outcome in ('new', 'changed',
                                                                                              'unchanged')}):
                    (stored if self._write_blob(sha256, media_type, verified.blobs[sha256]) else reused).append(sha256)

                self._fault('before_index')
                now = self._clock()
                counts = {outcome: sum(o == outcome for _, o, _ in plan) for outcome in OUTCOMES}
                summary = dict(request_id=verified.request_id, bundle_sha256=verified.sha256, source_id=source,
                               collection=dict(id=m['collection']['id'], version=m['collection']['version']),
                               collection_result=m['result']['status'], counts=counts,
                               blobs_written=len(stored), imported_at=now)
                c.execute('INSERT INTO photo_import VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)', (
                    verified.request_id, verified.sha256, m['job_id'], m['operation_id'], m['recipe_id'],
                    m['contract'], source, m['collection']['id'], m['collection']['version'],
                    m['collection']['manifest_sha256'], m['result']['status'], now,
                    json.dumps(summary, sort_keys=True, ensure_ascii=False)))
                for e, outcome, reason in plan:
                    if outcome in ('new', 'changed') and e.sha256 not in known_blobs:
                        c.execute('INSERT INTO blob VALUES (?,?,?,?,?,?,?)', (
                            e.sha256, e.media_type, e.bytes, e.width, e.height, verified.request_id, now))
                        known_blobs.add(e.sha256)
                    if outcome == 'changed':
                        c.execute("UPDATE photo SET status = 'superseded', superseded_by_request_id = ? "
                                  "WHERE player_id = ? AND source_id = ? AND status = 'active'",
                                  (verified.request_id, e.player_id, source))
                    if outcome in ('new', 'changed'):
                        c.execute("INSERT INTO photo (player_id, source_id, sha256, collection_id, collection_version, "
                                  "provenance_json, status, request_id, imported_at) VALUES (?,?,?,?,?,?,'active',?,?)",
                                  (e.player_id, source, e.sha256, m['collection']['id'], m['collection']['version'],
                                   json.dumps(e.provenance, sort_keys=True, ensure_ascii=False),
                                   verified.request_id, now))
                    c.execute('INSERT INTO photo_import_entry VALUES (?,?,?,?,?)',
                              (verified.request_id, e.player_id, outcome, e.sha256, reason))
                self._fault('before_commit')
                c.execute('COMMIT')
            except BaseException:
                if c.in_transaction:
                    c.execute('ROLLBACK')
                raise
        return dict(summary, reused=False)

    def _classify(self, e: bundle.Entry, active: dict[int, str], known: KnownPlayers):
        """(entry, outcome, reason). Never trusts the producer's own idea of new or changed."""
        if e.player_id not in known:
            return e, 'unknown_player', 'Sports Data no conoce este jugador'
        if e.transfer == 'rejected':
            return e, 'invalid', e.reason
        current = active.get(e.player_id)
        if e.transfer == 'omitted_unchanged':
            if current != e.sha256:
                return e, 'invalid', 'omitida como sin cambios, pero no es la fotografía vigente'
            if not self._blob_ok(e.sha256, e.media_type):
                return e, 'invalid', 'omitida como sin cambios, pero su archivo no está disponible'
            return e, 'unchanged', None
        if current == e.sha256:
            return e, 'unchanged', None
        return e, ('changed' if current is not None else 'new'), None

    # ─── Reading ─────────────────────────────────────────────────────────────────────────────────────────────

    def inventory(self, source_id: str) -> dict:
        """Active photos of one source, as sent to the producer (bundle.inventory)."""
        with closing(self._connect()) as c:
            active = dict(c.execute("SELECT player_id, sha256 FROM photo WHERE source_id = ? AND status = 'active'",
                                    (source_id,)).fetchall())
        return bundle.inventory(source_id, active)

    def active_photos(self, player_id: int) -> list[dict]:
        """Every active photo of a player, one per source. Choosing among sources is left to a later policy."""
        with closing(self._connect()) as c:
            c.row_factory = sqlite3.Row
            rows = c.execute(
                "SELECT p.player_id, p.source_id, p.sha256, b.media_type, b.bytes, b.width, b.height, "
                "p.collection_id, p.collection_version, p.provenance_json, p.request_id, p.imported_at "
                "FROM photo p JOIN blob b USING (sha256) WHERE p.player_id = ? AND p.status = 'active' "
                "ORDER BY p.source_id", (player_id,)).fetchall()
        photos = []
        for row in rows:
            photo = {key: row[key] for key in row.keys() if key != 'provenance_json'}
            photo.update(provenance=json.loads(row['provenance_json']),
                         path=self.blob_path(row['sha256'], row['media_type']))
            photos.append(photo)
        return photos

    def import_count(self) -> int:
        """Imports recorded so far, of any source (including those that stored nothing)."""
        with closing(self._connect()) as c:
            return c.execute('SELECT COUNT(*) FROM photo_import').fetchone()[0]

    def import_record(self, request_id: str) -> dict | None:
        with closing(self._connect()) as c:
            row = c.execute('SELECT summary_json FROM photo_import WHERE request_id = ?', (request_id,)).fetchone()
            if row is None:
                return None
            entries = c.execute('SELECT player_id, outcome, sha256, reason FROM photo_import_entry '
                                'WHERE request_id = ? ORDER BY player_id', (request_id,)).fetchall()
        return dict(json.loads(row[0]), entries=[dict(zip(('player_id', 'outcome', 'sha256', 'reason'), e))
                                                 for e in entries])

    def check(self) -> list[str]:
        """Problems between the index and the files: a referenced blob missing or not matching its hash."""
        problems = []
        with closing(self._connect()) as c:
            for sha256, media_type in c.execute('SELECT sha256, media_type FROM blob ORDER BY sha256'):
                if not self._blob_ok(sha256, media_type):
                    problems.append(f'{bundle.file_name(sha256, media_type)}: falta o no coincide con su hash')
            missing = c.execute("SELECT COUNT(*) FROM photo p LEFT JOIN blob b USING (sha256) "
                                "WHERE b.sha256 IS NULL").fetchone()[0]
            if missing:
                problems.append(f'{missing} fotografías sin blob indexado')
        return problems

    def orphans(self) -> list[str]:
        """Blob files the index does not know (left by an import that failed before its commit). Reported only:
        removing them is a later, explicit maintenance step."""
        with closing(self._connect()) as c:
            indexed = {bundle.file_name(sha256, media_type) for sha256, media_type in
                       c.execute('SELECT sha256, media_type FROM blob')}
        return sorted(p.name for p in self.blobs.iterdir() if p.name not in indexed)
