"""Conexión PostgreSQL directa para Sports Data; la aplicación sigue usando SQLite en este hito."""
import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url


CA_BUNDLE_PREDETERMINADO = '/etc/ssl/certs/ca-certificates.crt'
BASES_SPORTS_DATA = {
    'SPORTS_DATA_DATABASE_URL': 'sports_data',
    'SPORTS_DATA_MIGRATION_DATABASE_URL': 'sports_data',
}


def url_postgresql(variable: str):
    """Lee una URL directa del entorno y fuerza TLS con validación de CA y hostname."""
    base_esperada = BASES_SPORTS_DATA.get(variable)
    if base_esperada is None:
        raise ValueError(f'Variable PostgreSQL no admitida: {variable}')

    valor = os.environ.get(variable)
    if not valor:
        raise RuntimeError(f'Falta la variable de entorno {variable}')
    try:
        url = make_url(valor)
    except Exception:
        raise RuntimeError(f'{variable} no contiene una URL PostgreSQL válida') from None

    if url.drivername not in {'postgresql', 'postgresql+psycopg'}:
        raise RuntimeError(f'{variable} debe usar PostgreSQL con Psycopg 3')
    if url.database != base_esperada:
        raise RuntimeError(f'{variable} debe apuntar a la base {base_esperada}')
    if url.host and '-pooler.' in url.host:
        raise RuntimeError(f'{variable} debe usar el endpoint directo de Neon')

    ca_bundle = Path(os.environ.get('SSL_CERT_FILE') or CA_BUNDLE_PREDETERMINADO)
    if not ca_bundle.is_file():
        raise RuntimeError('No se encuentra el bundle de CA necesario para validar TLS')

    opciones = dict(url.query)
    opciones.update({
        'sslmode': 'verify-full',
        'sslrootcert': str(ca_bundle),
        'channel_binding': 'require',
    })
    return url.set(drivername='postgresql+psycopg', query=opciones)


def crear_motor_postgresql():
    """Motor futuro de ejecución: pool pequeño, sin keepalives ni actividad en segundo plano."""
    return create_engine(
        url_postgresql('SPORTS_DATA_DATABASE_URL'),
        pool_size=2,
        max_overflow=0,
        pool_timeout=10,
        pool_recycle=300,
        pool_use_lifo=True,
        pool_pre_ping=True,
        connect_args={'connect_timeout': 10},
    )
