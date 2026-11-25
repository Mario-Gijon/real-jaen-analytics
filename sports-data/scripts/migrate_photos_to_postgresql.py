"""Import an existing fotos.sqlite plus blobs/ store into the empty PostgreSQL photo catalog.

This is an explicit administrative command. It never runs during application startup and never modifies its source.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import text
from sqlalchemy.engine import Engine

from fotos import bundle, images

TABLES = ('sports_data_photo_meta', 'sports_data_photo_blob', 'sports_data_photo_import',
          'sports_data_photo', 'sports_data_photo_import_entry')
PHOTO_LOCK_KEYS = (1380601928, 1330925361)


class MigrationError(RuntimeError):
    """Legacy photo-store or destination validation rejected the migration."""


def _has_symlink_component(path: Path) -> bool:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _source_rows(root: Path):
    root = root.absolute()
    if _has_symlink_component(root) or not root.is_dir():
        raise MigrationError('La ruta de origen debe ser un directorio real y explícito')
    database = root / 'fotos.sqlite'
    blobs_dir = root / 'blobs'
    if _has_symlink_component(database) or not database.is_file():
        raise MigrationError('No se encuentra fotos.sqlite o su ruta no es segura')
    if _has_symlink_component(blobs_dir) or not blobs_dir.is_dir():
        raise MigrationError('No se encuentra blobs/ o su ruta no es segura')

    source_stat = database.stat()
    try:
        connection = sqlite3.connect(f'{database.resolve().as_uri()}?mode=ro', uri=True, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('BEGIN')
        if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise MigrationError('fotos.sqlite no supera integrity_check')
        if connection.execute('PRAGMA foreign_key_check').fetchone() is not None:
            raise MigrationError('fotos.sqlite contiene referencias rotas')
        table_names = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        if not {'meta', 'blob', 'photo_import', 'photo', 'photo_import_entry'} <= table_names:
            raise MigrationError('fotos.sqlite no tiene el esquema esperado')
        meta = {row['key']: row['value'] for row in connection.execute('SELECT key,value FROM meta')}
        if meta.get('schema_version') != '1':
            raise MigrationError('Versión de fotos.sqlite no admitida')

        blob_rows = [dict(row) for row in connection.execute(
            'SELECT sha256,media_type,bytes,width,height,first_request_id,stored_at FROM blob ORDER BY sha256')]
        import_rows = [dict(row) for row in connection.execute(
            'SELECT request_id,bundle_sha256,job_id,operation_id,recipe_id,contract,source_id,collection_id,'
            'collection_version,collection_manifest_sha256,collection_result,imported_at,summary_json '
            'FROM photo_import ORDER BY request_id')]
        photo_rows = [dict(row) for row in connection.execute(
            'SELECT id,player_id,source_id,sha256,collection_id,collection_version,provenance_json,status,request_id,'
            'superseded_by_request_id,imported_at FROM photo ORDER BY id')]
        entry_rows = [dict(row) for row in connection.execute(
            'SELECT request_id,player_id,outcome,sha256,reason FROM photo_import_entry '
            'ORDER BY request_id,player_id')]

        import_ids = {row['request_id'] for row in import_rows}
        blob_names = set()
        blob_content = {}
        for row in blob_rows:
            digest, media_type = row['sha256'], row['media_type']
            if (not isinstance(digest, str) or len(digest) != 64
                    or any(char not in '0123456789abcdef' for char in digest)):
                raise MigrationError('Hash de blob no válido')
            try:
                filename = bundle.file_name(digest, media_type)
            except (KeyError, TypeError, ValueError):
                raise MigrationError('Metadatos de blob no válidos') from None
            if row['first_request_id'] not in import_ids:
                raise MigrationError(f'El blob {digest[:12]}… no tiene una importación de origen')
            path = blobs_dir / filename
            if _has_symlink_component(path) or not path.is_file() or path.resolve().parent != blobs_dir.resolve():
                raise MigrationError(f'Falta el blob indexado {filename} o su ruta no es segura')
            data = path.read_bytes()
            if len(data) != row['bytes'] or hashlib.sha256(data).hexdigest() != digest:
                raise MigrationError(f'El blob {digest[:12]}… no coincide con su tamaño o SHA-256')
            try:
                if images.inspect(data, media_type) != (row['width'], row['height']):
                    raise MigrationError(f'Las dimensiones del blob {digest[:12]}… no coinciden')
            except images.ImageError:
                raise MigrationError(f'El blob {digest[:12]}… no es una imagen válida') from None
            blob_names.add(filename)
            blob_content[digest] = data

        for row in import_rows:
            try:
                row['summary_json'] = json.loads(row['summary_json'])
                row['imported_at'] = _timestamp(row['imported_at'])
            except (TypeError, ValueError):
                raise MigrationError('Un recibo de importación no contiene JSON o fecha válidos') from None
        for row in photo_rows:
            try:
                row['provenance_json'] = json.loads(row['provenance_json'])
                row['imported_at'] = _timestamp(row['imported_at'])
            except (TypeError, ValueError):
                raise MigrationError('Una fotografía no contiene procedencia o fecha válidas') from None
        for row in blob_rows:
            row['stored_at'] = _timestamp(row['stored_at'])

        indexed_photo_hashes = {row['sha256'] for row in photo_rows}
        if not indexed_photo_hashes <= blob_content.keys():
            raise MigrationError('Una fotografía referencia un blob inexistente')
        loose = sorted(path.name for path in blobs_dir.iterdir() if path.name not in blob_names)
        return connection, meta, blob_rows, blob_content, import_rows, photo_rows, entry_rows, loose, source_stat
    except sqlite3.DatabaseError as error:
        try:
            connection.close()
        except UnboundLocalError:
            pass
        raise MigrationError(f'fotos.sqlite no se puede validar ({type(error).__name__})') from None
    except BaseException:
        try:
            connection.close()
        except UnboundLocalError:
            pass
        raise


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _destination_counts(connection) -> dict[str, int]:
    return {table: connection.execute(text(f'SELECT count(*) FROM {table}')).scalar_one() for table in TABLES}


def _insert_rows(connection, table: str, rows: list[dict], sql: str) -> None:
    if rows:
        connection.execute(text(sql), rows)


def migrate(source_root: Path | str, engine: Engine) -> dict:
    """Copy a validated SQLite store atomically into an empty pg0005 destination."""
    root = Path(source_root).expanduser()
    source_data = _source_rows(root)
    source, meta, blobs, content, imports, photos, entries, loose, source_stat = source_data
    try:
        with engine.begin() as connection:
            connection.execute(text('SELECT pg_advisory_xact_lock(:namespace, :resource)'),
                               dict(namespace=PHOTO_LOCK_KEYS[0], resource=PHOTO_LOCK_KEYS[1]))
            current = _destination_counts(connection)
            if any(current.values()):
                raise MigrationError('El catálogo de destino no está vacío; no se modifica')

            _insert_rows(connection, 'sports_data_photo_meta',
                         [dict(key=key, value=value) for key, value in meta.items()],
                         'INSERT INTO sports_data_photo_meta (key,value) VALUES (:key,:value)')
            _insert_rows(connection, 'sports_data_photo_blob', [
                dict(sha256=row['sha256'], media_type=row['media_type'], byte_count=row['bytes'],
                     width=row['width'], height=row['height'], first_request_id=row['first_request_id'],
                     stored_at=row['stored_at'], content=content[row['sha256']]) for row in blobs
            ], 'INSERT INTO sports_data_photo_blob '
               '(sha256,media_type,byte_count,width,height,first_request_id,stored_at,content) '
               'VALUES (:sha256,:media_type,:byte_count,:width,:height,:first_request_id,:stored_at,:content)')
            import_payloads = []
            for row in imports:
                payload = dict(row)
                payload['summary_json'] = json.dumps(row['summary_json'], ensure_ascii=False)
                import_payloads.append(payload)
            _insert_rows(connection, 'sports_data_photo_import', import_payloads,
                         'INSERT INTO sports_data_photo_import '
               '(request_id,bundle_sha256,job_id,operation_id,recipe_id,contract,source_id,collection_id,'
               'collection_version,collection_manifest_sha256,collection_result,imported_at,summary_json) '
               'VALUES (:request_id,:bundle_sha256,:job_id,:operation_id,:recipe_id,:contract,:source_id,:collection_id,'
               ':collection_version,:collection_manifest_sha256,:collection_result,:imported_at,CAST(:summary_json AS jsonb))')
            _insert_rows(connection, 'sports_data_photo', [
                dict(sofascore_id=row['player_id'], source_id=row['source_id'], sha256=row['sha256'],
                     collection_id=row['collection_id'], collection_version=row['collection_version'],
                     provenance=json.dumps(row['provenance_json'], ensure_ascii=False), status=row['status'],
                     request_id=row['request_id'], superseded_by_request_id=row['superseded_by_request_id'],
                     imported_at=row['imported_at'], id=row['id']) for row in photos
            ], 'INSERT INTO sports_data_photo '
               '(id,sofascore_id,source_id,sha256,collection_id,collection_version,provenance,status,request_id,'
               'superseded_by_request_id,imported_at) VALUES (:id,:sofascore_id,:source_id,:sha256,:collection_id,'
               ':collection_version,CAST(:provenance AS jsonb),:status,:request_id,:superseded_by_request_id,:imported_at)')
            _insert_rows(connection, 'sports_data_photo_import_entry', [
                dict(request_id=row['request_id'], sofascore_id=row['player_id'], outcome=row['outcome'],
                     sha256=row['sha256'], reason=row['reason']) for row in entries
            ], 'INSERT INTO sports_data_photo_import_entry '
               '(request_id,sofascore_id,outcome,sha256,reason) VALUES '
               '(:request_id,:sofascore_id,:outcome,:sha256,:reason)')

            maximum_id = max((row['id'] for row in photos), default=None)
            if maximum_id is None:
                connection.execute(text("SELECT setval('sports_data_photo_id_seq', 1, false)"))
            else:
                connection.execute(text("SELECT setval('sports_data_photo_id_seq', :value, true)"),
                                   {'value': maximum_id})
            counts = _destination_counts(connection)
            expected = dict(zip(TABLES, (len(meta), len(blobs), len(imports), len(photos), len(entries))))
            if counts != expected:
                raise MigrationError('Los recuentos del destino no coinciden con SQLite')
            saved_blobs = connection.execute(text(
                'SELECT sha256,byte_count,content FROM sports_data_photo_blob ORDER BY sha256'
            )).all()
            if len(saved_blobs) != len(blobs):
                raise MigrationError('El número de blobs migrados no coincide')
            for sha256, byte_count, raw in saved_blobs:
                raw = bytes(raw)
                if (raw != content[sha256] or byte_count != len(raw)
                        or hashlib.sha256(raw).hexdigest() != sha256):
                    raise MigrationError(f'El blob migrado {sha256[:12]}… no coincide byte a byte')
            if maximum_id is not None:
                sequence_value = connection.execute(text("SELECT last_value FROM sports_data_photo_id_seq")).scalar_one()
                if sequence_value < maximum_id:
                    raise MigrationError('La secuencia de fotografías no cubre los identificadores migrados')

            after = (root / 'fotos.sqlite').stat()
            if (after.st_size, after.st_mtime_ns) != (source_stat.st_size, source_stat.st_mtime_ns):
                raise MigrationError('fotos.sqlite cambió durante la migración')
        return dict(meta=len(meta), blobs=len(blobs), imports=len(imports), photos=len(photos),
                    entries=len(entries), orphan_blobs=loose)
    finally:
        source.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Migra explícitamente fotos.sqlite y blobs/ a PostgreSQL')
    parser.add_argument('--source-root', type=Path, required=True,
                        help='Directorio que contiene fotos.sqlite y blobs/')
    args = parser.parse_args(argv)
    engine = None
    try:
        from persistencia.postgresql import url_postgresql
        from sqlalchemy import create_engine

        engine = create_engine(url_postgresql('SPORTS_DATA_MIGRATION_DATABASE_URL'),
                               pool_pre_ping=True, hide_parameters=True,
                               connect_args={'connect_timeout': 10})
        result = migrate(args.source_root, engine)
    except (MigrationError, RuntimeError, OSError) as error:
        print(f'Migración de fotografías: {error}', file=sys.stderr)
        return 1
    except Exception as error:
        print(f'Migración de fotografías: error de {type(error).__name__}', file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()
    print('Filas importadas: ' + json.dumps({key: value for key, value in result.items()
                                               if key != 'orphan_blobs'}, sort_keys=True))
    print('Blobs huérfanos sin indexar (no eliminados): ' + json.dumps(result['orphan_blobs']))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
