"""Secure PostgreSQL connections independent of hosting provider."""
from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import URL, make_url


DEFAULT_CA_BUNDLE = "/etc/ssl/certs/ca-certificates.crt"
EXPECTED_DATABASE = "video_service"
EXPECTED_ROLES = {
    "VIDEO_SERVICE_DATABASE_URL": "video_service_app",
    "VIDEO_SERVICE_MIGRATION_DATABASE_URL": "video_service_owner",
}
_TLS_QUERY_KEYS = {"sslmode", "sslrootcert", "channel_binding"}


def _read_url(variable: str) -> str:
    file_variable = f"{variable}_FILE"
    direct_value = os.environ.get(variable)
    file_path = os.environ.get(file_variable)
    if direct_value and file_path:
        raise RuntimeError(f"Configura solo {variable} o {file_variable}, no ambos.")
    if file_path:
        try:
            value = Path(file_path).read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            raise RuntimeError(f"No se pudo leer el secreto de {variable}.") from None
    else:
        value = (direct_value or "").strip()
    if not value:
        raise RuntimeError(f"Falta la variable de entorno {variable}.")
    return value


def _normalize_host(host: str) -> str:
    return host.strip().strip("[]").rstrip(".").lower()


def _allowed_hosts(variable: str) -> set[str]:
    raw = os.environ.get(variable, "")
    hosts = {_normalize_host(host) for host in raw.split(",") if host.strip()}
    if not hosts or "*" in hosts:
        raise RuntimeError(f"{variable} debe enumerar hosts exactos autorizados.")
    return hosts


def database_url(variable: str) -> URL:
    """Validate the database/role and force verified TLS unless loopback/private-Docker is explicit."""
    if variable not in EXPECTED_ROLES:
        raise ValueError("Variable PostgreSQL de Video Service no admitida.")
    try:
        url = make_url(_read_url(variable))
    except RuntimeError:
        raise
    except Exception:
        raise RuntimeError(f"{variable} no contiene una URL PostgreSQL válida.") from None
    if url.drivername not in {"postgresql", "postgresql+psycopg"}:
        raise RuntimeError(f"{variable} debe utilizar PostgreSQL con Psycopg 3.")
    if url.database != EXPECTED_DATABASE:
        raise RuntimeError(f"{variable} debe apuntar a la base {EXPECTED_DATABASE}.")
    if url.username != EXPECTED_ROLES[variable]:
        raise RuntimeError(f"{variable} debe usar el rol {EXPECTED_ROLES[variable]}.")
    if not url.host or "," in url.host:
        raise RuntimeError(f"{variable} debe señalar un único host PostgreSQL explícito.")

    host = _normalize_host(url.host)
    if variable == "VIDEO_SERVICE_MIGRATION_DATABASE_URL":
        if host not in _allowed_hosts("VIDEO_SERVICE_MIGRATION_ALLOWED_HOSTS"):
            raise RuntimeError("El host de migración de Video Service no está autorizado.")

    options = dict(url.query)
    if set(options) - _TLS_QUERY_KEYS or any(not isinstance(value, str) for value in options.values()):
        raise RuntimeError(f"{variable} contiene parámetros de URL no permitidos.")

    loopback = host in {"localhost", "127.0.0.1", "::1"}
    allow_local = (os.environ.get("POSTGRES_ALLOW_INSECURE_LOCAL") == "1"
                   or os.environ.get("VIDEO_SERVICE_ALLOW_INSECURE_LOCAL") == "1")
    private_hosts = {
        _normalize_host(value) for value in os.environ.get("POSTGRES_PRIVATE_DOCKER_HOSTS", "").split(",")
        if value.strip()
    }
    private_docker = host == "postgresql" and host in private_hosts
    if loopback:
        if not allow_local:
            raise RuntimeError("Las conexiones locales sin TLS requieren autorización explícita.")
        options = {"sslmode": "disable"}
    elif private_docker:
        if os.environ.get("POSTGRES_ALLOW_PRIVATE_DOCKER_NO_TLS") != "1":
            raise RuntimeError("La conexión Docker privada sin TLS requiere autorización explícita.")
        options = {"sslmode": "disable"}
    else:
        ca_bundle = Path(os.environ.get("SSL_CERT_FILE") or DEFAULT_CA_BUNDLE)
        if not ca_bundle.is_file():
            raise RuntimeError("No se encuentra el bundle de CA necesario para validar TLS.")
        options = {
            "sslmode": "verify-full",
            "sslrootcert": str(ca_bundle),
            "channel_binding": "require",
        }
    return url.set(drivername="postgresql+psycopg", query=options)


def create_video_service_engine(variable: str = "VIDEO_SERVICE_DATABASE_URL"):
    """Create a small reusable pool; SQL parameters stay out of engine logs."""
    return create_engine(
        database_url(variable),
        pool_size=2,
        max_overflow=0,
        pool_timeout=10,
        pool_recycle=300,
        pool_use_lifo=True,
        pool_pre_ping=True,
        hide_parameters=True,
        connect_args={"connect_timeout": 10},
    )
