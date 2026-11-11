"""Explicitly adopt or roll back the 0007 coordinator fingerprint for the comments bundle limit change.

This is not the older 0004 -> 0007 migration tool. It does not run Alembic and only changes
confirmed.codigo in the coordinator journal after taking and verifying a private snapshot.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from persistencia import actualizacion, carga, sqlite, sincronizacion  # noqa: E402
from persistencia.bloqueo import bloquear  # noqa: E402

SOURCE = '37038df67dc1c5898a51ffd3efe216d88d43508e16dbede291f3be2c537d0720'
TARGET = '8ed0a15a88a399cfb78f0e20f240f5196ff10caead77532a48d2de5c654e8cd7'
SCOPE_SHA256 = 'a791c80eb18cf028f3af658a2dfe19d954168e2c4b28810c5e5d73a45980f164'
REVISION = '0007'
FORMAT = 'real-jaen-bundle-limit-coordinator-transition-v1'
ADOPT_CONFIRM = f'adopt coordinator fingerprint {SOURCE} -> {TARGET}'
RESTORE_CONFIRM = f'restore coordinator fingerprint {TARGET} -> {SOURCE}'


class MaintenanceRejected(RuntimeError):
    pass


def _json(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _file_sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _ro(path):
    return sqlite3.connect(f'{Path(path).resolve().as_uri()}?mode=ro', uri=True)


def _db_signature(path):
    digest = hashlib.sha256()
    with closing(_ro(path)) as connection:
        tables = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        for table in tables:
            columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')]
            digest.update(_json([table, columns]))
            fields = ', '.join(f'"{column}"' for column in columns)
            for row in sorted(connection.execute(f'SELECT {fields} FROM "{table}"').fetchall(), key=repr):
                digest.update(_json([repr(value) for value in row]))
        if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise MaintenanceRejected(f'SQLite integrity_check failed: {path}')
        if connection.execute('PRAGMA foreign_key_check').fetchall():
            raise MaintenanceRejected(f'SQLite foreign_key_check failed: {path}')
    return digest.hexdigest()


def _table_inventory(path):
    result = {}
    with closing(_ro(path)) as connection:
        tables = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        for table in tables:
            columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')]
            fields = ', '.join(f'"{column}"' for column in columns)
            rows = sorted(connection.execute(f'SELECT {fields} FROM "{table}"').fetchall(), key=repr)
            digest = hashlib.sha256(_json([table, columns]))
            for row in rows:
                digest.update(_json([repr(value) for value in row]))
            result[table] = {'columns': columns, 'rows': len(rows), 'sha256': digest.hexdigest()}
    return result


def _write_private(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + f'.tmp-{os.getpid()}')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(_json(value)); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def _copy_tree(source, destination, skip=()):
    source, destination = Path(source), Path(destination)
    skip = {Path(p).resolve() for p in skip}
    if not source.exists():
        return
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    for current, dirs, files in os.walk(source, followlinks=False):
        current = Path(current)
        target_dir = destination / current.relative_to(source)
        target_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        for name in list(dirs):
            if (current / name).is_symlink():
                raise MaintenanceRejected(f'Symlink is not allowed in snapshot: {current / name}')
        for name in files:
            item = current / name
            if item.is_symlink():
                raise MaintenanceRejected(f'Symlink is not allowed in snapshot: {item}')
            if item.resolve() in skip or name.endswith('.lock'):
                continue
            target = target_dir / name
            shutil.copyfile(item, target); os.chmod(target, 0o600)


def _copy_sqlite(source, destination):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with closing(_ro(source)) as original, closing(sqlite3.connect(destination)) as copy:
        original.backup(copy)
        copy.execute('PRAGMA journal_mode=DELETE')
    os.chmod(destination, 0o600)
    _db_signature(destination)


def _journal(base):
    path = Path(str(base) + '.actualizaciones') / 'jobs.sqlite'
    if not path.is_file():
        raise MaintenanceRejected('Missing coordinator journal')
    with closing(_ro(path)) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if tables != {'trabajo', 'confirmado'}:
            raise MaintenanceRejected('Unknown coordinator journal schema')
        if [row[1] for row in connection.execute('PRAGMA table_info(trabajo)')] != ['id', 'estado', 'detalle']:
            raise MaintenanceRejected('Unknown trabajo schema')
        if [row[1] for row in connection.execute('PRAGMA table_info(confirmado)')] != ['id', 'detalle']:
            raise MaintenanceRejected('Unknown confirmado schema')
        if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or connection.execute(
                'PRAGMA foreign_key_check').fetchall():
            raise MaintenanceRejected('Coordinator journal integrity check failed')
        jobs = connection.execute('SELECT id, estado, detalle FROM trabajo ORDER BY id').fetchall()
        confirmed = connection.execute('SELECT id, detalle FROM confirmado ORDER BY id').fetchall()
    if len(confirmed) != 1 or confirmed[0][0] != 1:
        raise MaintenanceRejected('Expected exactly one coordinator confirmation')
    for ident, state, detail_text in jobs:
        try:
            detail = json.loads(detail_text)
        except (TypeError, json.JSONDecodeError) as error:
            raise MaintenanceRejected(f'Unreadable coordinator job {ident}') from error
        if detail.get('id') != ident or detail.get('estado') != state or state not in {
                'completed', 'failed', 'interrupted'} or detail.get('fase') in {'publishing', 'recovering'}:
            raise MaintenanceRejected(f'Active or inconsistent coordinator job {ident}')
    try:
        meta = json.loads(confirmed[0][1])
    except (TypeError, json.JSONDecodeError) as error:
        raise MaintenanceRejected('Unreadable coordinator confirmation') from error
    return path, meta, jobs


def _control_journal(updates):
    path = Path(updates) / 'control' / 'jobs.sqlite'
    if not path.exists():
        return None, []
    with closing(_ro(path)) as connection:
        if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or connection.execute(
                'PRAGMA foreign_key_check').fetchall():
            raise MaintenanceRejected('Control journal integrity check failed')
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if tables != {'job'} or [row[1] for row in connection.execute('PRAGMA table_info(job)')] != ['id', 'idem', 'state', 'data']:
            raise MaintenanceRejected('Unknown control journal schema')
        jobs = connection.execute('SELECT id, idem, state, data FROM job ORDER BY id').fetchall()
    for ident, idem, state, data_text in jobs:
        try:
            data = json.loads(data_text)
        except (TypeError, json.JSONDecodeError) as error:
            raise MaintenanceRejected(f'Unreadable control job {ident}') from error
        if state in {'queued', 'running'} or data.get('id') != ident or data.get('status') != state or state not in {
                'completed', 'failed', 'cancelled', 'interrupted'}:
            raise MaintenanceRejected(f'Active or inconsistent control job {ident}')
    return path, jobs


def _history_sha(jobs):
    return _sha(_json(jobs))


def _validate_origin(base, scope, *, expected_code=SOURCE):
    base, scope = Path(base).resolve(), Path(scope).resolve()
    if actualizacion.codigo() != TARGET:
        raise MaintenanceRejected(f'Unexpected target coordinator fingerprint: {actualizacion.codigo()}')
    if carga.referencia_del_codigo()['sha256_agregada'] != TARGET:
        raise MaintenanceRejected('Loaded rules do not match the reviewed target fingerprint')
    if not base.is_file() or not scope.is_file():
        raise MaintenanceRejected('Base and scope must exist')
    scope_hash = _sha(scope.read_bytes())
    if scope_hash != SCOPE_SHA256:
        raise MaintenanceRejected(f'Unexpected scope fingerprint: {scope_hash}')
    description = sqlite.comprobar_copia(base)
    if description['revision'] != REVISION:
        raise MaintenanceRejected(f'Expected Alembic {REVISION}, got {description["revision"]}')
    verification = sincronizacion.verificar(base)
    if not verification['ok']:
        raise MaintenanceRejected(f'Sports state does not verify: {verification["problemas"]}')
    journal, meta, jobs = _journal(base)
    if meta.get('codigo') != expected_code or meta.get('ambito') != scope_hash or meta.get('descripcion') != description:
        raise MaintenanceRejected('Coordinator metadata is not the exact reviewed source state')
    try:
        generation = str(uuid.UUID(meta['generacion']))
    except (KeyError, TypeError, ValueError) as error:
        raise MaintenanceRejected('Invalid confirmed generation UUID') from error
    if generation != meta['generacion']:
        raise MaintenanceRejected('Noncanonical confirmed generation UUID')
    updates = journal.parent
    control, control_jobs = _control_journal(updates)
    return dict(base=base, scope=scope, scope_hash=scope_hash, description=description, journal=journal,
                meta=meta, generation=generation, jobs=jobs, control=control, control_jobs=control_jobs,
                table_inventory=_table_inventory(base), base_signature=_db_signature(base),
                journal_signature=_db_signature(journal), control_signature=_db_signature(control) if control else None)


def _snapshot(source, backup_dir):
    backup_dir = Path(backup_dir).resolve()
    if backup_dir == source['base'].parent or source['base'].parent in backup_dir.parents:
        raise MaintenanceRejected('Snapshot must be outside the sports data tree')
    os.mkdir(backup_dir, 0o700)
    payload = backup_dir / 'payload'
    payload.mkdir(mode=0o700)
    saved_base = payload / source['base'].name
    saved_updates = payload / (source['base'].name + '.actualizaciones')
    sqlite.copia_consistente(source['base'], saved_base)
    updates = source['journal'].parent
    control = updates / 'control' / 'jobs.sqlite'
    _copy_tree(updates, saved_updates, skip=(source['journal'], control))
    _copy_sqlite(source['journal'], saved_updates / 'jobs.sqlite')
    if source['control']:
        _copy_sqlite(source['control'], saved_updates / 'control' / 'jobs.sqlite')
    copies = source['base'].parent / 'copias'
    _copy_tree(copies, payload / 'copias')
    manifest = dict(format=FORMAT, source_base=str(source['base']), source_scope=str(source['scope']),
                    source_code=SOURCE, target_code=TARGET, scope_sha256=source['scope_hash'],
                    revision=REVISION, generation=source['generation'], description=source['description'],
                    source_meta=source['meta'],
                    base_signature=source['base_signature'], table_inventory=source['table_inventory'],
                    coordinator_jobs_sha256=_history_sha(source['jobs']), coordinator_job_count=len(source['jobs']),
                    coordinator_journal_sha256=source['journal_signature'],
                    control_journal_sha256=source['control_signature'], control_job_count=len(source['control_jobs']),
                    created_at=datetime.now(timezone.utc).isoformat())
    manifest['payload_sha256'] = {p.relative_to(backup_dir).as_posix(): _file_sha(p)
                                  for p in sorted(payload.rglob('*')) if p.is_file()}
    manifest['complete'] = True
    manifest['manifest_sha256'] = _sha(_json(manifest))
    _write_private(backup_dir / 'manifest.json', manifest)
    for path in [backup_dir, *backup_dir.rglob('*')]:
        os.chmod(path, 0o700 if path.is_dir() else 0o600)
    return manifest


def _load_snapshot(backup_dir):
    backup_dir = Path(backup_dir).resolve()
    try:
        manifest = json.loads((backup_dir / 'manifest.json').read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as error:
        raise MaintenanceRejected('No complete transition snapshot found') from error
    signature = manifest.pop('manifest_sha256', None)
    if manifest.get('complete') is not True or signature != _sha(_json(manifest)) or manifest.get('format') != FORMAT:
        raise MaintenanceRejected('Snapshot manifest is incomplete or has an invalid hash')
    manifest['manifest_sha256'] = signature
    actual = {p.relative_to(backup_dir).as_posix() for p in (backup_dir / 'payload').rglob('*') if p.is_file()}
    if actual != set(manifest['payload_sha256']):
        raise MaintenanceRejected('Snapshot file list differs from its manifest')
    for name, expected in manifest['payload_sha256'].items():
        path = backup_dir / name
        if _file_sha(path) != expected:
            raise MaintenanceRejected(f'Snapshot file hash mismatch: {name}')
    return backup_dir, manifest


def _verify_saved(base, backup_dir, manifest):
    if Path(manifest['source_base']).resolve() != Path(base).resolve():
        raise MaintenanceRejected('Snapshot belongs to another base path')
    payload = backup_dir / 'payload'
    saved_base = payload / Path(base).name
    saved_journal = payload / (Path(base).name + '.actualizaciones') / 'jobs.sqlite'
    if sqlite.comprobar_copia(saved_base) != manifest['description'] or _db_signature(saved_base) != manifest['base_signature']:
        raise MaintenanceRejected('Saved base differs from source description/hash')
    if _table_inventory(saved_base) != manifest['table_inventory'] or sqlite.revision(saved_base) != REVISION:
        raise MaintenanceRejected('Saved sporting tables or Alembic revision differ')
    if _db_signature(saved_journal) != manifest['coordinator_journal_sha256']:
        raise MaintenanceRejected('Saved coordinator journal differs')
    saved_control = payload / (Path(base).name + '.actualizaciones') / 'control' / 'jobs.sqlite'
    if manifest['control_journal_sha256'] is None:
        if saved_control.exists():
            raise MaintenanceRejected('Unexpected saved control journal')
    elif not saved_control.is_file() or _db_signature(saved_control) != manifest['control_journal_sha256']:
        raise MaintenanceRejected('Saved control journal differs')


def _adopt_metadata(journal, expected_code, new_code, expected_meta):
    with closing(sqlite3.connect(journal, timeout=10)) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        rows = connection.execute('SELECT id, detalle FROM confirmado ORDER BY id').fetchall()
        if len(rows) != 1 or rows[0][0] != 1:
            raise MaintenanceRejected('Coordinator confirmation changed during maintenance')
        current = json.loads(rows[0][1])
        if current != expected_meta or current.get('codigo') != expected_code:
            raise MaintenanceRejected('Coordinator metadata changed during maintenance')
        updated = dict(current, codigo=new_code)
        connection.execute('UPDATE confirmado SET detalle=? WHERE id=1',
                           (json.dumps(updated, ensure_ascii=False, sort_keys=True, separators=(',', ':')),))


def adopt(base, scope, backup_dir, confirmation, *, _checkpoint=lambda _name: None):
    base, scope, backup_dir = Path(base).resolve(), Path(scope).resolve(), Path(backup_dir).resolve()
    if confirmation != ADOPT_CONFIRM:
        raise MaintenanceRejected(f'Confirm exactly: {ADOPT_CONFIRM}')
    if backup_dir == base.parent or base.parent in backup_dir.parents:
        raise MaintenanceRejected('Snapshot must be outside the sports data volume')
    with bloquear(base):
        if backup_dir.exists():
            backup_dir, manifest = _load_snapshot(backup_dir)
            _verify_saved(base, backup_dir, manifest)
            if manifest['source_code'] != SOURCE or manifest['target_code'] != TARGET:
                raise MaintenanceRejected('Snapshot is not the reviewed fingerprint transition')
            with closing(_ro(base.with_name(base.name + '.actualizaciones') / 'jobs.sqlite')) as connection:
                detail = connection.execute('SELECT detalle FROM confirmado WHERE id=1').fetchone()
            try:
                current_code = json.loads(detail[0]).get('codigo')
            except (TypeError, json.JSONDecodeError, IndexError) as error:
                raise MaintenanceRejected('Current coordinator confirmation is unreadable') from error
            if current_code not in (SOURCE, TARGET):
                raise MaintenanceRejected('Coordinator fingerprint is neither the reviewed source nor target')
            current = _validate_origin(base, scope, expected_code=current_code)
            if (manifest['scope_sha256'], manifest['generation'], manifest['base_signature']) != (
                    current['scope_hash'], current['generation'], current['base_signature']):
                raise MaintenanceRejected('Current base no longer matches the reviewed snapshot')
            if manifest['source_meta'] != dict(current['meta'], codigo=SOURCE):
                raise MaintenanceRejected('Current coordinator metadata differs beyond the approved fingerprint')
        else:
            current = _validate_origin(base, scope)
            backup_dir.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if stat.S_IMODE(backup_dir.parent.stat().st_mode) & 0o077:
                raise MaintenanceRejected('Snapshot parent directory must be private (mode 0700)')
            manifest = _snapshot(current, backup_dir)
            backup_dir, manifest = _load_snapshot(backup_dir)
            _verify_saved(base, backup_dir, manifest)
            _checkpoint('snapshot_complete')
        if current['meta']['codigo'] == SOURCE:
            _adopt_metadata(current['journal'], SOURCE, TARGET, current['meta'])
            _checkpoint('adopted')
        updated = _journal(base)[1]
        if updated != dict(current['meta'], codigo=TARGET):
            raise MaintenanceRejected('Only coordinator codigo may change during adoption')
        if (_db_signature(base) != current['base_signature'] or _table_inventory(base) != current['table_inventory']
                or _journal(base)[2] != current['jobs']):
            raise MaintenanceRejected('Sports database or coordinator work history changed')
        if _db_signature(current['journal']) == manifest['coordinator_journal_sha256']:
            raise MaintenanceRejected('Coordinator journal was not updated')
        if current['control'] and _db_signature(current['control']) != current['control_signature']:
            raise MaintenanceRejected('HTTP control history changed')
        verification = sincronizacion.verificar(base)
        if not verification['ok']:
            raise MaintenanceRejected(f'Post-adoption sports verification failed: {verification["problemas"]}')
    with actualizacion.Coordinador(base, scope) as coordinator:
        generation = coordinator.capturar().id
    if generation != current['generation']:
        raise MaintenanceRejected('Coordinator opened a different generation after adoption')
    receipt = dict(format=FORMAT, action='adopted', manifest_sha256=manifest['manifest_sha256'],
                   source_code=SOURCE, target_code=TARGET, scope_sha256=current['scope_hash'],
                   generation=generation, description=current['description'],
                   coordinator_jobs_sha256=_history_sha(current['jobs']),
                   coordinator_job_count=len(current['jobs']), adopted_at=datetime.now(timezone.utc).isoformat())
    _write_private(backup_dir / 'adoption.json', receipt)
    os.chmod(backup_dir / 'adoption.json', 0o600)
    return receipt


def restore(base, scope, backup_dir, confirmation):
    base, scope = Path(base).resolve(), Path(scope).resolve()
    if confirmation != RESTORE_CONFIRM:
        raise MaintenanceRejected(f'Confirm exactly: {RESTORE_CONFIRM}')
    if actualizacion.codigo() != TARGET:
        raise MaintenanceRejected('Rollback tool does not match the reviewed target code')
    backup_dir, manifest = _load_snapshot(backup_dir)
    _verify_saved(base, backup_dir, manifest)
    if manifest['source_base'] != str(base) or _sha(scope.read_bytes()) != manifest['scope_sha256']:
        raise MaintenanceRejected('Base/scope differs from the adopted snapshot')
    adoption_path = backup_dir / 'adoption.json'
    try:
        adoption = json.loads(adoption_path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as error:
        raise MaintenanceRejected('No valid adoption receipt') from error
    if (adoption.get('format') != FORMAT or adoption.get('action') != 'adopted'
            or adoption.get('manifest_sha256') != manifest['manifest_sha256']
            or adoption.get('source_code') != SOURCE or adoption.get('target_code') != TARGET
            or adoption.get('generation') != manifest['generation']):
        raise MaintenanceRejected('No matching adoption receipt')
    with bloquear(base):
        description = sqlite.comprobar_copia(base)
        journal, meta, jobs = _journal(base)
        if description != manifest['description'] or meta.get('generacion') != manifest['generation']:
            raise MaintenanceRejected('State changed since the adoption; review before rollback')
        if _history_sha(jobs) != manifest['coordinator_jobs_sha256']:
            raise MaintenanceRejected('New coordinator work exists; rollback needs a separate review')
        if meta.get('codigo') == TARGET:
            _adopt_metadata(journal, TARGET, SOURCE, meta)
        elif meta.get('codigo') != SOURCE:
            raise MaintenanceRejected('Coordinator fingerprint is neither the reviewed source nor target')
        if _journal(base)[1] != dict(manifest['source_meta']):
            raise MaintenanceRejected('Rollback changed unexpected coordinator metadata')
        verification = sincronizacion.verificar(base)
        if not verification['ok']:
            raise MaintenanceRejected(f'Post-rollback sports verification failed: {verification["problemas"]}')
    _write_private(backup_dir / 'restore.json', dict(format=FORMAT, action='restored',
                   manifest_sha256=manifest['manifest_sha256'], source_code=TARGET, target_code=SOURCE,
                   generation=manifest['generation'], restored_at=datetime.now(timezone.utc).isoformat()))
    return dict(format=FORMAT, action='restored', source_code=TARGET, target_code=SOURCE,
                generation=manifest['generation'], restored_at=datetime.now(timezone.utc).isoformat())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    for name in ('adopt', 'restore'):
        item = sub.add_parser(name)
        item.add_argument('--base', type=Path, required=True)
        item.add_argument('--scope', type=Path, required=True)
        item.add_argument('--backup-dir', type=Path, required=True)
        item.add_argument('--confirm', required=True)
    args = parser.parse_args()
    result = (adopt(args.base, args.scope, args.backup_dir, args.confirm) if args.action == 'adopt'
              else restore(args.base, args.scope, args.backup_dir, args.confirm))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
