"""Shared PostgreSQL persistence for player photos.

Every photo import takes the same transaction-level advisory lock (keys
1380601928/1330925361, ASCII ``RJPH``/``OTO1``) before checking its receipt and
inventory. Image content stays in BYTEA; metadata lookups never select it.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Iterable

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from . import bundle, images, serving
from .players import KnownPlayers
from .store import ImportConflict, InventoryConflict, OUTCOMES, StoreError

PHOTO_LOCK_KEYS = (1380601928, 1330925361)
_SHA = re.compile(r'^[0-9a-f]{64}$')


def _json(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


class PostgreSQLPhotoStore:
    """Write interface matching PhotoStore without opening the legacy filesystem store."""

    def __init__(self, engine):
        self.engine = engine

    @staticmethod
    def _fault(message: str):
        raise StoreError(message)

    @staticmethod
    def _stored_blob(connection, sha256: str):
        row = connection.execute(text(
            'SELECT media_type, byte_count, width, height, content '
            'FROM sports_data_photo_blob WHERE sha256=:sha256'
        ), {'sha256': sha256}).mappings().first()
        if row is None:
            return None
        content = bytes(row['content'])
        if (len(content) != row['byte_count'] or hashlib.sha256(content).hexdigest() != sha256
                or row['media_type'] not in images.FORMATS):
            raise StoreError('El blob PostgreSQL no coincide con su tamaño o SHA-256')
        return row

    def import_bundle(self, verified: bundle.VerifiedBundle, known: KnownPlayers) -> dict:
        manifest = verified.manifest
        source = verified.source_id
        try:
            with self.engine.begin() as connection:
                connection.execute(text('SELECT pg_advisory_xact_lock(:namespace, :resource)'),
                                   dict(namespace=PHOTO_LOCK_KEYS[0], resource=PHOTO_LOCK_KEYS[1]))
                previous = connection.execute(text(
                    'SELECT bundle_sha256, summary_json FROM sports_data_photo_import WHERE request_id=:request_id'
                ), {'request_id': verified.request_id}).first()
                if previous is not None:
                    if previous[0] != verified.sha256:
                        raise ImportConflict('La solicitud ya se importó con otro paquete')
                    summary = previous[1]
                    return dict(summary, reused=True)

                active = dict(connection.execute(text(
                    "SELECT sofascore_id, sha256 FROM sports_data_photo "
                    "WHERE source_id=:source AND status='active'"
                ), {'source': source}).all())
                current_inventory = bundle.inventory(source, active)
                if manifest.get('inventory_sha256') != current_inventory['sha256']:
                    raise InventoryConflict('El inventario cambió después de verificar el paquete')

                plan = [self._classify(connection, entry, active, known) for entry in verified.entries]
                now = datetime.now(timezone.utc).replace(microsecond=0)
                imported_at = now.isoformat()
                counts = {outcome: sum(result == outcome for _, result, _ in plan) for outcome in OUTCOMES}

                required_blobs = {}
                for entry, outcome, _reason in plan:
                    if outcome not in ('new', 'changed', 'unchanged'):
                        continue
                    if entry.sha256 is None:
                        self._fault('Entrada clasificada sin hash')
                    if entry.transfer == 'included':
                        raw = verified.blobs.get(entry.sha256)
                        if raw is None or len(raw) != entry.bytes or hashlib.sha256(raw).hexdigest() != entry.sha256:
                            self._fault('Contenido distinto de su tamaño o SHA-256')
                        required_blobs.setdefault(entry.sha256, (entry, raw))
                    else:
                        saved = self._stored_blob(connection, entry.sha256)
                        if (saved is None or saved['media_type'] != entry.media_type
                                or saved['width'] != entry.width or saved['height'] != entry.height):
                            self._fault('Fotografía omitida sin un blob vigente coherente')

                stored = 0
                for sha256, (entry, raw) in required_blobs.items():
                    existing = self._stored_blob(connection, sha256)
                    if existing is not None:
                        if (existing['media_type'] != entry.media_type or existing['byte_count'] != len(raw)
                                or existing['width'] != entry.width or existing['height'] != entry.height
                                or bytes(existing['content']) != raw):
                            self._fault('SHA-256 existente con metadatos o contenido incompatibles')
                        continue
                    connection.execute(text(
                        'INSERT INTO sports_data_photo_blob '
                        '(sha256, media_type, byte_count, width, height, first_request_id, stored_at, content) '
                        'VALUES (:sha256,:media_type,:byte_count,:width,:height,:request_id,:stored_at,:content)'
                    ), dict(sha256=sha256, media_type=entry.media_type, byte_count=len(raw), width=entry.width,
                            height=entry.height, request_id=verified.request_id, stored_at=now, content=raw))
                    stored += 1

                summary = dict(
                    request_id=verified.request_id, bundle_sha256=verified.sha256, source_id=source,
                    collection={'id': manifest['collection']['id'], 'version': manifest['collection']['version']},
                    collection_result=manifest['result']['status'], counts=counts, blobs_written=stored,
                    imported_at=imported_at,
                )
                connection.execute(text(
                    'INSERT INTO sports_data_photo_import '
                    '(request_id,bundle_sha256,job_id,operation_id,recipe_id,contract,source_id,collection_id,'
                    'collection_version,collection_manifest_sha256,collection_result,imported_at,summary_json) '
                    'VALUES (:request_id,:bundle_sha256,:job_id,:operation_id,:recipe_id,:contract,:source_id,'
                    ':collection_id,:collection_version,:manifest_sha,:result,:imported_at,CAST(:summary AS jsonb))'
                ), dict(request_id=verified.request_id, bundle_sha256=verified.sha256, job_id=manifest['job_id'],
                        operation_id=manifest['operation_id'], recipe_id=manifest['recipe_id'],
                        contract=manifest['contract'], source_id=source,
                        collection_id=manifest['collection']['id'],
                        collection_version=manifest['collection']['version'],
                        manifest_sha=manifest['collection']['manifest_sha256'],
                        result=manifest['result']['status'], imported_at=now, summary=_json(summary)))

                for entry, outcome, reason in plan:
                    if outcome == 'changed':
                        connection.execute(text(
                            "UPDATE sports_data_photo SET status='superseded',superseded_by_request_id=:request_id "
                            "WHERE sofascore_id=:player AND source_id=:source AND status='active'"
                        ), dict(request_id=verified.request_id, player=entry.player_id, source=source))
                    if outcome in ('new', 'changed'):
                        connection.execute(text(
                            'INSERT INTO sports_data_photo '
                            '(sofascore_id,source_id,sha256,collection_id,collection_version,provenance,status,'
                            'request_id,imported_at) VALUES (:player,:source,:sha256,:collection_id,:collection_version,'
                            "CAST(:provenance AS jsonb),'active',:request_id,:imported_at)"
                        ), dict(player=entry.player_id, source=source, sha256=entry.sha256,
                                collection_id=manifest['collection']['id'],
                                collection_version=manifest['collection']['version'],
                                provenance=_json(entry.provenance), request_id=verified.request_id, imported_at=now))
                    connection.execute(text(
                        'INSERT INTO sports_data_photo_import_entry '
                        '(request_id,sofascore_id,outcome,sha256,reason) '
                        'VALUES (:request_id,:player,:outcome,:sha256,:reason)'
                    ), dict(request_id=verified.request_id, player=entry.player_id, outcome=outcome,
                            sha256=entry.sha256, reason=reason))
            return dict(summary, reused=False)
        except (ImportConflict, InventoryConflict, StoreError):
            raise
        except SQLAlchemyError as error:
            raise StoreError(f'No se pudo guardar el paquete ({type(error).__name__})') from None

    def _classify(self, connection, entry, active, known):
        if entry.player_id not in known:
            return entry, 'unknown_player', 'Sports Data no conoce este jugador'
        if entry.transfer == 'rejected':
            return entry, 'invalid', entry.reason
        current = active.get(entry.player_id)
        if entry.transfer == 'omitted_unchanged':
            if current != entry.sha256:
                return entry, 'invalid', 'omitida como sin cambios, pero no es la fotografía vigente'
            saved = self._stored_blob(connection, entry.sha256)
            if (saved is None or saved['media_type'] != entry.media_type or saved['width'] != entry.width
                    or saved['height'] != entry.height):
                return entry, 'invalid', 'omitida como sin cambios, pero su blob no está disponible'
            return entry, 'unchanged', None
        if current == entry.sha256:
            return entry, 'unchanged', None
        return entry, ('changed' if current is not None else 'new'), None

    def active_photos(self, player_id: int) -> list[dict]:
        try:
            with self.engine.connect() as connection:
                rows = connection.execute(text(
                    'SELECT p.sofascore_id AS player_id,p.source_id,p.sha256,b.media_type,b.byte_count AS bytes,'
                    'b.width,b.height,p.collection_id,p.collection_version,p.provenance,p.request_id,p.imported_at '
                    'FROM sports_data_photo p JOIN sports_data_photo_blob b USING (sha256) '
                    "WHERE p.sofascore_id=:player AND p.status='active' ORDER BY p.source_id"
                ), {'player': player_id}).mappings().all()
            return [dict(row, provenance=row['provenance'], path=None) for row in rows]
        except SQLAlchemyError as error:
            raise StoreError(f'No se pudo leer el catálogo ({type(error).__name__})') from None


class PostgreSQLPhotoReader:
    """Read-only interface compatible with PhotoReader, backed by the shared app engine."""

    def __init__(self, engine, sources: Iterable[str] = serving.DEFAULT_SOURCES):
        self.engine = engine
        self.sources = tuple(sources)
        if not self.sources:
            raise ValueError('Se necesita al menos una fuente de fotografías')

    def _metadata(self, player_ids: Iterable[int]):
        wanted = sorted({player for player in player_ids if type(player) is int and player > 0})
        if not wanted:
            return {}
        rank = {source: index for index, source in enumerate(self.sources)}
        chosen = {}
        try:
            with self.engine.connect() as connection:
                for start in range(0, len(wanted), serving.BATCH):
                    chunk = wanted[start:start + serving.BATCH]
                    player_params = {f'player_{i}': player for i, player in enumerate(chunk)}
                    source_params = {f'source_{i}': source for i, source in enumerate(self.sources)}
                    query = (
                        'SELECT p.sofascore_id AS player_id,p.source_id,p.sha256,b.media_type '
                        'FROM sports_data_photo p JOIN sports_data_photo_blob b USING (sha256) '
                        "WHERE p.status='active' AND p.sofascore_id IN (" +
                        ','.join(':' + key for key in player_params) + ') AND p.source_id IN (' +
                        ','.join(':' + key for key in source_params) + ')'
                    )
                    rows = connection.execute(text(query), {**player_params, **source_params}).mappings().all()
                    for row in rows:
                        if row['media_type'] not in images.FORMATS or not _SHA.fullmatch(row['sha256']):
                            raise serving.PhotoStoreFault(f"fila de fotografía no válida para {row['player_id']}")
                        selected = serving.Selected(row['player_id'], row['source_id'], row['sha256'], row['media_type'])
                        candidate = (rank[row['source_id']], selected)
                        if row['player_id'] not in chosen or candidate[0] < chosen[row['player_id']][0]:
                            chosen[row['player_id']] = candidate
        except serving.PhotoStoreFault:
            raise
        except SQLAlchemyError as error:
            raise serving.PhotoStoreFault(f'catálogo PostgreSQL ilegible: {type(error).__name__}') from None
        return {player: selected for player, (_priority, selected) in chosen.items()}

    def select(self, player_ids: Iterable[int]) -> dict[int, serving.Selected]:
        return self._metadata(player_ids)

    def urls(self, player_ids: Iterable[int]) -> dict[int, str]:
        return {player: selected.url for player, selected in self.select(player_ids).items()}

    def inventory(self, source_id: str) -> dict:
        try:
            with self.engine.connect() as connection:
                rows = connection.execute(text(
                    "SELECT sofascore_id,sha256 FROM sports_data_photo "
                    "WHERE source_id=:source AND status='active'"
                ), {'source': source_id}).all()
            return bundle.inventory(source_id, dict(rows))
        except SQLAlchemyError as error:
            raise serving.PhotoStoreFault(f'catálogo PostgreSQL ilegible: {type(error).__name__}') from None

    def imported_bundle(self, request_id: str) -> tuple[str, dict] | None:
        try:
            with self.engine.connect() as connection:
                row = connection.execute(text(
                    'SELECT bundle_sha256,summary_json FROM sports_data_photo_import WHERE request_id=:request_id'
                ), {'request_id': request_id}).first()
            return None if row is None else (row[0], row[1])
        except SQLAlchemyError as error:
            raise serving.PhotoStoreFault(f'catálogo PostgreSQL ilegible: {type(error).__name__}') from None

    def serve(self, player_id: int, version: str):
        if not serving.VERSION.fullmatch(version or ''):
            return None
        rank = {source: index for index, source in enumerate(self.sources)}
        source_params = {f'source_{i}': source for i, source in enumerate(self.sources)}
        cases = ' '.join(f'WHEN :source_{i} THEN {i}' for i in range(len(self.sources)))
        query = text(
            'SELECT p.sofascore_id AS player_id,p.source_id,p.sha256,b.media_type,b.byte_count,'
            'b.content FROM sports_data_photo p JOIN sports_data_photo_blob b USING (sha256) '
            "WHERE p.sofascore_id=:player AND p.status='active' AND p.source_id IN (" +
            ','.join(':' + key for key in source_params) +
            f') ORDER BY CASE p.source_id {cases} ELSE {len(rank)} END LIMIT 1'
        )
        try:
            # Selection and BYTEA come from one PostgreSQL statement snapshot.
            with self.engine.connect() as connection:
                row = connection.execute(query, {'player': player_id, **source_params}).mappings().first()
        except SQLAlchemyError as error:
            raise serving.PhotoStoreFault(f'catálogo PostgreSQL ilegible: {type(error).__name__}') from None
        if row is None or row['sha256'] != version:
            return None
        data = bytes(row['content'])
        if len(data) != row['byte_count'] or hashlib.sha256(data).hexdigest() != row['sha256']:
            raise serving.PhotoStoreFault(f'la fotografía de {player_id} no coincide con su hash')
        if row['media_type'] not in images.FORMATS:
            raise serving.PhotoStoreFault(f'tipo de fotografía no válido para {player_id}')
        return data, serving.Selected(player_id, row['source_id'], row['sha256'], row['media_type'])
