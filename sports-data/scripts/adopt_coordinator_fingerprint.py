"""Explicit, one-time transition of the sports coordinator fingerprint.

Run only with the API and acquisition services stopped, using the exact state volume and an external
backup mount. The operation takes a consistent pair snapshot, upgrades Alembic 0004 to 0007, verifies
all pre-existing tables are byte-for-byte equivalent at the row level, then updates only the confirmed
coordinator code fingerprint and database description. It never starts an update or contacts a provider.
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
import sys
import uuid

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from persistencia import actualizacion, carga, sqlite, sincronizacion  # noqa: E402
from persistencia.bloqueo import bloquear  # noqa: E402


LEGACY_COORDINATOR_CODE = '8d1b586793ac2495ebf2bebe9cace154f8caa9ae4641199c38ca181fa03ca0d4'
CURRENT_COORDINATOR_CODE = '37038df67dc1c5898a51ffd3efe216d88d43508e16dbede291f3be2c537d0720'
HISTORICAL_IMPORT_CODES = frozenset({
    '177422701165f3a83bf14f22d537dbab08cd8bd96a560758ce40ec9782130a58',
    LEGACY_COORDINATOR_CODE,
})
SCOPE_SHA256 = 'a791c80eb18cf028f3af658a2dfe19d954168e2c4b28810c5e5d73a45980f164'
SOURCE_REVISION = '0004'
TARGET_REVISION = '0007'
COMMENT_TABLES = frozenset({'narrative_comment_capture', 'narrative_comment', 'narrative_comment_selection'})
MANIFEST_VERSION = 1
CONFIRMATION = f'adopt coordinator fingerprint {LEGACY_COORDINATOR_CODE} -> {CURRENT_COORDINATOR_CODE}'


class MaintenanceRejected(RuntimeError):
    """Refuse an unknown or inconsistent coordinator state without changing it."""


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _read_sqlite(path):
    return sqlite3.connect(f'{Path(path).resolve().as_uri()}?mode=ro', uri=True)


def _db_signature(path):
    """Hash the logical SQLite contents (tables, column order, and all rows)."""
    digest = hashlib.sha256()
    with closing(_read_sqlite(path)) as connection:
        tables = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        for table in tables:
            columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')]
            digest.update(_json_bytes([table, columns]))
            quoted = ', '.join(f'"{column}"' for column in columns)
            rows = sorted((tuple(row) for row in connection.execute(f'SELECT {quoted} FROM "{table}"')), key=repr)
            for row in rows:
                digest.update(_json_bytes([repr(value) for value in row]))
        if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise MaintenanceRejected(f'SQLite con integridad incorrecta: {path}')
        if connection.execute('PRAGMA foreign_key_check').fetchall():
            raise MaintenanceRejected(f'Claves foráneas inválidas: {path}')
    return digest.hexdigest()


def _table_inventory(path):
    """Signatures of tables that existed before the comments migrations, excluding only Alembic metadata."""
    result = {}
    with closing(_read_sqlite(path)) as connection:
        names = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        for table in names:
            if table == 'alembic_version':
                continue
            columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')]
            quoted = ', '.join(f'"{column}"' for column in columns)
            rows = sorted((tuple(row) for row in connection.execute(f'SELECT {quoted} FROM "{table}"')), key=repr)
            digest = hashlib.sha256()
            digest.update(_json_bytes([table, columns]))
            for row in rows:
                digest.update(_json_bytes([repr(value) for value in row]))
            result[table] = {'columns': columns, 'rows': len(rows), 'sha256': digest.hexdigest()}
    return result


def _copy_sqlite(source, destination):
    source, destination = Path(source), Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if destination.exists():
        raise FileExistsError(destination)
    with closing(_read_sqlite(source)) as original, closing(sqlite3.connect(destination)) as copy:
        original.backup(copy)
        copy.execute('PRAGMA journal_mode=DELETE')
    os.chmod(destination, 0o600)
    _db_signature(destination)


def _copy_tree(source, destination, *, skip_databases=()):
    """Copy a quiescent artifact tree without following links or dropping WAL/SHM evidence."""
    source, destination = Path(source), Path(destination)
    skip_databases = {Path(item).resolve() for item in skip_databases}
    skip_databases |= {Path(str(item) + suffix).resolve() for item in tuple(skip_databases)
                       for suffix in sqlite.ACOMPANANTES}
    if not source.exists():
        return
    destination.mkdir(parents=True, mode=0o700)
    for current, dirs, files in os.walk(source, followlinks=False):
        current_path = Path(current)
        relative = current_path.relative_to(source)
        target_dir = destination / relative
        target_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        for name in list(dirs):
            item = current_path / name
            if item.is_symlink():
                raise MaintenanceRejected(f'No se admiten enlaces simbólicos en el estado: {item}')
            (target_dir / name).mkdir(mode=0o700, exist_ok=True)
        for name in files:
            item = current_path / name
            if item.is_symlink():
                raise MaintenanceRejected(f'No se admiten enlaces simbólicos en el estado: {item}')
            if item.resolve() in skip_databases or name.endswith('.lock'):
                continue
            target = target_dir / name
            shutil.copyfile(item, target)
            os.chmod(target, 0o600)


def _write_private(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + f'.tmp-{os.getpid()}')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(_json_bytes(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def _load_journal(base):
    journal = base.with_name(base.name + '.actualizaciones') / 'jobs.sqlite'
    if not journal.is_file():
        raise MaintenanceRejected('Falta el journal deportivo <base>.actualizaciones/jobs.sqlite')
    with closing(_read_sqlite(journal)) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if tables != {'trabajo', 'confirmado'}:
            raise MaintenanceRejected('El journal deportivo no usa exactamente tablas trabajo/confirmado; no se convierte automáticamente')
        if [row[1] for row in connection.execute('PRAGMA table_info(trabajo)')] != ['id', 'estado', 'detalle']:
            raise MaintenanceRejected('Esquema desconocido para trabajo')
        if [row[1] for row in connection.execute('PRAGMA table_info(confirmado)')] != ['id', 'detalle']:
            raise MaintenanceRejected('Esquema desconocido para confirmado')
        if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or connection.execute(
                'PRAGMA foreign_key_check').fetchall():
            raise MaintenanceRejected('El journal deportivo no supera integrity_check / foreign_key_check')
        rows = connection.execute('SELECT id, estado, detalle FROM trabajo ORDER BY id').fetchall()
        confirmed_rows = connection.execute('SELECT id, detalle FROM confirmado ORDER BY id').fetchall()
    if len(confirmed_rows) != 1 or confirmed_rows[0][0] != 1:
        raise MaintenanceRejected('El journal debe contener una única confirmación id=1')
    work = []
    for ident, state, detail_text in rows:
        try:
            detail = json.loads(detail_text)
        except (TypeError, json.JSONDecodeError) as error:
            raise MaintenanceRejected(f'Detalle de trabajo ilegible: {ident}') from error
        if detail.get('id') != ident or detail.get('estado') != state:
            raise MaintenanceRejected(f'Identidad o estado inconsistente en el trabajo {ident}')
        if state not in {'completed', 'failed', 'interrupted'} or detail.get('fase') in {'publishing', 'recovering'}:
            raise MaintenanceRejected(f'Existe un trabajo activo o estado no reconocido: {ident} ({state}/{detail.get("fase")})')
        work.append((ident, state, detail_text))
    try:
        confirmed = json.loads(confirmed_rows[0][1])
    except (TypeError, json.JSONDecodeError) as error:
        raise MaintenanceRejected('El metadato confirmado no es JSON válido') from error
    return journal, confirmed, work


def _check_control_journal(root):
    path = root / 'control' / 'jobs.sqlite'
    if not path.exists():
        return None
    with closing(_read_sqlite(path)) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if tables != {'job'} or [row[1] for row in connection.execute('PRAGMA table_info(job)')] != ['id', 'idem', 'state', 'data']:
            raise MaintenanceRejected('Esquema desconocido para el journal de solicitudes control/jobs.sqlite')
        if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or connection.execute(
                'PRAGMA foreign_key_check').fetchall():
            raise MaintenanceRejected('El journal de control no supera su verificación SQLite')
        records = connection.execute('SELECT id, idem, state, data FROM job ORDER BY id').fetchall()
        active = [row[0] for row in records if row[2] in ('queued', 'running')]
        if active:
            raise MaintenanceRejected(f'Hay solicitudes de control activas: {active}')
        for ident, idem, state, data_text in records:
            try:
                data = json.loads(data_text)
            except (TypeError, json.JSONDecodeError) as error:
                raise MaintenanceRejected(f'Detalle de solicitud ilegible: {ident}') from error
            if (data.get('id') != ident or data.get('status') != state
                    or data.get('idem', data.get('idempotency_key', idem)) != idem):
                raise MaintenanceRejected(f'Identidad, estado o clave idempotente inconsistente: {ident}')
            if state not in {'completed', 'failed', 'cancelled', 'interrupted'}:
                raise MaintenanceRejected(f'Estado de control no reconocido: {ident} ({state})')
    return path


def _import_hashes(base):
    known = set()
    with closing(_read_sqlite(base)) as connection:
        for (summary,) in connection.execute("SELECT summary FROM import_run WHERE status IN ('committed','no_changes')"):
            if not summary:
                continue
            try:
                code = json.loads(summary).get('codigo') or {}
                fingerprint = code.get('sha256_agregada') if isinstance(code, dict) else None
            except (TypeError, json.JSONDecodeError) as error:
                raise MaintenanceRejected('Un resumen de importación histórico no es JSON válido') from error
            if fingerprint:
                if fingerprint not in HISTORICAL_IMPORT_CODES:
                    raise MaintenanceRejected(f'Huella histórica de importación no reconocida: {fingerprint}')
                known.add(fingerprint)
    return sorted(known)


def _validate_origin(base, scope):
    base, scope = Path(base).resolve(), Path(scope).resolve()
    if not base.is_file() or not scope.is_file():
        raise MaintenanceRejected('La base y el ámbito deben existir')
    actual_code = carga.referencia_del_codigo()['sha256_agregada']
    if actual_code != CURRENT_COORDINATOR_CODE:
        raise MaintenanceRejected(f'El código de mantenimiento no coincide con la versión revisada: {actual_code}')
    if actualizacion.codigo() != CURRENT_COORDINATOR_CODE:
        raise MaintenanceRejected('La huella deportiva calculada por el coordinador no es la esperada')
    scope_hash = hashlib.sha256(scope.read_bytes()).hexdigest()
    if scope_hash != SCOPE_SHA256:
        raise MaintenanceRejected(f'El ámbito no coincide con el alcance fijado: {scope_hash}')
    database_description = sqlite.comprobar_copia(base)
    if database_description['revision'] != SOURCE_REVISION:
        raise MaintenanceRejected(f'Se esperaba Alembic {SOURCE_REVISION}, se encontró {database_description["revision"]}')
    journal_path, confirmed, work = _load_journal(base)
    if confirmed.get('codigo') != LEGACY_COORDINATOR_CODE:
        raise MaintenanceRejected(f'La huella confirmada no es el origen conocido {LEGACY_COORDINATOR_CODE}')
    if confirmed.get('ambito') != scope_hash:
        raise MaintenanceRejected('La huella del ámbito no coincide con la confirmación antigua')
    try:
        generation = str(uuid.UUID(confirmed['generacion']))
    except (KeyError, TypeError, ValueError) as error:
        raise MaintenanceRejected('La generación confirmada no es un UUID válido') from error
    if generation != confirmed['generacion']:
        raise MaintenanceRejected('La generación confirmada no usa el formato UUID canónico')
    if confirmed.get('descripcion') != database_description:
        raise MaintenanceRejected('La base no coincide exactamente con la descripción confirmada')
    control_path = _check_control_journal(journal_path.parent)
    import_hashes = _import_hashes(base)
    return dict(base=base, scope=scope, scope_hash=scope_hash, code=actual_code, revision=database_description['revision'],
                description=database_description, journal=journal_path, confirmed=confirmed, work=work,
                control_journal=control_path, import_hashes=import_hashes)


def _payload_paths(source_base, payload):
    source_base = Path(source_base)
    return {
        'base': payload / source_base.name,
        'updates': payload / (source_base.name + '.actualizaciones'),
        'copies': payload / 'copias',
    }


def _copy_snapshot_files(source_base, payload):
    paths = _payload_paths(source_base, payload)
    payload.mkdir(parents=True, mode=0o700)
    sqlite.copia_consistente(source_base, paths['base'])
    updates = Path(str(source_base) + '.actualizaciones')
    if not updates.is_dir():
        raise MaintenanceRejected('Falta el directorio del coordinator y sus evidencias')
    control = updates / 'control' / 'jobs.sqlite'
    _copy_tree(updates, paths['updates'], skip_databases={updates / 'jobs.sqlite', control})
    journal = updates / 'jobs.sqlite'
    _copy_sqlite(journal, paths['updates'] / 'jobs.sqlite')
    if control.exists():
        _copy_sqlite(control, paths['updates'] / 'control' / 'jobs.sqlite')
    copies = source_base.parent / 'copias'
    if copies.exists():
        _copy_tree(copies, paths['copies'])
    return paths


def _snapshot_manifest(source, backup_dir):
    return {
        'format': 'real-jaen-coordinator-fingerprint-backup-v1', 'manifest_version': MANIFEST_VERSION,
        'source_base': str(source['base']), 'source_scope': str(source['scope']),
        'source_revision': source['revision'], 'source_code_sha256': source['confirmed']['codigo'],
        'target_code_sha256': source['code'], 'scope_sha256': source['scope_hash'],
        'source_description': source['description'], 'source_generation': source['confirmed']['generacion'],
        'historical_import_code_sha256': source['import_hashes'],
        'coordinator_work_count': len(source['work']),
        'coordinator_completed_count': sum(1 for _ident, state, _detail in source['work'] if state == 'completed'),
        'coordinator_work_sha256': hashlib.sha256(_json_bytes(source['work'])).hexdigest(),
        'coordinator_journal_sha256': _db_signature(source['journal']),
        'control_journal_sha256': _db_signature(source['control_journal']) if source['control_journal'] else None,
        'coordinator_artifacts_sha256': _artifact_inventory(source['journal'].parent),
        'backup_dir': str(backup_dir), 'created_at': datetime.now(timezone.utc).isoformat(),
    }


def _artifact_inventory(directory):
    directory = Path(directory)
    result = {}
    operational_databases = {directory / 'jobs.sqlite', directory / 'control' / 'jobs.sqlite'}
    operational_databases |= {Path(str(item) + suffix) for item in tuple(operational_databases)
                              for suffix in sqlite.ACOMPANANTES}
    if not directory.exists():
        return result
    for path in sorted(directory.rglob('*')):
        if path.is_symlink():
            raise MaintenanceRejected(f'No se admiten enlaces simbólicos en el estado: {path}')
        if not path.is_file() or path.name.endswith('.lock'):
            continue
        if path in operational_databases:
            continue
        result[path.relative_to(directory).as_posix()] = _sha256(path)
    return result


def _seal_manifest(backup_dir, manifest):
    payload = backup_dir / 'payload'
    file_hashes = {}
    for path in sorted(payload.rglob('*')):
        if path.is_file():
            file_hashes[path.relative_to(backup_dir).as_posix()] = _sha256(path)
    manifest = dict(manifest, payload_sha256=file_hashes)
    manifest['complete'] = True
    manifest['manifest_sha256'] = hashlib.sha256(_json_bytes(manifest)).hexdigest()
    _write_private(backup_dir / 'manifest.json', manifest)
    for path in [backup_dir, *backup_dir.rglob('*')]:
        if path.is_dir():
            os.chmod(path, 0o700)
        else:
            os.chmod(path, 0o600)
    return manifest


def _load_manifest(backup_dir):
    backup_dir = Path(backup_dir).resolve()
    manifest_path = backup_dir / 'manifest.json'
    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as error:
        raise MaintenanceRejected('No existe un manifiesto completo y legible en el respaldo') from error
    signature = manifest.pop('manifest_sha256', None)
    if manifest.get('complete') is not True or signature != hashlib.sha256(_json_bytes(manifest)).hexdigest():
        raise MaintenanceRejected('La huella del manifiesto o el indicador complete no son válidos')
    manifest['manifest_sha256'] = signature
    if manifest.get('format') != 'real-jaen-coordinator-fingerprint-backup-v1' or manifest.get('manifest_version') != MANIFEST_VERSION:
        raise MaintenanceRejected('Formato de respaldo desconocido')
    for name, expected in manifest['payload_sha256'].items():
        path = backup_dir / name
        if not path.is_file() or _sha256(path) != expected:
            raise MaintenanceRejected(f'Archivo de respaldo ausente o alterado: {name}')
    actual_files = {path.relative_to(backup_dir).as_posix() for path in (backup_dir / 'payload').rglob('*') if path.is_file()}
    if actual_files != set(manifest['payload_sha256']):
        raise MaintenanceRejected('El respaldo contiene archivos no declarados o carece de archivos declarados')
    return manifest


def _validate_snapshot_against_source(base, scope, backup_dir, manifest):
    if Path(manifest['source_base']).resolve() != Path(base).resolve():
        raise MaintenanceRejected('El respaldo pertenece a otra ruta de base; no se remapean rutas históricas')
    if Path(manifest['source_scope']).resolve() != Path(scope).resolve():
        raise MaintenanceRejected('El respaldo pertenece a otra ruta de ámbito')
    if manifest['source_code_sha256'] != LEGACY_COORDINATOR_CODE or manifest['target_code_sha256'] != CURRENT_COORDINATOR_CODE:
        raise MaintenanceRejected('El respaldo no representa la transición de huella autorizada')
    if manifest['source_revision'] != SOURCE_REVISION or manifest['scope_sha256'] != SCOPE_SHA256:
        raise MaintenanceRejected('El respaldo no corresponde al esquema/ámbito autorizado')
    payload = backup_dir / 'payload'
    saved = _payload_paths(base, payload)
    if sqlite.comprobar_copia(saved['base']) != manifest['source_description']:
        raise MaintenanceRejected('La base guardada no coincide con la descripción del manifiesto')
    if sqlite.revision(saved['base']) != SOURCE_REVISION:
        raise MaintenanceRejected('La copia del manifiesto no es la revisión 0004')
    if _db_signature(saved['updates'] / 'jobs.sqlite') != manifest['coordinator_journal_sha256']:
        raise MaintenanceRejected('El journal guardado no coincide con el manifiesto')
    saved_journal, saved_confirmed, saved_work = _load_journal(saved['base'])
    if (saved_confirmed.get('codigo') != manifest['source_code_sha256']
            or saved_confirmed.get('ambito') != manifest['scope_sha256']
            or saved_confirmed.get('generacion') != manifest['source_generation']
            or saved_confirmed.get('descripcion') != manifest['source_description']
            or hashlib.sha256(_json_bytes(saved_work)).hexdigest() != manifest['coordinator_work_sha256']):
        raise MaintenanceRejected('Los metadatos o el historial del journal guardado no coinciden con el manifiesto')
    saved_control = saved['updates'] / 'control' / 'jobs.sqlite'
    if manifest['control_journal_sha256'] is None:
        if saved_control.exists():
            raise MaintenanceRejected('El manifiesto no declara el journal de control que aparece en el respaldo')
    elif not saved_control.exists() or _db_signature(saved_control) != manifest['control_journal_sha256']:
        raise MaintenanceRejected('El journal del control HTTP no coincide con el manifiesto')
    if _table_inventory(saved['base']) != manifest['source_table_inventory']:
        raise MaintenanceRejected('Inventario de tablas de origen no coincide con el manifiesto')
    return saved


def _validate_current_artifacts(base, manifest):
    directory = Path(str(base) + '.actualizaciones')
    if _artifact_inventory(directory) != manifest['coordinator_artifacts_sha256']:
        raise MaintenanceRejected('Cambió la evidencia archivada del coordinador desde el respaldo')


def _validate_migrated(base, manifest):
    description = sqlite.comprobar_copia(base)
    if description['revision'] != TARGET_REVISION:
        raise MaintenanceRejected(f'La base no está en Alembic {TARGET_REVISION}')
    target_tables = _table_inventory(base)
    source_tables = manifest['source_table_inventory']
    if not set(source_tables).issubset(target_tables):
        raise MaintenanceRejected('Una tabla existente antes de la migración ha desaparecido')
    changed = [name for name, signature in source_tables.items() if target_tables[name] != signature]
    if changed:
        raise MaintenanceRejected(f'Las migraciones cambiaron datos/tablas anteriores: {changed[:8]}')
    added = set(target_tables) - set(source_tables)
    if added != COMMENT_TABLES:
        raise MaintenanceRejected(f'Las únicas tablas nuevas permitidas son las de comentarios: se encontraron {sorted(added)}')
    verification = sincronizacion.verificar(base)
    if not verification['ok']:
        raise MaintenanceRejected(f'La verificación de persistencia no es limpia: {verification}')
    return description


def _validate_migration_in_progress(base, manifest):
    """Permit only the known Alembic chain's partial revision after an interrupted upgrade."""
    description = sqlite.comprobar_copia(base)
    revision = description['revision']
    if revision not in {SOURCE_REVISION, '0005', '0006'}:
        raise MaintenanceRejected(f'Revisión inesperada antes de reanudar la migración: {revision}')
    current = _table_inventory(base)
    source = manifest['source_table_inventory']
    if not set(source).issubset(current) or any(current.get(name) != item for name, item in source.items()):
        raise MaintenanceRejected('Las tablas existentes antes de migrar cambiaron')
    added = set(current) - set(source)
    if not added.issubset(COMMENT_TABLES):
        raise MaintenanceRejected(f'Hay tablas nuevas ajenas a comentarios: {sorted(added - COMMENT_TABLES)}')
    for name in added:
        if current[name]['rows'] != 0:
            raise MaintenanceRejected(f'La migración parcial contiene datos no presentes en el esquema 0004: {name}')
    return description


def _work_state(base):
    _journal, _confirmed, work = _load_journal(Path(base))
    return work


def _write_adoption_receipt(backup_dir, *, manifest, target_description, generation):
    receipt = {
        'format': 'real-jaen-coordinator-fingerprint-adoption-v1',
        'manifest_sha256': manifest['manifest_sha256'], 'source_base': manifest['source_base'],
        'source_code_sha256': LEGACY_COORDINATOR_CODE, 'target_code_sha256': CURRENT_COORDINATOR_CODE,
        'scope_sha256': SCOPE_SHA256, 'source_generation': manifest['source_generation'],
        'generation_at_adoption': generation, 'target_revision': TARGET_REVISION,
        'target_description': target_description,
        'coordinator_work_sha256': manifest['coordinator_work_sha256'],
        'coordinator_work_count': manifest['coordinator_work_count'],
        'coordinator_completed_count': manifest['coordinator_completed_count'],
        'adopted_at': datetime.now(timezone.utc).isoformat(),
    }
    _write_private(Path(backup_dir) / 'adoption.json', receipt)
    return receipt


def adopt(base, scope, backup_dir, confirmation, *, _checkpoint=lambda _name: None):
    """Create/verify a private snapshot, migrate 0004→0007, then adopt only confirmed metadata."""
    base, scope, backup_dir = Path(base).resolve(), Path(scope).resolve(), Path(backup_dir).resolve()
    if confirmation != CONFIRMATION:
        raise MaintenanceRejected(f'Confirma escribiendo exactamente: {CONFIRMATION}')
    if backup_dir == base.parent or base.parent in backup_dir.parents or backup_dir in base.parent.parents:
        raise MaintenanceRejected('El respaldo debe quedar fuera del árbol del volumen deportivo')
    if backup_dir.exists():
        manifest = _load_manifest(backup_dir)
        saved = _validate_snapshot_against_source(base, scope, backup_dir, manifest)
    else:
        backup_dir.parent.mkdir(parents=True, exist_ok=True)
        os.mkdir(backup_dir, 0o700)
        try:
            with bloquear(base):
                source = _validate_origin(base, scope)
                _copy_snapshot_files(base, backup_dir / 'payload')
                manifest = _snapshot_manifest(source, backup_dir)
                manifest['source_table_inventory'] = _table_inventory(base)
                _seal_manifest(backup_dir, manifest)
            manifest = _load_manifest(backup_dir)
            _checkpoint('snapshot_complete')
        except BaseException:
            # An incomplete directory deliberately remains for diagnosis; it has no usable complete manifest.
            raise

    with bloquear(base):
        actual_code = carga.referencia_del_codigo()['sha256_agregada']
        if actual_code != CURRENT_COORDINATOR_CODE or actualizacion.codigo() != CURRENT_COORDINATOR_CODE:
            raise MaintenanceRejected('La huella del código actual cambió respecto al respaldo autorizado')
        scope_hash = hashlib.sha256(scope.read_bytes()).hexdigest()
        if scope_hash != SCOPE_SHA256:
            raise MaintenanceRejected('La huella del ámbito ya no coincide')
        journal_path, confirmed, work = _load_journal(base)
        if hashlib.sha256(_json_bytes(work)).hexdigest() != manifest['coordinator_work_sha256']:
            raise MaintenanceRejected('El historial de trabajos cambió desde el respaldo')
        if _db_signature(journal_path) != manifest['coordinator_journal_sha256']:
            # Confirmed detail may already have been adopted after a crash; compare the work history separately.
            if confirmed.get('codigo') != CURRENT_COORDINATOR_CODE:
                raise MaintenanceRejected('El journal deportivo cambió después del respaldo')
        control_journal = journal_path.parent / 'control' / 'jobs.sqlite'
        if manifest['control_journal_sha256'] is not None and _db_signature(control_journal) != manifest['control_journal_sha256']:
            raise MaintenanceRejected('El historial de solicitudes HTTP cambió después del respaldo')
        saved = _validate_snapshot_against_source(base, scope, backup_dir, manifest)
        _validate_current_artifacts(base, manifest)

        revision = sqlite.revision(base)
        if revision in {SOURCE_REVISION, '0005', '0006'}:
            description = _validate_migration_in_progress(base, manifest)
            if confirmed.get('codigo') != LEGACY_COORDINATOR_CODE:
                raise MaintenanceRejected('La huella antigua debe permanecer intacta hasta completar la migración')
            if revision == SOURCE_REVISION and description != manifest['source_description']:
                raise MaintenanceRejected('El estado actual ya no coincide con la copia anterior a migrar')
            if confirmed.get('ambito') != scope_hash or confirmed.get('generacion') != manifest['source_generation']:
                raise MaintenanceRejected('Ámbito o generación confirmada cambiaron desde el respaldo')
            if confirmed.get('descripcion') != description:
                raise MaintenanceRejected('La descripción confirmada antigua ya no coincide con SQLite')
            result = sincronizacion.migrar(base)
            if result['revision'] != TARGET_REVISION or not result['verificacion']['ok']:
                raise MaintenanceRejected(f'La migración no terminó verificada: {result}')
            _checkpoint('migrated')
        elif revision != TARGET_REVISION:
            raise MaintenanceRejected(f'Revisión inesperada: {revision}')

        target_description = _validate_migrated(base, manifest)
        target_inventory = _table_inventory(base)
        if confirmed.get('ambito') != scope_hash or confirmed.get('generacion') != manifest['source_generation']:
            raise MaintenanceRejected('El coordinador ya no apunta al mismo ámbito y generación del respaldo')
        if confirmed.get('codigo') == LEGACY_COORDINATOR_CODE:
            _saved_journal, saved_confirmed, _saved_work = _load_journal(saved['base'])
            if confirmed != saved_confirmed:
                raise MaintenanceRejected('La confirmación antigua cambió respecto al journal guardado')
            if confirmed.get('descripcion') != manifest['source_description']:
                raise MaintenanceRejected('La confirmación antigua no coincide con el estado guardado antes de migrar')
            updated = dict(confirmed, codigo=CURRENT_COORDINATOR_CODE, descripcion=target_description)
            with closing(sqlite3.connect(journal_path, timeout=10)) as connection:
                connection.execute('PRAGMA synchronous=FULL')
                connection.execute('BEGIN IMMEDIATE')
                row = connection.execute('SELECT detalle FROM confirmado WHERE id=1').fetchone()
                if row is None or json.loads(row[0]) != confirmed:
                    raise MaintenanceRejected('El journal cambió durante la confirmación; no se escribe')
                connection.execute('UPDATE confirmado SET detalle=? WHERE id=1',
                                   (json.dumps(updated, ensure_ascii=False, separators=(',', ':'), allow_nan=False),))
                connection.commit()
            confirmed = updated
            _checkpoint('adopted')
        elif confirmed.get('codigo') == CURRENT_COORDINATOR_CODE:
            _saved_journal, saved_confirmed, _saved_work = _load_journal(saved['base'])
            expected_confirmed = dict(saved_confirmed, codigo=CURRENT_COORDINATOR_CODE,
                                      descripcion=target_description)
            if confirmed != expected_confirmed:
                raise MaintenanceRejected('La confirmación actual incluye cambios distintos a la adopción registrada')
            if confirmed.get('descripcion') != target_description:
                raise MaintenanceRejected('La huella actual ya está confirmada, pero la descripción no coincide')
            receipt_path = backup_dir / 'adoption.json'
            if receipt_path.exists():
                receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
                if receipt.get('manifest_sha256') != manifest['manifest_sha256'] or receipt.get('generation_at_adoption') != manifest['source_generation']:
                    raise MaintenanceRejected('Existe una evidencia de adopción distinta')
            elif confirmed.get('generacion') != manifest['source_generation']:
                raise MaintenanceRejected('No se puede demostrar que la transición se completó antes de crear su recibo')
        else:
            raise MaintenanceRejected('Huella confirmada distinta del origen y destino conocidos')

        final_journal_path, final_meta, final_work = _load_journal(base)
        final_description = sqlite.comprobar_copia(base)
        if final_meta.get('codigo') != CURRENT_COORDINATOR_CODE or final_meta.get('ambito') != scope_hash:
            raise MaintenanceRejected('La confirmación posterior no refleja la huella/ámbito actuales')
        if final_meta.get('generacion') != manifest['source_generation'] or final_meta.get('descripcion') != final_description:
            raise MaintenanceRejected('La confirmación posterior no coincide con la generación/descripción')
        if final_work != work or _table_inventory(base) != target_inventory:
            raise MaintenanceRejected('Cambió el historial o el inventario de partidos')
        receipt_path = backup_dir / 'adoption.json'
        if not receipt_path.exists():
            _write_adoption_receipt(backup_dir, manifest=manifest, target_description=final_description,
                                     generation=final_meta['generacion'])

    # Open/close with the regular coordinator only after releasing the maintenance lock; it owns its lock in a
    # separate thread. This is a read-only readiness check: no update is requested or queued.
    with actualizacion.Coordinador(base, scope):
        pass
    return {
        'status': 'adopted', 'base': str(base), 'source_revision': SOURCE_REVISION,
        'target_revision': TARGET_REVISION, 'source_code_sha256': LEGACY_COORDINATOR_CODE,
        'target_code_sha256': CURRENT_COORDINATOR_CODE, 'scope_sha256': SCOPE_SHA256,
        'generation': manifest['source_generation'], 'coordinator_jobs_preserved': manifest['coordinator_work_count'],
        'coordinator_completed_jobs_preserved': manifest['coordinator_completed_count'],
        'historical_import_code_sha256': manifest['historical_import_code_sha256'],
        'sports_table_count': len(manifest['source_table_inventory']), 'backup_dir': str(backup_dir),
        'backup_manifest_sha256': manifest['manifest_sha256'], 'database': sqlite.comprobar_copia(base),
        'coordinator_open_close': 'ok',
    }


def restore(base, scope, backup_dir, confirmation):
    """Restore the pre-migration database and coordinator tree as a pair; keep the failed state aside."""
    base, scope, backup_dir = Path(base).resolve(), Path(scope).resolve(), Path(backup_dir).resolve()
    manifest = _load_manifest(backup_dir)
    _validate_snapshot_against_source(base, scope, backup_dir, manifest)
    expected_confirmation = f'restore coordinator snapshot {manifest["manifest_sha256"]}'
    if confirmation != expected_confirmation:
        raise MaintenanceRejected(f'Confirma escribiendo exactamente: {expected_confirmation}')
    if base.parent in backup_dir.parents or backup_dir in base.parent.parents:
        raise MaintenanceRejected('El respaldo debe quedar fuera del árbol del volumen deportivo')
    updates = base.with_name(base.name + '.actualizaciones')
    saved = _payload_paths(base, backup_dir / 'payload')
    preserve_dir = backup_dir / 'post-maintenance-state'

    with bloquear(base):
        # A fully restored retry is a no-op. Do not overwrite a state that has since accepted new work.
        if base.is_file() and updates.is_dir():
            try:
                current_description = sqlite.comprobar_copia(base)
                _journal, meta, work = _load_journal(base)
                restored = (current_description == manifest['source_description']
                            and meta.get('codigo') == LEGACY_COORDINATOR_CODE
                            and meta.get('ambito') == SCOPE_SHA256
                            and meta.get('generacion') == manifest['source_generation']
                            and meta.get('descripcion') == current_description
                            and hashlib.sha256(_json_bytes(work)).hexdigest() == manifest['coordinator_work_sha256']
                            and _db_signature(_journal) == manifest['coordinator_journal_sha256']
                            and _artifact_inventory(updates) == manifest['coordinator_artifacts_sha256'])
                if restored:
                    control = updates / 'control' / 'jobs.sqlite'
                    if manifest['control_journal_sha256'] is None:
                        restored = not control.exists()
                    else:
                        restored = control.exists() and _db_signature(control) == manifest['control_journal_sha256']
                if restored:
                    return {'status': 'already_restored', 'base': str(base), 'revision': SOURCE_REVISION,
                            'generation': manifest['source_generation'], 'backup_dir': str(backup_dir)}
            except (MaintenanceRejected, sqlite3.Error):
                # A partial restore has no runnable coordinator. Keep services stopped and finish from the snapshot.
                pass

        if base.is_file() and updates.is_dir():
            _load_journal(base)
            _check_control_journal(updates)
        if base.is_file() and updates.is_dir() and not preserve_dir.exists():
            preserve_dir.mkdir(mode=0o700)
            _copy_snapshot_files(base, preserve_dir / 'payload')
            description = sqlite.comprobar_copia(base)
            current_journal = updates / 'jobs.sqlite'
            preservation = {
                'format': 'real-jaen-preserved-pre-restore-state-v1', 'source_base': str(base),
                'database_description': description,
                'coordinator_journal_sha256': _db_signature(current_journal) if current_journal.exists() else None,
                'payload_sha256': {path.relative_to(preserve_dir).as_posix(): _sha256(path)
                                   for path in sorted((preserve_dir / 'payload').rglob('*')) if path.is_file()},
            }
            preservation['complete'] = True
            preservation['manifest_sha256'] = hashlib.sha256(_json_bytes(preservation)).hexdigest()
            _write_private(preserve_dir / 'manifest.json', preservation)

        stage = base.parent / f'.{base.name}.restore-stage-{uuid.uuid4()}'
        stage.mkdir(mode=0o700)
        _copy_sqlite(saved['base'], stage / base.name)
        _copy_tree(saved['updates'], stage / updates.name)
        if sqlite.comprobar_copia(stage / base.name) != manifest['source_description']:
            raise MaintenanceRejected('La copia preparada para restaurar no coincide con la revisión 0004')
        staged_journal = stage / updates.name / 'jobs.sqlite'
        if _db_signature(staged_journal) != manifest['coordinator_journal_sha256']:
            raise MaintenanceRejected('El journal preparado para restaurar no coincide con el manifiesto')

        quarantine = base.parent / f'.{base.name}.preserved-before-restore-{uuid.uuid4()}'
        quarantine.mkdir(mode=0o700)
        for item in (base, *(base.with_name(base.name + suffix) for suffix in sqlite.ACOMPANANTES), updates):
            if item.exists():
                os.replace(item, quarantine / item.name)
        os.replace(stage / base.name, base)
        os.replace(stage / updates.name, updates)
        # `copias/` is append-only: keep migration and restore copies instead of replacing that archive.
        shutil.rmtree(stage)
        if sqlite.comprobar_copia(base) != manifest['source_description']:
            raise MaintenanceRejected('La base restaurada no reproduce la descripción previa')
        journal, meta, work = _load_journal(base)
        if (meta.get('codigo') != LEGACY_COORDINATOR_CODE or meta.get('ambito') != SCOPE_SHA256
                or meta.get('generacion') != manifest['source_generation'] or meta.get('descripcion') != manifest['source_description']
                or hashlib.sha256(_json_bytes(work)).hexdigest() != manifest['coordinator_work_sha256']
                or _db_signature(journal) != manifest['coordinator_journal_sha256']):
            raise MaintenanceRejected('El par restaurado no coincide con su generación, historial y journal originales')
        _check_control_journal(updates)
        control = updates / 'control' / 'jobs.sqlite'
        if manifest['control_journal_sha256'] is not None and _db_signature(control) != manifest['control_journal_sha256']:
            raise MaintenanceRejected('El journal del control HTTP no se restauró íntegramente')
    return {'status': 'restored', 'base': str(base), 'revision': SOURCE_REVISION,
            'generation': manifest['source_generation'], 'backup_dir': str(backup_dir),
            'preserved_current_dir': str(preserve_dir), 'quarantined_state': str(quarantine),
            'database': sqlite.comprobar_copia(base)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest='operation', required=True)
    for operation in ('adopt', 'restore'):
        sub = subparsers.add_parser(operation)
        sub.add_argument('--base', type=Path, required=True)
        sub.add_argument('--scope', type=Path, required=True)
        sub.add_argument('--backup-dir', type=Path, required=True,
                         help='directorio de respaldo completo fuera del volumen deportivo')
        sub.add_argument('--confirm', required=True,
                         help='adopt: frase fija del cambio de huella; restore: frase con la huella del manifiesto')
    args = parser.parse_args()
    try:
        function = adopt if args.operation == 'adopt' else restore
        print(json.dumps(function(args.base, args.scope, args.backup_dir, args.confirm), ensure_ascii=False, indent=2))
    except (MaintenanceRejected, RuntimeError, ValueError, OSError, sqlite3.Error) as error:
        parser.exit(1, f'Mantenimiento rechazado: {error}\n')


if __name__ == '__main__':
    main()
