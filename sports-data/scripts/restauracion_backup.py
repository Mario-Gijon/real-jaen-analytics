"""Validate and safely extract the portable v2 installation backup. Standard library plus this image's SQLite code."""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import sys
import tarfile
import tempfile

FORMAT = 2
HASH_LINE = re.compile(r'^([0-9a-f]{64}) [ *](\./[A-Za-z0-9_.-]+)$')


class RestoreError(ValueError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _json(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RestoreError(f'{label} no es JSON legible: {error}') from None
    if not isinstance(value, dict):
        raise RestoreError(f'{label} debe ser un objeto JSON')
    return value


def _hashes(root: Path) -> dict[str, str]:
    sums = root / 'SHA256SUMS'
    if sums.is_symlink() or not sums.is_file():
        raise RestoreError('Falta SHA256SUMS regular')
    result: dict[str, str] = {}
    try:
        lines = sums.read_text(encoding='ascii').splitlines()
    except (OSError, UnicodeError) as error:
        raise RestoreError(f'SHA256SUMS no se puede leer: {error}') from None
    for line in lines:
        match = HASH_LINE.fullmatch(line)
        if not match:
            raise RestoreError('SHA256SUMS contiene una línea no admitida')
        name = match.group(2)[2:]
        if name in result:
            raise RestoreError(f'SHA256SUMS repite {name}')
        result[name] = match.group(1)
    return result


def _safe_members(path: Path, prefix: str | None, *, strip_root: bool = False) -> list[tuple[tarfile.TarInfo, tuple[str, ...]]]:
    try:
        archive = tarfile.open(path, 'r:')
    except (tarfile.TarError, OSError) as error:
        raise RestoreError(f'{path.name} no es un archivo tar válido: {error}') from None
    with archive:
        members = archive.getmembers()
        if len(members) > 100_000:
            raise RestoreError(f'{path.name} contiene demasiadas entradas')
        seen: set[tuple[str, ...]] = set()
        result = []
        for member in members:
            name = member.name
            raw = name.removeprefix('./') if strip_root else name
            if (not raw or raw.startswith('/') or '\\' in raw or any(ord(char) < 32 for char in raw)
                    or any(part == '..' for part in raw.split('/'))):
                raise RestoreError(f'{path.name} contiene una ruta insegura: {name!r}')
            parts = tuple(part for part in raw.split('/') if part not in ('', '.'))
            if not parts:
                if member.isdir() and strip_root:
                    continue
                raise RestoreError(f'{path.name} contiene una ruta vacía: {name!r}')
            if prefix:
                if parts[0] != prefix:
                    raise RestoreError(f'{path.name} debe quedar bajo {prefix}/: {name!r}')
            if not (member.isfile() or member.isdir()) or member.issym() or member.islnk():
                raise RestoreError(f'{path.name} contiene un enlace o archivo especial: {name!r}')
            if member.size < 0 or member.size > 2**63 - 1:
                raise RestoreError(f'{path.name} contiene un tamaño no válido')
            if parts in seen:
                raise RestoreError(f'{path.name} repite la ruta {name!r}')
            seen.add(parts)
            result.append((member, parts))
        return result


def _check_archive_sqlite(archive: Path, member_name: str, *, prefix: str | None = None,
                          strip_root: bool = False, strip_prefix: bool = False) -> None:
    with tempfile.TemporaryDirectory(prefix='rja-restore-check-') as scratch:
        destination = Path(scratch) / 'extract'
        extract(archive, destination, prefix=prefix, strip_root=strip_root, strip_prefix=strip_prefix)
        database = destination / member_name
        try:
            with closing(sqlite3.connect(f'{database.resolve().as_uri()}?mode=ro', uri=True)) as connection:
                integrity = connection.execute('PRAGMA integrity_check').fetchone()[0]
                foreign_keys = connection.execute('PRAGMA foreign_key_check').fetchall()
        except sqlite3.DatabaseError as error:
            raise RestoreError(f'{archive.name}:{member_name} no es una SQLite válida: {error}') from None
        if integrity != 'ok' or foreign_keys:
            raise RestoreError(f'{archive.name}:{member_name} no supera integrity_check o foreign_key_check')


def validate(backup: Path, *, sports_sqlite_module: bool = True) -> dict:
    root = backup.resolve(strict=True)
    if not root.is_dir() or backup.is_symlink():
        raise RestoreError('El respaldo debe ser un directorio regular, no un enlace')
    entries = list(root.iterdir())
    if any(entry.is_symlink() or not entry.is_file() for entry in entries):
        raise RestoreError('El respaldo contiene carpetas o enlaces no admitidos en su raíz')
    manifest = _json(root / 'manifest.json', 'manifest.json')
    if manifest.get('formato_respaldo') != FORMAT:
        raise RestoreError('Solo se admite formato_respaldo=2; los formatos anteriores no acreditan fotos ni catálogo externo completos')
    photos = manifest.get('fotos')
    catalog = manifest.get('catalogo_video')
    if not isinstance(photos, dict) or photos.get('formato') != 1 or not isinstance(photos.get('incluidas'), bool):
        raise RestoreError('El manifiesto v2 no describe el almacén de fotografías')
    if not isinstance(catalog, dict) or catalog.get('formato') != 1 or catalog.get('incluida') is not True:
        raise RestoreError('El manifiesto v2 no describe el catálogo externo de vídeo incluido')
    expected = {'real-jaen.sqlite', 'actualizaciones.tar', 'acquisition-jobs.tar',
                'video-service-state.tar', 'video-catalog.tar', 'copiar.json', 'manifest.json'}
    if (root / 'procedencia.tar').exists():
        expected.add('procedencia.tar')
    if photos['incluidas']:
        expected.add('fotos.tar')
    hashes = _hashes(root)
    if set(hashes) != expected:
        missing, extra = sorted(expected - set(hashes)), sorted(set(hashes) - expected)
        raise RestoreError(f'SHA256SUMS no coincide con el inventario (faltan {missing}; sobran {extra})')
    actual_files = {entry.name for entry in entries if entry.name != 'SHA256SUMS'}
    if actual_files != expected:
        raise RestoreError(f'El inventario no coincide con el manifiesto (faltan {sorted(expected-actual_files)}; sobran {sorted(actual_files-expected)})')
    for name, expected_hash in hashes.items():
        path = root / name
        if path.is_symlink() or not path.is_file() or sha256(path) != expected_hash:
            raise RestoreError(f'{name}: falta, no es regular o no coincide con SHA-256')

    copied = _json(root / 'copiar.json', 'copiar.json')
    if (copied.get('sha256_archivo') != sha256(root / 'real-jaen.sqlite')
            or copied.get('revision') != manifest.get('revision')
            or copied.get('huella') != manifest.get('huella')):
        raise RestoreError('copiar.json, manifest.json y real-jaen.sqlite no son coherentes')
    if not isinstance(manifest.get('generacion'), str) or not manifest['generacion']:
        raise RestoreError('El manifiesto no registra una generación deportiva válida')
    try:
        with closing(sqlite3.connect(f'{(root / "real-jaen.sqlite").as_uri()}?mode=ro', uri=True)) as connection:
            integrity = connection.execute('PRAGMA integrity_check').fetchone()[0]
            foreign_keys = connection.execute('PRAGMA foreign_key_check').fetchall()
            revision_row = connection.execute('SELECT version_num FROM alembic_version').fetchone()
    except sqlite3.DatabaseError as error:
        raise RestoreError(f'real-jaen.sqlite no es una base válida: {error}') from None
    if (integrity != 'ok' or integrity != manifest.get('integridad') or foreign_keys
            or not revision_row or revision_row[0] != manifest.get('revision')):
        raise RestoreError('real-jaen.sqlite no supera integridad, claves foráneas o revisión declarada')

    update_members = _safe_members(root / 'actualizaciones.tar', 'real-jaen.sqlite.actualizaciones')
    update_files = {parts for member, parts in update_members if member.isfile()}
    if (('real-jaen.sqlite.actualizaciones', 'jobs.sqlite') not in update_files
            or ('real-jaen.sqlite.actualizaciones', 'control', 'jobs.sqlite') not in update_files):
        raise RestoreError('actualizaciones.tar no contiene los diarios del coordinador y su control')
    _check_archive_sqlite(root / 'actualizaciones.tar', 'jobs.sqlite',
                          prefix='real-jaen.sqlite.actualizaciones', strip_prefix=True)
    _check_archive_sqlite(root / 'actualizaciones.tar', 'control/jobs.sqlite',
                          prefix='real-jaen.sqlite.actualizaciones', strip_prefix=True)
    if 'procedencia.tar' in expected:
        _safe_members(root / 'procedencia.tar', 'procedencia')
    acquisition_members = _safe_members(root / 'acquisition-jobs.tar', None, strip_root=True)
    if not any(member.isfile() and parts == ('jobs.sqlite',) for member, parts in acquisition_members):
        raise RestoreError('acquisition-jobs.tar no contiene jobs.sqlite')
    _check_archive_sqlite(root / 'acquisition-jobs.tar', 'jobs.sqlite', strip_root=True)
    video_members = _safe_members(root / 'video-service-state.tar', None, strip_root=True)
    if not any(member.isfile() and parts == ('video-service.sqlite',) for member, parts in video_members):
        raise RestoreError('video-service-state.tar no contiene video-service.sqlite')
    if any(member.isfile() and parts == ('.real-jaen-restore-incomplete',) for member, parts in video_members):
        raise RestoreError('video-service-state.tar intenta incluir el marcador reservado de restauración')
    _check_archive_sqlite(root / 'video-service-state.tar', 'video-service.sqlite', strip_root=True)
    if photos['incluidas']:
        _safe_members(root / 'fotos.tar', 'player-photos')
    _safe_members(root / 'video-catalog.tar', 'video-catalog')

    if sports_sqlite_module:
        from persistencia import sqlite as sports_sqlite
        description = sports_sqlite.comprobar_copia(root / 'real-jaen.sqlite')
        for key in ('revision', 'huella', 'integridad'):
            if description.get(key) != manifest.get(key):
                raise RestoreError(f'real-jaen.sqlite no coincide con el manifiesto en {key}')
    return manifest


def extract(archive_path: Path, destination: Path, *, prefix: str | None = None,
            strip_root: bool = False, strip_prefix: bool = False) -> None:
    """Extract regular files/directories only, never following links or overwriting an existing file."""
    members = _safe_members(archive_path, prefix, strip_root=strip_root)
    destination.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink() or any(destination.iterdir()):
        raise RestoreError(f'El destino de extracción no está vacío: {destination}')
    directories: list[tuple[Path, int]] = []
    try:
        with tarfile.open(archive_path, 'r:') as archive:
            by_name = {item.name: item for item in archive.getmembers()}
            for member, original_parts in members:
                parts = original_parts
                if strip_prefix:
                    if not prefix or parts[0] != prefix:
                        raise RestoreError(f'La ruta {member.name!r} no comienza por {prefix}/')
                    parts = parts[1:]
                    if not parts:
                        if member.isdir():
                            continue
                        raise RestoreError(f'No se puede extraer la raíz del archivo como archivo: {member.name!r}')
                target = destination.joinpath(*parts)
                if not target.resolve().is_relative_to(destination.resolve()):
                    raise RestoreError(f'La ruta {member.name!r} sale del destino')
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=False)
                    directories.append((target, member.mode & 0o777 & ~0o6000))
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                source = archive.extractfile(by_name[member.name])
                if source is None:
                    raise RestoreError(f'No se pudo leer {member.name!r}')
                with source, target.open('xb') as output:
                    shutil.copyfileobj(source, output, 1 << 20)
                target.chmod(member.mode & 0o777 & ~0o6000)
        for path, mode in sorted(directories, key=lambda entry: len(entry[0].parts), reverse=True):
            path.chmod(mode)
    except (tarfile.TarError, OSError, EOFError) as error:
        raise RestoreError(f'No se pudo extraer {archive_path.name}: {error}') from None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    check = sub.add_parser('validar')
    check.add_argument('--backup', type=Path, required=True)
    extraction = sub.add_parser('extraer')
    extraction.add_argument('--archive', type=Path, required=True)
    extraction.add_argument('--destination', type=Path, required=True)
    extraction.add_argument('--prefix')
    extraction.add_argument('--strip-root', action='store_true')
    extraction.add_argument('--strip-prefix', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.command == 'validar':
            value = validate(args.backup)
            print(json.dumps({'formato_respaldo': FORMAT, 'generacion': value['generacion']}, ensure_ascii=False))
        else:
            extract(args.archive, args.destination, prefix=args.prefix, strip_root=args.strip_root,
                    strip_prefix=args.strip_prefix)
    except (OSError, RestoreError, ValueError) as error:
        print(f'Restauración: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
