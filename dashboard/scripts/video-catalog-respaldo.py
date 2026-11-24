"""Copy only the Video Service manifest and the calibration JSON files it references.

The external data directory can also contain original video files. Those are deliberately never included.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import sys
import tarfile
from urllib.parse import parse_qsl, urlsplit

PREFIX = 'video-catalog'
SENSITIVE_KEYS = {'access_token', 'api_key', 'auth', 'authorization', 'cookie', 'key', 'password', 'secret',
                  'sig', 'signature', 'token'}
SENSITIVE_QUERY_KEYS = {'access_token', 'api_key', 'auth', 'key', 'secret', 'sig', 'signature', 'token'}


class BackupError(Exception):
    pass


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1 << 20), b''):
            value.update(block)
    return value.hexdigest()


def _relative(value: str, label: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (not value or path.is_absolute() or '\\' in value or '..' in path.parts or not path.parts
            or any(ord(char) < 32 for char in value)):
        raise BackupError(f'{label}: ruta relativa no válida')
    return path


def _reject_sensitive(value, where='documento') -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in SENSITIVE_KEYS:
                raise BackupError(f'{where}: se rechaza una clave sensible ({key})')
            _reject_sensitive(item, where)
    elif isinstance(value, list):
        for item in value:
            _reject_sensitive(item, where)


def _source_file(root: Path, relative: PurePosixPath) -> Path:
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise BackupError(f'El catálogo contiene un enlace simbólico: {relative}')
    try:
        resolved = current.resolve(strict=True)
    except OSError as error:
        raise BackupError(f'No se puede leer {relative}: {error}') from None
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise BackupError(f'No es un archivo regular dentro del catálogo: {relative}')
    return resolved


def respaldar(data_dir: Path, manifest_name: str, out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    try:
        root = data_dir.expanduser().resolve(strict=True)
    except OSError as error:
        raise BackupError(f'No se puede abrir el catálogo de vídeo: {error}') from None
    if not root.is_dir():
        raise BackupError('El catálogo de vídeo no es un directorio')
    manifest_rel = _relative(manifest_name, 'VIDEO_SERVICE_MANIFEST')
    manifest_path = _source_file(root, manifest_rel)
    try:
        document = json.loads(manifest_path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise BackupError(f'No se puede leer el manifiesto de vídeo: {error}') from None
    if not isinstance(document, dict) or document.get('schema_version') != 1 or not isinstance(document.get('videos'), list):
        raise BackupError('El manifiesto debe tener schema_version=1 y videos como lista')
    _reject_sensitive(document, 'manifiesto de vídeo')
    files = {manifest_rel.as_posix(): manifest_path}
    calibrations = set()
    for index, video in enumerate(document['videos']):
        if not isinstance(video, dict):
            raise BackupError(f'videos[{index}] no es un objeto')
        source_url = video.get('source_url')
        if source_url is not None:
            if not isinstance(source_url, str):
                raise BackupError(f'videos[{index}].source_url no es texto')
            parsed = urlsplit(source_url)
            if (parsed.username is not None or parsed.password is not None
                    or any(key.lower() in SENSITIVE_QUERY_KEYS for key, _ in parse_qsl(parsed.query))):
                raise BackupError(f'videos[{index}].source_url contiene credenciales o parámetros sensibles')
        calibration_name = video.get('calibration_file')
        if calibration_name is None:
            continue
        if not isinstance(calibration_name, str):
            raise BackupError(f'videos[{index}].calibration_file no es texto')
        relative = _relative(calibration_name, f'videos[{index}].calibration_file')
        calibration = _source_file(root, relative)
        try:
            value = json.loads(calibration.read_text(encoding='utf-8'))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise BackupError(f'No se puede leer la calibración {relative}: {error}') from None
        if not isinstance(value, dict) or not isinstance(value.get('segments'), list):
            raise BackupError(f'La calibración {relative} no contiene segments')
        _reject_sensitive(value, f'calibración {relative}')
        files[relative.as_posix()] = calibration
        calibrations.add(relative.as_posix())
    target, partial = out / 'video-catalog.tar', out / '.video-catalog.tar.partial'
    if target.exists() or partial.exists():
        raise BackupError('El destino ya contiene un respaldo del catálogo de vídeo')
    try:
        with tarfile.open(partial, 'w', format=tarfile.PAX_FORMAT) as archive:
            for name, path in sorted(files.items()):
                archive.add(path, arcname=f'{PREFIX}/{name}', recursive=False)
        with tarfile.open(partial) as archive:
            members = archive.getmembers()
            expected = {f'{PREFIX}/{name}' for name in files}
            actual = {member.name for member in members if member.isfile()}
            if actual != expected or len(members) != len(expected):
                raise BackupError('El archivo de catálogo no contiene exactamente el manifiesto y sus calibraciones')
            for member in members:
                name = member.name.removeprefix(PREFIX + '/')
                with archive.extractfile(member) as source:
                    if hashlib.sha256(source.read()).hexdigest() != _digest(files[name]):
                        raise BackupError(f'{name}: el archivo copiado no coincide con el original')
        os.replace(partial, target)
        return dict(formato=1, incluida=True, directorio='/app/data', manifiesto=manifest_rel.as_posix(),
                    calibraciones=len(calibrations), archivos=len(files),
                    huella_archivos=hashlib.sha256(''.join(
                        f'{name}\0{_digest(path)}\n' for name, path in sorted(files.items())).encode()).hexdigest())
    except BaseException:
        partial.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        raise


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Copia solo el manifiesto de vídeo y sus calibraciones')
    command = parser.add_subparsers(dest='orden', required=True).add_parser('respaldar')
    command.add_argument('--data', type=Path, required=True)
    command.add_argument('--manifest', required=True)
    command.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = respaldar(args.data, args.manifest, args.out)
    except (BackupError, OSError) as error:
        print(f'Catálogo de vídeo: {error}', file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
