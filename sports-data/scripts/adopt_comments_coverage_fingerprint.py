"""Adopt or restore the reviewed comments-coverage fix without changing SQLite sports data.

This maintenance is pinned to one code-fingerprint pair and one confirmed generation. It does not run Alembic,
start Acquisition, or make network requests. Stop the three project services before use; cooperative locks and
journal checks are additional safeguards, not a substitute for stopping them.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from persistencia import actualizacion, carga, sqlite, sincronizacion  # noqa: E402
from persistencia.bloqueo import bloquear  # noqa: E402

SOURCE = '4f5ba2ff054951cdcb2ce04f971e8b1e36c57d6d4eb73a4f5c69e44f5d8e6a1b'
TARGET = '376f2cdc92f351ab9fa7056eb584a0e3813f1cf61fe0d9a6b362a996ce7cc7fe'
SOURCE_COMMIT = 'ce27eaf8d1136f5598c8bf4ba57e8a7a4bafcd0c'
TARGET_COMMIT = 'a76af75c1cbf651feb363184251266baf9f75dd8'
SCOPE_SHA256 = 'a791c80eb18cf028f3af658a2dfe19d954168e2c4b28810c5e5d73a45980f164'
GENERATION = 'e07d528a-2f19-47e7-ae28-30f09b285042'
REVISION = '0008'
FORMAT = 'real-jaen-comments-coverage-fingerprint-transition-v1'
ADOPT_CONFIRM = f'adopt coordinator fingerprint {SOURCE} -> {TARGET} generation {GENERATION} services-stopped'
RESTORE_CONFIRM = f'restore coordinator fingerprint {TARGET} -> {SOURCE} generation {GENERATION} services-stopped'


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


def _regular_path(path, label):
    original = Path(path).expanduser()
    if original.is_symlink():
        raise MaintenanceRejected(f'{label} cannot be a symbolic link')
    resolved = original.resolve()
    if not resolved.is_file():
        raise MaintenanceRejected(f'{label} must be an existing regular file')
    return resolved


def _ro(path):
    return sqlite3.connect(f'{Path(path).resolve().as_uri()}?mode=ro', uri=True)


def _db_signature(path):
    digest = hashlib.sha256()
    with closing(_ro(path)) as connection:
        objects = connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        ).fetchall()
        digest.update(_json(objects))
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


def _schema_signature(path):
    with closing(_ro(path)) as connection:
        objects = connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        ).fetchall()
    return _sha(_json(objects))


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


def _fsync_file(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_dir(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


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
        dirs[:] = [name for name in dirs if (current / name).resolve() not in skip]
        for name in files:
            item = current / name
            if item.is_symlink():
                raise MaintenanceRejected(f'Symlink is not allowed in snapshot: {item}')
            if item.resolve() in skip or name.endswith('.lock'):
                continue
            target = target_dir / name
            with item.open('rb') as source_stream, target.open('xb') as target_stream:
                shutil.copyfileobj(source_stream, target_stream, length=1024 * 1024)
                target_stream.flush()
                os.fsync(target_stream.fileno())
            os.chmod(target, 0o600)
        _fsync_dir(target_dir)
    directories = [Path(current) / name for current, names, _files in os.walk(destination, topdown=True)
                   for name in names]
    for directory in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        _fsync_dir(directory)
    _fsync_dir(destination)


def _copy_sqlite(source, destination):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with closing(_ro(source)) as original, closing(sqlite3.connect(destination)) as copy:
        original.backup(copy)
        copy.execute('PRAGMA journal_mode=DELETE')
    os.chmod(destination, 0o600)
    _fsync_file(destination)
    _fsync_dir(destination.parent)
    _db_signature(destination)


def _inventory_tree(root, excluded=()):
    root = Path(root).resolve()
    excluded = {Path(item).resolve() for item in excluded}
    inventory = {}
    for current, dirs, files in os.walk(root, followlinks=False):
        current = Path(current)
        for name in list(dirs):
            item = current / name
            if item.is_symlink():
                raise MaintenanceRejected(f'Symlink is not allowed in state artifacts: {item}')
        dirs[:] = [name for name in dirs if (current / name).resolve() not in excluded]
        for name in files:
            item = current / name
            if item.is_symlink():
                raise MaintenanceRejected(f'Symlink is not allowed in state artifacts: {item}')
            if item.resolve() in excluded or name.endswith('.lock'):
                continue
            if not item.is_file():
                raise MaintenanceRejected(f'Non-regular state artifact: {item}')
            relative = item.relative_to(root).as_posix()
            inventory[relative] = {'size': item.stat().st_size, 'sha256': _file_sha(item)}
    return dict(sorted(inventory.items()))


def _tree_bytes(root, excluded=()):
    return sum(item['size'] for item in _inventory_tree(root, excluded).values())


def _check_backup_space(source, backup_dir):
    paths = _db_paths(source['base'], source['acquisition_journal'])
    excluded = set(paths.values())
    excluded.update(Path(str(path) + suffix) for path in paths.values() for suffix in sqlite.ACOMPANANTES)
    required = (_tree_bytes(source['base'].parent, {*excluded, source['journal'].parent})
                + _tree_bytes(source['journal'].parent, (source['journal'], source['control']))
                + _tree_bytes(source['acquisition_journal'].parent, excluded)
                + sum(path.stat().st_size for path in paths.values()) * 2 + 256 * 1024 * 1024)
    free = shutil.disk_usage(backup_dir.parent).free
    if free < required:
        raise MaintenanceRejected(f'Insufficient free space for verified snapshot: need {required} bytes, have {free}')
    return {'required_bytes': required, 'free_bytes': free}


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
        indexes = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'")}
        if indexes != {'unico_activo'}:
            raise MaintenanceRejected('Unknown coordinator journal indexes')
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


def _locked(paths):
    stack = ExitStack()
    try:
        for path in sorted({Path(item).resolve() for item in paths}, key=str):
            stack.enter_context(bloquear(path))
    except BaseException:
        stack.close()
        raise
    return stack


def _db_paths(base, acquisition_journal):
    updates = Path(str(base) + '.actualizaciones')
    return {
        'sports': Path(base),
        'coordinator': updates / 'jobs.sqlite',
        'control': updates / 'control' / 'jobs.sqlite',
        'acquisition': Path(acquisition_journal),
    }


def _sidecars(path):
    result = {}
    for suffix in sqlite.ACOMPANANTES:
        companion = Path(str(path) + suffix)
        if companion.is_symlink() or (companion.exists() and not companion.is_file()):
            raise MaintenanceRejected(f'Invalid SQLite sidecar: {companion}')
        result[suffix] = ({'size': companion.stat().st_size, 'sha256': _file_sha(companion)}
                          if companion.exists() else None)
        if suffix == '-journal' and result[suffix] and result[suffix]['size']:
            raise MaintenanceRejected(f'Unresolved rollback journal: {companion}')
    return result


def _acquisition_journal(path):
    path = Path(path).resolve()
    if not path.is_file():
        raise MaintenanceRejected('Missing Acquisition jobs.sqlite')
    with closing(_ro(path)) as connection:
        if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or connection.execute(
                'PRAGMA foreign_key_check').fetchall():
            raise MaintenanceRejected('Acquisition journal integrity check failed')
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        columns = tuple(row[1] for row in connection.execute('PRAGMA table_info(job)'))
        if tables != {'job'} or columns != ('id', 'request', 'state', 'data'):
            raise MaintenanceRejected('Unknown Acquisition journal schema')
        rows = connection.execute('SELECT id, request, state, data FROM job ORDER BY id').fetchall()
    for ident, _request, state, data_text in rows:
        try:
            detail = json.loads(data_text)
        except (TypeError, json.JSONDecodeError) as error:
            raise MaintenanceRejected(f'Unreadable Acquisition job {ident}') from error
        if (state in {'queued', 'running', 'cancelling'} or state not in {
                'completed', 'failed', 'cancelled', 'interrupted'} or not isinstance(detail, dict)
                or detail.get('id') != ident):
            raise MaintenanceRejected(f'Active or inconsistent Acquisition job {ident}')
    return path, rows


def _validate_origin(base, scope, acquisition_journal, *, expected_code=SOURCE):
    base, scope = Path(base).resolve(), Path(scope).resolve()
    if actualizacion.codigo() != TARGET:
        raise MaintenanceRejected(f'Unexpected target coordinator fingerprint: {actualizacion.codigo()}')
    if carga.referencia_del_codigo()['sha256_agregada'] != TARGET:
        raise MaintenanceRejected('Loaded rules do not match the reviewed target fingerprint')
    acquisition_journal = Path(acquisition_journal).resolve()
    if not base.is_file() or base.is_symlink() or not scope.is_file() or scope.is_symlink():
        raise MaintenanceRejected('Base and scope must be regular files')
    scope_hash = _sha(scope.read_bytes())
    if scope_hash != SCOPE_SHA256:
        raise MaintenanceRejected(f'Unexpected scope fingerprint: {scope_hash}')
    description = sqlite.comprobar_copia(base)
    if description['revision'] != REVISION:
        raise MaintenanceRejected(f'Expected Alembic {REVISION}, got {description["revision"]}')
    verification = sincronizacion.verificar(base)
    if not verification['ok']:
        raise MaintenanceRejected(f'Sports state does not verify: {verification["problemas"]}')
    if verification.get('cambios_en_seco'):
        raise MaintenanceRejected('Sports state would change during dry synchronization')
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
    if control is None:
        raise MaintenanceRejected('Missing HTTP control journal')
    acq_path, acq_jobs = _acquisition_journal(acquisition_journal)
    if generation != GENERATION:
        raise MaintenanceRejected(f'Unexpected confirmed generation: {generation}')
    paths = _db_paths(base, acq_path)
    sidecars = {label: _sidecars(path) for label, path in paths.items()}
    return dict(base=base, scope=scope, scope_hash=scope_hash, description=description, journal=journal,
                meta=meta, generation=generation, jobs=jobs, control=control, control_jobs=control_jobs,
                acquisition_journal=acq_path, acquisition_jobs=acq_jobs,
                table_inventory=_table_inventory(base), base_signature=_db_signature(base),
                journal_signature=_db_signature(journal), control_signature=_db_signature(control),
                acquisition_signature=_db_signature(acq_path), sidecars=sidecars)


def _snapshot(source, backup_dir):
    backup_dir = Path(backup_dir).resolve()
    if backup_dir == source['base'].parent or source['base'].parent in backup_dir.parents:
        raise MaintenanceRejected('Snapshot must be outside the sports data tree')
    if backup_dir == source['acquisition_journal'].parent or source['acquisition_journal'].parent in backup_dir.parents:
        raise MaintenanceRejected('Snapshot must be outside the Acquisition volume')
    space = _check_backup_space(source, backup_dir)
    os.mkdir(backup_dir, 0o700)
    payload = backup_dir / 'payload'
    payload.mkdir(mode=0o700)
    db_payload = payload / 'databases'
    db_payload.mkdir(mode=0o700)
    paths = _db_paths(source['base'], source['acquisition_journal'])
    destinations = {label: db_payload / f'{label}.sqlite' for label in paths}
    sidecars_before = {label: _sidecars(path) for label, path in paths.items()}
    updates = source['journal'].parent
    control = updates / 'control' / 'jobs.sqlite'
    state_exclusions = set(paths.values())
    state_exclusions.add(updates)
    state_exclusions.update(Path(str(path) + suffix) for path in paths.values()
                            for suffix in sqlite.ACOMPANANTES)
    state_inventory = _inventory_tree(source['base'].parent, state_exclusions)
    updates_exclusions = (source['journal'], control,
                          *(Path(str(path) + suffix) for path in (source['journal'], control)
                            for suffix in sqlite.ACOMPANANTES))
    updates_inventory = _inventory_tree(updates, updates_exclusions)
    acq_inventory = _inventory_tree(source['acquisition_journal'].parent, state_exclusions)
    for label, path in paths.items():
        _copy_sqlite(path, destinations[label])
    saved_base = payload / source['base'].name
    saved_updates = payload / (source['base'].name + '.actualizaciones')
    _copy_tree(updates, saved_updates, skip=updates_exclusions)
    if _inventory_tree(saved_updates) != updates_inventory:
        raise MaintenanceRejected('Coordinator artifacts changed while the snapshot was being copied')
    # Preserve every other root-level artifact (including procedencia and copias), not only known directories.
    root_lock = source['base'].with_name(source['base'].name + '.lock')
    root_extras = payload / 'state-extras'
    _copy_tree(source['base'].parent, root_extras,
               skip=(source['base'], updates, root_lock,
                     *(source['base'].with_name(source['base'].name + suffix) for suffix in sqlite.ACOMPANANTES)))
    acq_root = source['acquisition_journal'].parent
    acq_extras = payload / 'acquisition-extras'
    _copy_tree(acq_root, acq_extras,
               skip=(source['acquisition_journal'],
                     *(Path(str(source['acquisition_journal']) + suffix) for suffix in sqlite.ACOMPANANTES)))
    saved_scope = payload / 'scope.json'
    shutil.copyfile(source['scope'], saved_scope)
    os.chmod(saved_scope, 0o600)
    _fsync_file(saved_scope)
    _fsync_dir(payload)
    sidecars_after = {label: _sidecars(path) for label, path in paths.items()}
    if sidecars_before != sidecars_after or sidecars_before != source['sidecars']:
        raise MaintenanceRejected('SQLite sidecars changed during the coherent snapshot')
    if state_inventory != _inventory_tree(source['base'].parent, state_exclusions):
        raise MaintenanceRejected('State artifacts changed while the snapshot was being copied')
    if updates_inventory != _inventory_tree(updates, updates_exclusions):
        raise MaintenanceRejected('Coordinator artifacts changed while the snapshot was being copied')
    if acq_inventory != _inventory_tree(source['acquisition_journal'].parent, state_exclusions):
        raise MaintenanceRejected('Acquisition artifacts changed while the snapshot was being copied')
    for label, path in paths.items():
        if _db_signature(destinations[label]) != source[
                {'sports': 'base_signature', 'coordinator': 'journal_signature',
                 'control': 'control_signature', 'acquisition': 'acquisition_signature'}[label]]:
            raise MaintenanceRejected(f'SQLite backup signature changed: {label}')
    manifest = dict(format=FORMAT, source_base=str(source['base']), source_scope=str(source['scope']),
                    source_acquisition_journal=str(source['acquisition_journal']),
                    source_code=SOURCE, target_code=TARGET,
                    source_code_commit=SOURCE_COMMIT, target_code_commit=TARGET_COMMIT,
                    scope_sha256=source['scope_hash'],
                    revision=REVISION, generation=source['generation'], description=source['description'],
                    source_meta=source['meta'],
                    base_signature=source['base_signature'], table_inventory=source['table_inventory'],
                    coordinator_jobs_sha256=_history_sha(source['jobs']), coordinator_job_count=len(source['jobs']),
                    coordinator_journal_sha256=source['journal_signature'],
                    coordinator_schema_sha256=_schema_signature(source['journal']),
                    control_journal_sha256=source['control_signature'],
                    control_jobs_sha256=_history_sha(source['control_jobs']),
                    control_job_count=len(source['control_jobs']),
                    acquisition_journal_sha256=source['acquisition_signature'],
                    acquisition_jobs_sha256=_history_sha(source['acquisition_jobs']),
                    acquisition_job_count=len(source['acquisition_jobs']),
                    other_journal_signatures={label: _db_signature(path) for label, path in paths.items()},
                    sidecars=sidecars_before, state_artifacts=state_inventory,
                    updates_artifacts=updates_inventory,
                    acquisition_artifacts=acq_inventory,
                    scope_file_sha256=_file_sha(saved_scope),
                    space_check=space,
                    created_at=datetime.now(timezone.utc).isoformat())
    manifest['payload_sha256'] = {p.relative_to(backup_dir).as_posix(): _file_sha(p)
                                  for p in sorted(payload.rglob('*')) if p.is_file()}
    manifest['complete'] = True
    manifest['manifest_sha256'] = _sha(_json(manifest))
    _write_private(backup_dir / 'manifest.json', manifest)
    for path in [backup_dir, *backup_dir.rglob('*')]:
        os.chmod(path, 0o700 if path.is_dir() else 0o600)
        if path.is_file():
            _fsync_file(path)
        else:
            _fsync_dir(path)
    _fsync_dir(backup_dir)
    _fsync_dir(backup_dir.parent)
    return manifest


def _load_snapshot(backup_dir):
    backup_dir = Path(backup_dir).resolve()
    if backup_dir.is_symlink() or not backup_dir.is_dir() or stat.S_IMODE(backup_dir.stat().st_mode) & 0o077:
        raise MaintenanceRejected('Snapshot directory must be private and regular')
    try:
        manifest_path = backup_dir / 'manifest.json'
        if (manifest_path.is_symlink() or not manifest_path.is_file()
                or stat.S_IMODE(manifest_path.stat().st_mode) & 0o077):
            raise MaintenanceRejected('Snapshot manifest must be a private regular file')
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as error:
        raise MaintenanceRejected('No complete transition snapshot found') from error
    signature = manifest.pop('manifest_sha256', None)
    if manifest.get('complete') is not True or signature != _sha(_json(manifest)) or manifest.get('format') != FORMAT:
        raise MaintenanceRejected('Snapshot manifest is incomplete or has an invalid hash')
    manifest['manifest_sha256'] = signature
    payload = backup_dir / 'payload'
    for path in payload.rglob('*'):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise MaintenanceRejected(f'Unexpected snapshot entry: {path}')
        if stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise MaintenanceRejected(f'Snapshot entry is not private: {path}')
    actual = {p.relative_to(backup_dir).as_posix() for p in payload.rglob('*') if p.is_file()}
    if actual != set(manifest['payload_sha256']):
        raise MaintenanceRejected('Snapshot file list differs from its manifest')
    for name, expected in manifest['payload_sha256'].items():
        path = backup_dir / name
        if _file_sha(path) != expected:
            raise MaintenanceRejected(f'Snapshot file hash mismatch: {name}')
    return backup_dir, manifest


def _verify_saved(base, acquisition_journal, backup_dir, manifest):
    if (Path(manifest['source_base']).resolve() != Path(base).resolve()
            or Path(manifest['source_acquisition_journal']).resolve() != Path(acquisition_journal).resolve()):
        raise MaintenanceRejected('Snapshot belongs to another base path')
    if (manifest.get('format') != FORMAT or manifest.get('complete') is not True
            or manifest.get('source_code') != SOURCE or manifest.get('target_code') != TARGET
            or manifest.get('source_code_commit') != SOURCE_COMMIT
            or manifest.get('target_code_commit') != TARGET_COMMIT
            or manifest.get('revision') != REVISION or manifest.get('generation') != GENERATION
            or manifest.get('scope_sha256') != SCOPE_SHA256
            or manifest.get('source_meta', {}).get('codigo') != SOURCE):
        raise MaintenanceRejected('Snapshot is not the exact reviewed fingerprint transition')
    payload = backup_dir / 'payload'
    saved_base = payload / 'databases' / 'sports.sqlite'
    saved_journal = payload / 'databases' / 'coordinator.sqlite'
    if sqlite.comprobar_copia(saved_base) != manifest['description'] or _db_signature(saved_base) != manifest['base_signature']:
        raise MaintenanceRejected('Saved base differs from source description/hash')
    if _table_inventory(saved_base) != manifest['table_inventory'] or sqlite.revision(saved_base) != REVISION:
        raise MaintenanceRejected('Saved sporting tables or Alembic revision differ')
    if _db_signature(saved_journal) != manifest['coordinator_journal_sha256']:
        raise MaintenanceRejected('Saved coordinator journal differs')
    saved_control = payload / 'databases' / 'control.sqlite'
    if not saved_control.is_file() or _db_signature(saved_control) != manifest['control_journal_sha256']:
        raise MaintenanceRejected('Saved control journal differs')
    saved_acquisition = payload / 'databases' / 'acquisition.sqlite'
    if not saved_acquisition.is_file() or _db_signature(saved_acquisition) != manifest['acquisition_journal_sha256']:
        raise MaintenanceRejected('Saved Acquisition journal differs')
    for path in (saved_base, saved_journal, saved_control, saved_acquisition):
        if any(Path(str(path) + suffix).exists() for suffix in sqlite.ACOMPANANTES):
            raise MaintenanceRejected(f'Saved SQLite is not standalone: {path.name}')
    if manifest.get('other_journal_signatures') != {
            'sports': _db_signature(saved_base), 'coordinator': _db_signature(saved_journal),
            'control': _db_signature(saved_control), 'acquisition': _db_signature(saved_acquisition)}:
        raise MaintenanceRejected('Saved journal inventory differs from the manifest')
    if _schema_signature(saved_journal) != manifest.get('coordinator_schema_sha256'):
        raise MaintenanceRejected('Saved coordinator schema differs from the manifest')
    if _inventory_tree(payload / 'state-extras') != manifest.get('state_artifacts'):
        raise MaintenanceRejected('Saved state artifacts differ from the manifest')
    saved_updates = payload / (Path(base).name + '.actualizaciones')
    if _inventory_tree(saved_updates) != manifest.get('updates_artifacts'):
        raise MaintenanceRejected('Saved coordinator artifacts differ from the manifest')
    if _inventory_tree(payload / 'acquisition-extras') != manifest.get('acquisition_artifacts'):
        raise MaintenanceRejected('Saved Acquisition artifacts differ from the manifest')
    saved_scope = payload / 'scope.json'
    if not saved_scope.is_file() or _file_sha(saved_scope) != manifest.get('scope_file_sha256'):
        raise MaintenanceRejected('Saved scope file differs from the manifest')


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


def _assert_snapshot_matches(source, manifest, *, expected_code=SOURCE):
    if (manifest.get('source_base') != str(source['base'])
            or manifest.get('source_acquisition_journal') != str(source['acquisition_journal'])
            or manifest.get('source_code') != SOURCE or manifest.get('target_code') != TARGET
            or manifest.get('scope_sha256') != source['scope_hash']
            or manifest.get('generation') != source['generation']
            or manifest.get('description') != source['description']
            or manifest.get('base_signature') != source['base_signature']
            or manifest.get('table_inventory') != source['table_inventory']
            or manifest.get('source_meta') != dict(source['meta'], codigo=SOURCE)):
        raise MaintenanceRejected('Current state does not match the reviewed snapshot')
    if _history_sha(source['jobs']) != manifest.get('coordinator_jobs_sha256'):
        raise MaintenanceRejected('Coordinator work history differs from the snapshot')
    if (_history_sha(source['control_jobs']) != manifest.get('control_jobs_sha256')
            or _db_signature(source['control']) != manifest.get('control_journal_sha256')):
        raise MaintenanceRejected('HTTP control history differs from the snapshot')
    if (_history_sha(source['acquisition_jobs']) != manifest.get('acquisition_jobs_sha256')
            or _db_signature(source['acquisition_journal']) != manifest.get('acquisition_journal_sha256')):
        raise MaintenanceRejected('Acquisition history differs from the snapshot')
    if source['sidecars'] != manifest.get('sidecars'):
        # Only the coordinator journal is allowed to gain SQLite sidecars from its single metadata transaction.
        if expected_code != TARGET:
            raise MaintenanceRejected('SQLite sidecars changed from the source snapshot')
        expected = dict(manifest['sidecars'])
        actual = dict(source['sidecars'])
        expected.pop('coordinator', None)
        actual.pop('coordinator', None)
        if actual != expected:
            raise MaintenanceRejected('SQLite sidecars changed since the snapshot')
    if _schema_signature(source['journal']) != manifest.get('coordinator_schema_sha256'):
        raise MaintenanceRejected('Coordinator journal schema differs from the snapshot')
    if source['meta'].get('codigo') != expected_code:
        raise MaintenanceRejected('Confirmed coordinator code is not the expected transition side')


def _current_artifact_inventories(source):
    paths = _db_paths(source['base'], source['acquisition_journal'])
    exclusions = set(paths.values())
    exclusions.update(Path(str(path) + suffix) for path in paths.values()
                      for suffix in sqlite.ACOMPANANTES)
    updates = source['journal'].parent
    exclusions.add(updates)
    control = updates / 'control' / 'jobs.sqlite'
    return (
        _inventory_tree(source['base'].parent, exclusions),
        _inventory_tree(updates, (source['journal'], control,
                                  *(Path(str(path) + suffix) for path in (source['journal'], control)
                                    for suffix in sqlite.ACOMPANANTES))),
        _inventory_tree(source['acquisition_journal'].parent, exclusions),
    )


def _source_runtime_probe(source_runtime_root, *, base=None, scope=None, open_coordinator=False):
    root = Path(source_runtime_root).resolve()
    if not root.is_dir() or root.is_symlink():
        raise MaintenanceRejected('The source runtime directory is missing or invalid')
    code = (
        'import json,sys; sys.path.insert(0,sys.argv[1]); '
        'from persistencia import actualizacion,carga; '
        'print(json.dumps({"code":actualizacion.codigo(),"loaded":carga.referencia_del_codigo()["sha256_agregada"]}));'
    )
    env = dict(os.environ, PYTHONPATH=str(root), PYTHONDONTWRITEBYTECODE='1')
    check = subprocess.run([sys.executable, '-B', '-c', code, str(root)], cwd=root, env=env,
                           capture_output=True, text=True, timeout=30)
    if check.returncode:
        raise MaintenanceRejected('Could not inspect the prior runtime fingerprint')
    try:
        detail = json.loads(check.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as error:
        raise MaintenanceRejected('Prior runtime returned an unreadable fingerprint') from error
    if detail != {'code': SOURCE, 'loaded': SOURCE}:
        raise MaintenanceRejected('Prior runtime does not exactly match the confirmed source fingerprint')
    if not open_coordinator:
        return {'source_runtime_code': SOURCE}
    if base is None or scope is None:
        raise MaintenanceRejected('Base and scope are required for the prior runtime probe')
    probe = '''
import json, sys
sys.path.insert(0, sys.argv[1])
from persistencia import actualizacion, carga
base, scope, generation, expected = sys.argv[2:6]
assert actualizacion.codigo() == carga.referencia_del_codigo()["sha256_agregada"]
assert actualizacion.codigo() == expected
with actualizacion.Coordinador(base, scope) as coordinator:
    captured = coordinator.capturar().id
assert captured == generation
print(json.dumps({"generation": captured, "code": actualizacion.codigo()}))
'''
    run = subprocess.run([sys.executable, '-B', '-c', probe, str(root), str(base), str(scope), GENERATION,
                          SOURCE], cwd=root, env=env, capture_output=True, text=True, timeout=60)
    if run.returncode:
        diagnostic = (run.stderr or run.stdout).strip()[-2000:]
        raise MaintenanceRejected('Prior runtime could not open the restored generation'
                                  + (f': {diagnostic}' if diagnostic else ''))
    try:
        result = json.loads(run.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as error:
        raise MaintenanceRejected('Prior runtime open probe returned an unreadable result') from error
    if result != {'generation': GENERATION, 'code': SOURCE}:
        raise MaintenanceRejected('Prior runtime opened a different generation')
    return result


def _write_receipt(path, receipt):
    receipt = dict(receipt)
    receipt['receipt_sha256'] = _sha(_json(receipt))
    _write_private(path, receipt)
    os.chmod(path, 0o600)
    return receipt


def _adoption_receipt(manifest, *, open_verified, adopted_at=None):
    return dict(format=FORMAT, action='adopted', manifest_sha256=manifest['manifest_sha256'],
                source_code=SOURCE, target_code=TARGET, scope_sha256=manifest['scope_sha256'],
                generation=GENERATION, revision=REVISION, description=manifest['description'],
                base_signature=manifest['base_signature'],
                coordinator_jobs_sha256=manifest['coordinator_jobs_sha256'],
                control_jobs_sha256=manifest['control_jobs_sha256'],
                acquisition_jobs_sha256=manifest['acquisition_jobs_sha256'],
                coordinator_open_verified=open_verified,
                adopted_at=adopted_at or datetime.now(timezone.utc).isoformat())


def _read_receipt(path, action, manifest):
    try:
        receipt = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as error:
        raise MaintenanceRejected(f'Missing or unreadable {action} receipt') from error
    signature = receipt.pop('receipt_sha256', None)
    if signature != _sha(_json(receipt)) or receipt.get('action') != action:
        raise MaintenanceRejected(f'Invalid {action} receipt')
    receipt['receipt_sha256'] = signature
    if receipt.get('manifest_sha256') != manifest['manifest_sha256']:
        raise MaintenanceRejected(f'{action} receipt belongs to another snapshot')
    return receipt


def _verify_target_open(base, scope, generation):
    with actualizacion.Coordinador(base, scope) as coordinator:
        captured = coordinator.capturar().id
    if captured != generation:
        raise MaintenanceRejected('The coordinator opened a different generation after adoption')


def prepare(base, scope, acquisition_journal, backup_dir):
    """Validate the live source and create a private, hashed snapshot without changing confirmed state."""
    base, scope = _regular_path(base, 'Sports SQLite'), _regular_path(scope, 'Scope file')
    acquisition_journal = _regular_path(acquisition_journal, 'Acquisition journal')
    backup_input = Path(backup_dir).expanduser()
    if backup_input.is_symlink():
        raise MaintenanceRejected('Snapshot destination cannot be a symbolic link')
    backup_dir = backup_input.resolve()
    if backup_dir.exists() or backup_dir.is_symlink():
        raise MaintenanceRejected('Snapshot destination already exists; choose a new private directory')
    for protected in (base.parent, acquisition_journal.parent):
        if backup_dir == protected or protected in backup_dir.parents or backup_dir in protected.parents:
            raise MaintenanceRejected('Snapshot must be outside the state and Acquisition trees')
    if backup_dir == ROOT or ROOT in backup_dir.parents:
        raise MaintenanceRejected('Snapshot must be outside the API repository')
    with _locked([*_db_paths(base, acquisition_journal).values()]):
        source = _validate_origin(base, scope, acquisition_journal)
        backup_dir.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if stat.S_IMODE(backup_dir.parent.stat().st_mode) & 0o077:
            raise MaintenanceRejected('Snapshot parent must be private (mode 0700)')
        manifest = _snapshot(source, backup_dir)
        backup_dir, manifest = _load_snapshot(backup_dir)
        _verify_saved(base, acquisition_journal, backup_dir, manifest)
    return dict(format=FORMAT, action='prepared', manifest_sha256=manifest['manifest_sha256'],
                source_code=SOURCE, target_code=TARGET, scope_sha256=source['scope_hash'],
                generation=source['generation'], revision=REVISION, description=source['description'],
                base_signature=source['base_signature'], coordinator_job_count=len(source['jobs']),
                control_job_count=len(source['control_jobs']), acquisition_job_count=len(source['acquisition_jobs']),
                payload_file_count=len(manifest['payload_sha256']), prepared_at=datetime.now(timezone.utc).isoformat())


def adopt(base, scope, acquisition_journal, backup_dir, confirmation, *, _checkpoint=lambda _name: None):
    base, scope = _regular_path(base, 'Sports SQLite'), _regular_path(scope, 'Scope file')
    acquisition_journal = _regular_path(acquisition_journal, 'Acquisition journal')
    backup_dir = Path(backup_dir).expanduser().resolve()
    if confirmation != ADOPT_CONFIRM:
        raise MaintenanceRejected(f'Confirm exactly: {ADOPT_CONFIRM}')
    if backup_dir == base.parent or base.parent in backup_dir.parents:
        raise MaintenanceRejected('Snapshot must be outside the sports data volume')
    if backup_dir == acquisition_journal.parent or acquisition_journal.parent in backup_dir.parents:
        raise MaintenanceRejected('Snapshot must be outside the Acquisition volume')
    backup_dir, manifest = _load_snapshot(backup_dir)
    _verify_saved(base, acquisition_journal, backup_dir, manifest)
    with _locked([*_db_paths(base, acquisition_journal).values()]):
        with closing(_ro(Path(str(base) + '.actualizaciones') / 'jobs.sqlite')) as connection:
            row = connection.execute('SELECT detalle FROM confirmado WHERE id=1').fetchone()
        try:
            current_code = json.loads(row[0]).get('codigo')
        except (TypeError, json.JSONDecodeError, IndexError) as error:
            raise MaintenanceRejected('Current coordinator confirmation is unreadable') from error
        if current_code not in (SOURCE, TARGET):
            raise MaintenanceRejected('Coordinator fingerprint is neither the reviewed source nor target')
        current = _validate_origin(base, scope, acquisition_journal, expected_code=current_code)
        _assert_snapshot_matches(current, manifest, expected_code=current_code)
        state_inventory, updates_inventory, acq_inventory = _current_artifact_inventories(current)
        if (state_inventory != manifest['state_artifacts'] or updates_inventory != manifest['updates_artifacts']
                or acq_inventory != manifest['acquisition_artifacts']):
            raise MaintenanceRejected('State or Acquisition artifacts changed since the snapshot')
        if current_code == SOURCE:
            _adopt_metadata(current['journal'], SOURCE, TARGET, current['meta'])
            # Persist a recoverable receipt before probing the new runtime. If opening it fails,
            # restore can still prove exactly which metadata transition was made.
            try:
                _write_receipt(backup_dir / 'adoption.json',
                               _adoption_receipt(manifest, open_verified=False))
            except Exception as error:
                # If the receipt cannot be made durable, put the metadata back only after
                # re-proving that nothing except confirmed.codigo changed.
                try:
                    changed = _validate_origin(base, scope, acquisition_journal, expected_code=TARGET)
                    _assert_snapshot_matches(changed, manifest, expected_code=TARGET)
                    if _current_artifact_inventories(changed) != (
                            manifest['state_artifacts'], manifest['updates_artifacts'],
                            manifest['acquisition_artifacts']):
                        raise MaintenanceRejected('Artifacts changed before receipt could be persisted')
                    _adopt_metadata(changed['journal'], TARGET, SOURCE, changed['meta'])
                except Exception as rollback_error:
                    raise MaintenanceRejected(
                        'Could not persist the adoption receipt and could not prove a safe metadata rollback'
                    ) from rollback_error
                raise MaintenanceRejected('Could not persist the adoption receipt; source fingerprint restored') from error
            _checkpoint('adopted')
        _, updated, updated_jobs = _journal(base)
        if updated != dict(manifest['source_meta'], codigo=TARGET):
            raise MaintenanceRejected('Only confirmed.codigo may change during adoption')
        if (_db_signature(base) != manifest['base_signature']
                or _table_inventory(base) != manifest['table_inventory']
                or _history_sha(updated_jobs) != manifest['coordinator_jobs_sha256']):
            raise MaintenanceRejected('Sports data or coordinator history changed during adoption')
        if (_db_signature(current['control']) != manifest['control_journal_sha256']
                or _db_signature(current['acquisition_journal']) != manifest['acquisition_journal_sha256']):
            raise MaintenanceRejected('Control or Acquisition history changed during adoption')
        if current['sidecars']['sports'] != manifest['sidecars']['sports']:
            raise MaintenanceRejected('Sports SQLite sidecars changed during adoption')
        verification = sincronizacion.verificar(base)
        if not verification['ok'] or verification.get('cambios_en_seco'):
            raise MaintenanceRejected(f'Post-adoption sports verification failed: {verification}')
    _verify_target_open(base, scope, GENERATION)
    with _locked([*_db_paths(base, acquisition_journal).values()]):
        current = _validate_origin(base, scope, acquisition_journal, expected_code=TARGET)
        _assert_snapshot_matches(current, manifest, expected_code=TARGET)
        if _current_artifact_inventories(current) != (
                manifest['state_artifacts'], manifest['updates_artifacts'], manifest['acquisition_artifacts']):
            raise MaintenanceRejected('Artifacts changed while opening the adopted coordinator')
    receipt_path = backup_dir / 'adoption.json'
    existing = (_read_receipt(receipt_path, 'adopted', manifest) if receipt_path.exists() else None)
    if existing:
        expected = _adoption_receipt(manifest, open_verified=existing.get('coordinator_open_verified', False),
                                     adopted_at=existing.get('adopted_at'))
        if {k: v for k, v in existing.items() if k != 'receipt_sha256'} != expected:
            raise MaintenanceRejected('Existing adoption receipt differs from verified state')
        if existing.get('coordinator_open_verified') is True:
            return existing
        receipt = _adoption_receipt(manifest, open_verified=True, adopted_at=existing.get('adopted_at'))
    else:
        receipt = _adoption_receipt(manifest, open_verified=True)
    return _write_receipt(receipt_path, receipt)


def restore(base, scope, acquisition_journal, backup_dir, source_runtime_root, confirmation):
    base, scope = _regular_path(base, 'Sports SQLite'), _regular_path(scope, 'Scope file')
    acquisition_journal = _regular_path(acquisition_journal, 'Acquisition journal')
    backup_dir = Path(backup_dir).expanduser().resolve()
    if confirmation != RESTORE_CONFIRM:
        raise MaintenanceRejected(f'Confirm exactly: {RESTORE_CONFIRM}')
    if actualizacion.codigo() != TARGET or carga.referencia_del_codigo()['sha256_agregada'] != TARGET:
        raise MaintenanceRejected('Restore must run under the exact reviewed target code')
    backup_dir, manifest = _load_snapshot(backup_dir)
    _verify_saved(base, acquisition_journal, backup_dir, manifest)
    adoption = _read_receipt(backup_dir / 'adoption.json', 'adopted', manifest)
    if adoption.get('generation') != GENERATION or adoption.get('source_code') != SOURCE or adoption.get('target_code') != TARGET:
        raise MaintenanceRejected('Adoption receipt does not match this transition')
    _source_runtime_probe(source_runtime_root)
    restore_path = backup_dir / 'restore.json'
    with _locked([*_db_paths(base, acquisition_journal).values()]):
        current = _validate_origin(base, scope, acquisition_journal, expected_code=TARGET)
        _assert_snapshot_matches(current, manifest, expected_code=TARGET)
        if _current_artifact_inventories(current) != (
                manifest['state_artifacts'], manifest['updates_artifacts'], manifest['acquisition_artifacts']):
            raise MaintenanceRejected('State or Acquisition artifacts changed after adoption; restore is refused')
        if restore_path.exists():
            restored_receipt = _read_receipt(restore_path, 'restored', manifest)
            if restored_receipt.get('target_code') != SOURCE:
                raise MaintenanceRejected('Existing restore receipt has the wrong target fingerprint')
            # An earlier restore is idempotent only while the exact original state remains.
            raise MaintenanceRejected('Coordinator is already restored; its current confirmation is not the adopted state')
        _adopt_metadata(current['journal'], TARGET, SOURCE, current['meta'])
        _, restored_meta, jobs = _journal(base)
        if restored_meta != manifest['source_meta'] or _history_sha(jobs) != manifest['coordinator_jobs_sha256']:
            raise MaintenanceRejected('Restore changed other coordinator metadata or history')
    try:
        opened = _source_runtime_probe(source_runtime_root, base=base, scope=scope, open_coordinator=True)
        with _locked([*_db_paths(base, acquisition_journal).values()]):
            restored = _validate_origin(base, scope, acquisition_journal, expected_code=SOURCE)
            _assert_snapshot_matches(restored, manifest, expected_code=SOURCE)
            if _current_artifact_inventories(restored) != (
                    manifest['state_artifacts'], manifest['updates_artifacts'], manifest['acquisition_artifacts']):
                raise MaintenanceRejected('Artifacts changed while opening the restored coordinator')
    except Exception as error:
        # Only point the journal back to TARGET if every state component still proves that
        # the failed probe did not accept work or alter the published generation.
        try:
            with _locked([*_db_paths(base, acquisition_journal).values()]):
                current = _validate_origin(base, scope, acquisition_journal, expected_code=SOURCE)
                _assert_snapshot_matches(current, manifest, expected_code=SOURCE)
                if _current_artifact_inventories(current) != (
                        manifest['state_artifacts'], manifest['updates_artifacts'], manifest['acquisition_artifacts']):
                    raise MaintenanceRejected('Artifacts changed after source-runtime probe failure')
                _adopt_metadata(current['journal'], SOURCE, TARGET, current['meta'])
        except Exception as rollback_error:
            raise MaintenanceRejected(
                'Source runtime failed to reopen the generation; metadata remains at SOURCE because '
                'the exact safe rollback conditions could not be verified'
            ) from rollback_error
        raise MaintenanceRejected('Source runtime could not reopen the restored generation; target code restored') from error
    receipt = dict(format=FORMAT, action='restored', manifest_sha256=manifest['manifest_sha256'],
                   source_code=TARGET, target_code=SOURCE, scope_sha256=manifest['scope_sha256'],
                   generation=GENERATION, revision=REVISION, source_runtime_open_verified=opened['generation'],
                   restored_at=datetime.now(timezone.utc).isoformat())
    return _write_receipt(restore_path, receipt)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    for name in ('prepare', 'adopt', 'restore'):
        item = sub.add_parser(name)
        item.add_argument('--base', type=Path, required=True)
        item.add_argument('--scope', type=Path, required=True)
        item.add_argument('--acquisition-journal', type=Path, required=True)
        item.add_argument('--backup-dir', type=Path, required=True)
        if name != 'prepare':
            item.add_argument('--confirm', required=True)
        if name == 'restore':
            item.add_argument('--source-runtime-root', type=Path, required=True)
    args = parser.parse_args()
    if args.action == 'prepare':
        result = prepare(args.base, args.scope, args.acquisition_journal, args.backup_dir)
    elif args.action == 'adopt':
        result = adopt(args.base, args.scope, args.acquisition_journal, args.backup_dir, args.confirm)
    else:
        result = restore(args.base, args.scope, args.acquisition_journal, args.backup_dir,
                         args.source_runtime_root, args.confirm)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
