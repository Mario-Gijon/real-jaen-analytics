"""Nombres efectivos de una instalación a partir de su configuración de Docker Compose ya resuelta.

Lo usa dashboard/scripts/respaldo-instalacion.sh, que ejecuta
`docker compose ... config --format json` (Compose aplica el archivo, el --env-file, el entorno y los valores por
defecto: RJ_DEV_PROJECT_NAME, RJ_DEV_VOLUME_PREFIX, COMPOSE_PROJECT_NAME, RJ_PROD_*_VOLUME...) y pasan su salida por
la entrada estándar a este archivo, montado en solo lectura en un contenedor efímero sin red de la imagen de Sports
Data. Solo biblioteca estándar.

  docker compose ... config --format json | python instalacion_compose.py

Escribe una línea `clave=valor` por dato (project, sports_data_image, sports_state, acquisition_jobs,
acquisition_profiles, video_state, imports). Comprueba que cada servicio monta su volumen de datos donde los scripts lo
esperan, de modo que nunca se respalda ni restaura un volumen supuesto. Ante cualquier dato ausente o inesperado
termina con código 1 sin escribir nada en la salida estándar.
"""
from __future__ import annotations

import json
import re
import sys

PROJECT = re.compile(r'[a-z0-9][a-z0-9_-]*')  # Docker Compose project names
VOLUME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]+')  # Docker volume names
# (service, mount target, volume key in the Compose file): the persistent data the scripts back up and restore.
DATA_MOUNTS = (
    ('sports-data', '/state', 'sports-state'),
    ('acquisition', '/acquisition-jobs', 'acquisition-jobs'),
    ('acquisition', '/profiles', 'acquisition-profiles'),
    ('video-service', '/state', 'video-state'),
)


class ConfigError(ValueError):
    pass


def resolve(config: dict) -> dict[str, str]:
    """The installation's effective names from `docker compose config --format json`."""
    if not isinstance(config, dict):
        raise ConfigError('La configuración de Compose no es un objeto JSON')
    project = config.get('name')
    if not isinstance(project, str) or not PROJECT.fullmatch(project):
        raise ConfigError(f'Nombre de proyecto de Compose no válido: {project!r}')
    services = config.get('services') or {}
    volumes = config.get('volumes') or {}

    def service(name):
        value = services.get(name)
        if not isinstance(value, dict):
            raise ConfigError(f'La configuración no tiene el servicio {name}')
        return value

    def mount(name, target):
        found = [m for m in service(name).get('volumes') or [] if isinstance(m, dict) and m.get('target') == target]
        if len(found) != 1:
            raise ConfigError(f'{name} debe montar exactamente un origen en {target}')
        return found[0]

    result = {'project': project}
    image = service('sports-data').get('image')
    if not isinstance(image, str) or not image or any(c.isspace() for c in image):
        raise ConfigError(f'Imagen de sports-data no válida: {image!r}')
    result['sports_data_image'] = image
    for name, target, key in DATA_MOUNTS:
        m = mount(name, target)
        if m.get('type') != 'volume' or m.get('source') != key:
            raise ConfigError(f'{name} debe montar el volumen {key} en {target}')
        if m.get('read_only'):
            raise ConfigError(f'{name} monta {key} en solo lectura')
        definition = volumes.get(key)
        volume = definition.get('name') if isinstance(definition, dict) else None
        if not isinstance(volume, str) or not VOLUME.fullmatch(volume):
            raise ConfigError(f'Nombre de volumen no válido para {key}: {volume!r}')
        result[key.replace('-', '_')] = volume
    names = [result[key.replace('-', '_')] for _, _, key in DATA_MOUNTS]
    if len(set(names)) != len(names):
        raise ConfigError(f'Los volúmenes de datos deben ser distintos: {names}')
    imports = mount('sports-data', '/imports')
    source = imports.get('source')
    if imports.get('type') != 'bind' or not isinstance(source, str) or not source or '\n' in source:
        raise ConfigError('sports-data debe montar una carpeta del anfitrión en /imports')
    result['imports'] = source
    catalog = mount('video-service', '/app/data')
    catalog_source = catalog.get('source')
    if (catalog.get('type') != 'bind' or catalog.get('read_only') is not True
            or not isinstance(catalog_source, str) or not catalog_source or '\n' in catalog_source):
        raise ConfigError('video-service debe montar su catálogo externo en solo lectura en /app/data')
    environment = service('video-service').get('environment') or {}
    manifest = environment.get('VIDEO_SERVICE_MANIFEST', 'videos.json') if isinstance(environment, dict) else None
    if (not isinstance(manifest, str) or not manifest or manifest.startswith('/')
            or '\\' in manifest or '..' in manifest.split('/') or '\n' in manifest):
        raise ConfigError(f'Nombre de manifiesto de vídeo no válido: {manifest!r}')
    result['video_catalog_dir'] = catalog_source
    result['video_manifest'] = manifest
    return result


def main(argv=None) -> int:
    try:
        resolved = resolve(json.load(sys.stdin))
    except (ConfigError, ValueError) as error:
        print(f'Configuración de Compose: {error}', file=sys.stderr)
        return 1
    sys.stdout.write(''.join(f'{key}={value}\n' for key, value in resolved.items()))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
