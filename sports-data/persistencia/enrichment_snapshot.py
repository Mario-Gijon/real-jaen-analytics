"""Private, immutable SQLite snapshots retained for enrichment readers.

The coordinator owns one reference for its current generation. HTTP readers acquire
short leases while capturing that generation; a retired snapshot is removed only
after its last lease closes. A small registry bound prevents slow readers from
letting old-generation database copies accumulate without limit.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import sqlite3
import threading
import uuid

from persistencia import sqlite


MAX_LIVE_ENRICHMENT_SNAPSHOTS = 3


class SnapshotCapacityError(RuntimeError):
    """Publication cannot create another snapshot until older readers finish."""


class EnrichmentSnapshotLease:
    def __init__(self, snapshot: "EnrichmentSnapshot"):
        self._snapshot = snapshot
        self._lock = threading.Lock()
        self._released = False

    @property
    def generation_id(self) -> str:
        return self._snapshot.generation_id

    @property
    def path(self) -> Path:
        return self._snapshot.path

    def release(self) -> None:
        with self._lock:
            if self._released:
                return
            self._released = True
        self._snapshot._release_reader()

    close = release

    def __enter__(self) -> "EnrichmentSnapshotLease":
        return self

    def __exit__(self, *_):
        self.release()


class EnrichmentSnapshot:
    """One immutable copy with an owner reference and zero or more reader leases."""

    def __init__(self, registry: "EnrichmentSnapshotRegistry", generation_id: str, path: Path):
        self._registry = registry
        self.generation_id = generation_id
        self.path = path
        self._lock = threading.Lock()
        self._references = 1  # The current Generacion owns this until it is replaced/closed.
        self._retired = False

    def retain(self) -> EnrichmentSnapshotLease:
        with self._lock:
            if self._retired or self._references <= 0:
                raise RuntimeError("La instantánea de enrichment ya no está vigente")
            self._references += 1
        return EnrichmentSnapshotLease(self)

    def release_owner(self) -> None:
        remove = False
        with self._lock:
            if self._retired:
                return
            self._retired = True
            self._references -= 1
            remove = self._references == 0
        if remove:
            self._registry._remove(self)

    def _release_reader(self) -> None:
        remove = False
        with self._lock:
            if self._references <= 0:
                return
            self._references -= 1
            remove = self._retired and self._references == 0
        if remove:
            self._registry._remove(self)


class EnrichmentSnapshotRegistry:
    """Create and own private snapshots in a coordinator-only directory."""

    def __init__(self, root: str | Path, *, maximum: int = MAX_LIVE_ENRICHMENT_SNAPSHOTS):
        if maximum < 1:
            raise ValueError("maximum debe ser positivo")
        self.root = Path(os.path.abspath(root))
        self.maximum = maximum
        self._lock = threading.Lock()
        self._snapshots: dict[str, EnrichmentSnapshot] = {}
        self._reservations = 0

    def cleanup_after_restart(self) -> None:
        """Remove only old UUID-named snapshot directories after the DB lock is acquired."""
        if self.root.is_symlink():
            raise RuntimeError("El directorio de instantáneas no puede ser un enlace simbólico")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.root.is_symlink():
            raise RuntimeError("El directorio de instantáneas no puede ser un enlace simbólico")
        os.chmod(self.root, 0o700)
        with self._lock:
            if self._snapshots or self._reservations:
                raise RuntimeError("No se pueden limpiar instantáneas con lectores activos")
            for child in self.root.iterdir():
                try:
                    uuid.UUID(child.name)
                except ValueError:
                    continue
                if child.is_symlink() or not child.is_dir():
                    continue
                shutil.rmtree(child)
            _fsync_directory(self.root)

    def create(self, source: str | Path, generation_id: str) -> EnrichmentSnapshot:
        try:
            canonical_generation = str(uuid.UUID(generation_id))
        except (ValueError, TypeError, AttributeError) as error:
            raise ValueError("generation_id debe ser UUID") from error
        if self.root.is_symlink():
            raise RuntimeError("El directorio de instantáneas no puede ser un enlace simbólico")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.root.is_symlink():
            raise RuntimeError("El directorio de instantáneas no puede ser un enlace simbólico")
        os.chmod(self.root, 0o700)
        with self._lock:
            if len(self._snapshots) + self._reservations >= self.maximum:
                raise SnapshotCapacityError("Hay demasiadas instantáneas retenidas por consultas activas")
            self._reservations += 1
        reserved = True

        directory = self.root / canonical_generation
        database = directory / "generation.sqlite"
        created_directory = False
        try:
            directory.mkdir(mode=0o700)
            created_directory = True
            os.chmod(directory, 0o700)
            sqlite.copia_consistente(source, database)
            os.chmod(database, 0o400)
            _fsync_file(database)
            _fsync_directory(directory)
            _fsync_directory(self.root)
            self._check_snapshot(database)
            snapshot = EnrichmentSnapshot(self, canonical_generation, database)
            with self._lock:
                self._reservations -= 1
                reserved = False
                self._snapshots[str(database)] = snapshot
            return snapshot
        except BaseException:
            with self._lock:
                if reserved:
                    self._reservations -= 1
            if created_directory and directory.exists() and not directory.is_symlink():
                shutil.rmtree(directory)
            raise

    @staticmethod
    def _check_snapshot(database: Path) -> None:
        connection = sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)
        try:
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("La instantánea SQLite no supera integrity_check")
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise RuntimeError("La instantánea SQLite tiene claves foráneas inválidas")
        finally:
            connection.close()

    def _remove(self, snapshot: EnrichmentSnapshot) -> None:
        key = str(snapshot.path)
        with self._lock:
            registered = self._snapshots.get(key) is snapshot
        if not registered:
            return
        directory = snapshot.path.parent
        if not (directory.parent == self.root and not self.root.is_symlink()
                and not directory.is_symlink() and directory.is_dir()
                and directory.name == snapshot.generation_id):
            return
        try:
            shutil.rmtree(directory)
        except OSError:
            # Keep the handle registered so failed cleanup continues to count
            # against the cap; the next process start can safely retry it.
            return
        with self._lock:
            if self._snapshots.get(key) is snapshot:
                del self._snapshots[key]
        try:
            _fsync_directory(self.root)
        except OSError:
            pass


def _fsync_file(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = [
    "EnrichmentSnapshot",
    "EnrichmentSnapshotLease",
    "EnrichmentSnapshotRegistry",
    "MAX_LIVE_ENRICHMENT_SNAPSHOTS",
    "SnapshotCapacityError",
]
