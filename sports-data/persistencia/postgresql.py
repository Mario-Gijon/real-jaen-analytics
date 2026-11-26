"""Conexiones PostgreSQL seguras e independientes del proveedor para Sports Data."""
from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import URL, make_url


CA_BUNDLE_PREDETERMINADO = '/etc/ssl/certs/ca-certificates.crt'
BASES_SPORTS_DATA = {
    'SPORTS_DATA_DATABASE_URL': 'sports_data',
    'SPORTS_DATA_MIGRATION_DATABASE_URL': 'sports_data',
}
ROLES_SPORTS_DATA = {
    'SPORTS_DATA_DATABASE_URL': 'sports_data_app',
    'SPORTS_DATA_MIGRATION_DATABASE_URL': 'sports_data_owner',
}
_PARAMETROS_TLS_URL = {'sslmode', 'sslrootcert', 'channel_binding'}


def _leer_url(variable: str) -> str:
    file_variable = f'{variable}_FILE'
    direct_value = os.environ.get(variable)
    file_path = os.environ.get(file_variable)
    if direct_value and file_path:
        raise RuntimeError(f'Configura solo {variable} o {file_variable}, no ambos')
    if file_path:
        try:
            value = Path(file_path).read_text(encoding='utf-8').strip()
        except (OSError, UnicodeError):
            raise RuntimeError(f'No se pudo leer el secreto de {variable}') from None
    else:
        value = (direct_value or '').strip()
    if not value:
        raise RuntimeError(f'Falta la variable de entorno {variable}')
    return value


def _normalizar_host(host: str) -> str:
    return host.strip().strip('[]').rstrip('.').lower()


def _hosts_autorizados(variable: str) -> set[str]:
    value = os.environ.get(variable, '')
    hosts = {_normalizar_host(host) for host in value.split(',') if host.strip()}
    if not hosts or '*' in hosts:
        raise RuntimeError(f'{variable} debe enumerar hosts exactos autorizados')
    return hosts


def _verificar_destino_migracion(variable: str, host: str) -> None:
    allowlist_variable = 'SPORTS_DATA_MIGRATION_ALLOWED_HOSTS'
    if _normalizar_host(host) not in _hosts_autorizados(allowlist_variable):
        raise RuntimeError(f'El host de {variable} no está en la lista de destinos autorizados')


def url_postgresql(variable: str) -> URL:
    """Valida base, rol y host; fuerza TLS remoto o una excepción local acotada."""
    base_esperada = BASES_SPORTS_DATA.get(variable)
    if base_esperada is None:
        raise ValueError('Variable PostgreSQL de Sports Data no admitida')

    try:
        url = make_url(_leer_url(variable))
    except RuntimeError:
        raise
    except Exception:
        raise RuntimeError(f'{variable} no contiene una URL PostgreSQL válida') from None

    if url.drivername not in {'postgresql', 'postgresql+psycopg'}:
        raise RuntimeError(f'{variable} debe usar PostgreSQL con Psycopg 3')
    if url.database != base_esperada:
        raise RuntimeError(f'{variable} debe apuntar a la base {base_esperada}')
    if url.username != ROLES_SPORTS_DATA[variable]:
        raise RuntimeError(f'{variable} debe usar el rol {ROLES_SPORTS_DATA[variable]}')
    if not url.host or ',' in url.host:
        raise RuntimeError(f'{variable} debe señalar un único host PostgreSQL explícito')

    host = _normalizar_host(url.host)
    if variable == 'SPORTS_DATA_MIGRATION_DATABASE_URL':
        _verificar_destino_migracion(variable, host)

    options = dict(url.query)
    if set(options) - _PARAMETROS_TLS_URL or any(not isinstance(value, str) for value in options.values()):
        raise RuntimeError(f'{variable} contiene parámetros de URL no permitidos')

    loopback = host in {'localhost', '127.0.0.1', '::1'}
    private_docker_hosts = {
        _normalizar_host(value) for value in os.environ.get('POSTGRES_PRIVATE_DOCKER_HOSTS', '').split(',')
        if value.strip()
    }
    private_docker = host == 'postgresql' and host in private_docker_hosts
    if loopback:
        if os.environ.get('POSTGRES_ALLOW_INSECURE_LOCAL') != '1':
            raise RuntimeError('Las conexiones locales sin TLS requieren POSTGRES_ALLOW_INSECURE_LOCAL=1')
        options = {'sslmode': 'disable'}
    elif private_docker:
        if os.environ.get('POSTGRES_ALLOW_PRIVATE_DOCKER_NO_TLS') != '1':
            raise RuntimeError('La conexión Docker privada sin TLS requiere autorización explícita')
        options = {'sslmode': 'disable'}
    else:
        ca_bundle = Path(os.environ.get('SSL_CERT_FILE') or CA_BUNDLE_PREDETERMINADO)
        if not ca_bundle.is_file():
            raise RuntimeError('No se encuentra el bundle de CA necesario para validar TLS')
        options = {
            'sslmode': 'verify-full',
            'sslrootcert': str(ca_bundle),
            'channel_binding': 'require',
        }

    return url.set(drivername='postgresql+psycopg', query=options)


def _crear_motor_postgresql(*, pool_size, max_overflow, pool_timeout):
    return create_engine(
        url_postgresql('SPORTS_DATA_DATABASE_URL'),
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=pool_timeout,
        pool_recycle=300,
        pool_use_lifo=True,
        pool_pre_ping=True,
        hide_parameters=True,
        connect_args={'connect_timeout': 10},
    )


def crear_motor_postgresql():
    """Main bounded pool: at most five connections per Sports Data process."""
    return _crear_motor_postgresql(pool_size=4, max_overflow=1, pool_timeout=2)


def crear_motor_heartbeat_postgresql():
    """A reserved single-connection pool keeps lease renewals independent of publication work."""
    return _crear_motor_postgresql(pool_size=1, max_overflow=0, pool_timeout=1)
