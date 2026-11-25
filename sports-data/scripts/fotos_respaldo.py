"""Copia comprobada del almacén de fotos persistente de Sports Data.

Se ejecuta desde el script administrativo de backup dentro de un contenedor efímero, con /state en solo lectura.
Solo usa la biblioteca estándar. El almacén vive en /state/player-photos; el tar conserva ese nombre y omite únicamente
`tmp/`, que es área de trabajo. La restauración se valida en un área temporal y no sobrescribe /state/player-photos.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import sqlite3
import sys
import tarfile
import tempfile

FORMAT = 1
TOP = 'player-photos'
SKIPPED = ('tmp',)
DATABASE = 'fotos.sqlite'


class BackupError(Exception):
    pass


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _files(store: Path) -> list[str]:
    names = []
    for current, dirs, files in os.walk(store, followlinks=False):
        here = Path(current)
        relative = here.relative_to(store)
        dirs[:] = sorted(d for d in dirs if not (relative == Path('.') and d in SKIPPED))
        for name in [*dirs, *files]:
            path = here / name
            if path.is_symlink() or not (path.is_dir() or path.is_file()):
                raise BackupError(f'Elemento inesperado en el almacén de fotografías: {path.relative_to(store)}')
        names.extend(PurePosixPath(TOP, *relative.parts, name).as_posix() for name in sorted(files))
    return names


def _summary(store: Path) -> dict:
    """Validate a temporary copy; opening the live SQLite file can otherwise recover a journal."""
    if not (store / DATABASE).is_file():
        raise BackupError('Falta fotos.sqlite en el almacén de fotografías')
    file_digest = hashlib.sha256()
    count = 0
    for name in _files(store):
        relative = PurePosixPath(name).relative_to(TOP)
        if relative.name.startswith(DATABASE):
            continue
        file_digest.update(f'{relative}\0{_sha(store.joinpath(*relative.parts))}\n'.encode())
        count += 1
    connection = sqlite3.connect(store / DATABASE)
    try:
        if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise BackupError('fotos.sqlite no supera integrity_check')
        if connection.execute('PRAGMA foreign_key_check').fetchall():
            raise BackupError('fotos.sqlite tiene referencias rotas')
        q = lambda sql: connection.execute(sql).fetchall()
        blobs = q('SELECT sha256, bytes FROM blob ORDER BY sha256')
        directory = store / 'blobs'
        known = set()
        for sha256, size in blobs:
            matches = sorted(directory.glob(f'{sha256}.*')) if directory.is_dir() else []
            if len(matches) != 1:
                raise BackupError(f'Blob {sha256[:12]}…: {len(matches)} archivos en lugar de 1')
            if matches[0].stat().st_size != size or _sha(matches[0]) != sha256:
                raise BackupError(f'Blob {sha256[:12]}…: no coincide con su tamaño o su SHA-256')
            known.add(matches[0].name)
        loose = sorted(p.name for p in directory.iterdir() if p.name not in known) if directory.is_dir() else []
        index_digest = hashlib.sha256()
        for table in ('blob', 'photo', 'photo_import', 'photo_import_entry'):
            for row in q(f'SELECT * FROM {table} ORDER BY 1, 2'):
                index_digest.update(json.dumps([table, *row], ensure_ascii=False).encode() + b'\n')
        status = dict(q('SELECT status, COUNT(*) FROM photo GROUP BY status'))
        return dict(formato=FORMAT, incluidas=True, ruta='/state/player-photos',
                    fotos_activas=status.get('active', 0), fotos_sustituidas=status.get('superseded', 0),
                    blobs=len(blobs), bytes_blobs=sum(size for _, size in blobs),
                    importaciones=q('SELECT COUNT(*) FROM photo_import')[0][0],
                    entradas=q('SELECT COUNT(*) FROM photo_import_entry')[0][0], blobs_sin_indice=len(loose),
                    archivos=count, huella_indice=index_digest.hexdigest(), huella_archivos=file_digest.hexdigest())
    except sqlite3.DatabaseError as error:
        raise BackupError(f'fotos.sqlite no se puede leer: {error}') from None
    finally:
        connection.close()


def _extract_checked(archive_path: Path, destination: Path) -> Path:
    """Extract only safe regular files and directories under player-photos/."""
    try:
        with tarfile.open(archive_path) as archive:
            members = archive.getmembers()
            names = set()
            for member in members:
                path = PurePosixPath(member.name)
                normalized = tuple(part for part in path.parts if part not in ('', '.'))
                if (not (member.isfile() or member.isdir()) or path.is_absolute() or '\\' in member.name
                        or '..' in path.parts or not normalized or normalized[0] != TOP
                        or normalized in names or any(ord(char) < 32 for char in member.name)):
                    raise BackupError(f'Entrada no admitida en el archivo de fotografías: {member.name!r}')
                names.add(normalized)
            if (TOP, DATABASE) not in names:
                raise BackupError('El archivo de fotografías no contiene fotos.sqlite')
            for member in members:
                target = destination.joinpath(*PurePosixPath(member.name).parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    source = archive.extractfile(member)
                    if source is None:
                        raise BackupError(f'No se pudo leer {member.name}')
                    with source, target.open('xb') as output:
                        while chunk := source.read(1 << 20):
                            output.write(chunk)
    except (tarfile.TarError, OSError, EOFError) as error:
        if isinstance(error, BackupError):
            raise
        raise BackupError(f'El archivo de fotografías está dañado o incompleto: {error}') from None
    return destination / TOP


def restaurar(state: Path, archive_path: Path, manifest_path: Path) -> dict:
    """Restore the complete photo store to /state/player-photos without replacing existing data."""
    state = state.resolve(strict=True)
    target = state / TOP
    stage = state / '.player-photos.restore-incomplete'
    if target.exists() or target.is_symlink() or stage.exists() or stage.is_symlink():
        raise BackupError('El almacén o el área de restauración de fotografías ya existe; no se sobrescribe')
    try:
        document = json.loads(manifest_path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise BackupError(f'No se puede leer manifest.json: {error}') from None
    expected = document.get('fotos') if isinstance(document, dict) else None
    if not isinstance(expected, dict) or expected.get('formato') != FORMAT or expected.get('incluidas') is not True:
        raise BackupError('El manifiesto no declara una colección de fotografías incluida')
    stage.mkdir(mode=0o700)
    try:
        restored = _extract_checked(archive_path, stage)
        actual = _summary(restored)
        if actual != expected:
            raise BackupError('El almacén extraído no coincide con el resumen fotos del manifiesto')
        restored.rename(target)
        stage.rmdir()
        return actual
    except BaseException:
        # Keep the partial staging directory and the outer restore marker for manual diagnosis/recovery.
        raise


def validar_respaldo(archive_path: Path, manifest_path: Path) -> dict:
    """Validate photo archive contents and hashes in disposable scratch space, without touching a service volume."""
    try:
        document = json.loads(manifest_path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise BackupError(f'No se puede leer manifest.json: {error}') from None
    expected = document.get('fotos') if isinstance(document, dict) else None
    if not isinstance(expected, dict) or expected.get('formato') != FORMAT or expected.get('incluidas') is not True:
        raise BackupError('El manifiesto no declara una colección de fotografías incluida')
    with tempfile.TemporaryDirectory(prefix='rja-photos-validate-') as scratch:
        actual = _summary(_extract_checked(archive_path, Path(scratch)))
    if actual != expected:
        raise BackupError('fotos.tar no coincide con el resumen del manifiesto')
    return actual


def respaldar(state: Path, out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    store = state / TOP
    if not store.exists():
        result = dict(formato=FORMAT, incluidas=False, ruta='/state/player-photos',
                      motivo='La instalación no tiene almacén de fotografías')
        _write_json(out / 'fotos.json', result)
        return result
    if store.is_symlink() or not store.is_dir():
        raise BackupError('/state/player-photos no es un directorio')
    target, partial = out / 'fotos.tar', out / '.fotos.tar.partial'
    if target.exists() or (out / 'fotos.json').exists():
        raise BackupError('El destino ya contiene un respaldo de fotografías')
    try:
        names = _files(store)
        with tarfile.open(partial, 'w', format=tarfile.PAX_FORMAT) as archive:
            archive.add(store, arcname=TOP, recursive=False)
            for current, dirs, _ in os.walk(store, followlinks=False):
                relative = Path(current).relative_to(store)
                dirs[:] = sorted(d for d in dirs if not (relative == Path('.') and d in SKIPPED))
                for name in dirs:
                    archive.add(Path(current) / name, arcname=PurePosixPath(TOP, *relative.parts, name).as_posix(),
                                recursive=False)
            for name in names:
                source = store.joinpath(*PurePosixPath(name).relative_to(TOP).parts)
                archive.add(source, arcname=name, recursive=False)
        with tarfile.open(partial) as archive:
            members = archive.getmembers()
            archived = {m.name: m for m in members if m.isfile()}
            if sorted(archived) != sorted(names) or len(members) != len(archived) + sum(m.isdir() for m in members):
                raise BackupError('El archivo no contiene exactamente los archivos del almacén')
            for name in names:
                member = archived[name]
                with archive.extractfile(member) as handle:
                    if hashlib.sha256(handle.read()).hexdigest() != _sha(
                            store.joinpath(*PurePosixPath(name).relative_to(TOP).parts)):
                        raise BackupError(f'{name}: el archivo copiado no coincide con el original')
        with tempfile.TemporaryDirectory(prefix='rja-player-photos-') as scratch:
            result = _summary(_extract_checked(partial, Path(scratch)))
        os.replace(partial, target)
        _write_json(out / 'fotos.json', result)
        return result
    except BaseException:
        partial.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        (out / 'fotos.json').unlink(missing_ok=True)
        raise


def _write_json(path: Path, value: dict) -> None:
    with path.open('x', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write('\n')


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Copia verificada del almacén persistente de fotos')
    sub = parser.add_subparsers(dest='orden', required=True)
    backup = sub.add_parser('respaldar')
    backup.add_argument('--state', type=Path, required=True)
    backup.add_argument('--out', type=Path, required=True)
    restore = sub.add_parser('restaurar')
    restore.add_argument('--state', type=Path, required=True)
    restore.add_argument('--tar', type=Path, required=True)
    restore.add_argument('--manifest', type=Path, required=True)
    validate = sub.add_parser('validar')
    validate.add_argument('--tar', type=Path, required=True)
    validate.add_argument('--manifest', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.orden == 'respaldar':
            result = respaldar(args.state, args.out)
        elif args.orden == 'restaurar':
            result = restaurar(args.state, args.tar, args.manifest)
        else:
            result = validar_respaldo(args.tar, args.manifest)
    except (BackupError, OSError) as error:
        print(f'Fotografías: {error}', file=sys.stderr)
        return 1
    print(json.dumps(dict(result, orden=args.orden), ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
