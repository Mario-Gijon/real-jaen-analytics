"""Secure direct PostgreSQL connection helpers for Video Service."""
from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import URL, make_url


DEFAULT_CA_BUNDLE = "/etc/ssl/certs/ca-certificates.crt"
EXPECTED_DATABASE = "video_service"


def database_url(variable: str) -> URL:
    """Resolve a Video Service URL, requiring verified TLS remotely.

    Local TLS opt-out is explicit and restricted to loopback hosts. URLs and
    driver errors are deliberately never included in exceptions.
    """
    if variable not in {"VIDEO_SERVICE_DATABASE_URL", "VIDEO_SERVICE_MIGRATION_DATABASE_URL"}:
        raise ValueError("Variable PostgreSQL de Video Service no admitida.")
    value = os.environ.get(variable)
    if not value:
        raise RuntimeError(f"Falta la variable de entorno {variable}.")
    try:
        url = make_url(value)
    except Exception:
        raise RuntimeError(f"{variable} no contiene una URL PostgreSQL válida.") from None
    if url.drivername not in {"postgresql", "postgresql+psycopg"}:
        raise RuntimeError(f"{variable} debe utilizar PostgreSQL con Psycopg 3.")
    if url.database != EXPECTED_DATABASE:
        raise RuntimeError(f"{variable} debe apuntar a la base {EXPECTED_DATABASE}.")
    if variable == "VIDEO_SERVICE_DATABASE_URL" and url.username != "video_service_app":
        raise RuntimeError("VIDEO_SERVICE_DATABASE_URL debe usar el rol video_service_app.")
    if variable == "VIDEO_SERVICE_MIGRATION_DATABASE_URL" and url.username == "video_service_app":
        raise RuntimeError("La URL de migración debe utilizar credenciales administrativas separadas.")

    host = (url.host or "").strip("[]").lower()
    loopback = host in {"localhost", "127.0.0.1", "::1"}
    options = dict(url.query)
    if loopback:
        if os.environ.get("VIDEO_SERVICE_ALLOW_INSECURE_LOCAL") != "1":
            raise RuntimeError("Las conexiones locales requieren VIDEO_SERVICE_ALLOW_INSECURE_LOCAL=1.")
        options.pop("sslrootcert", None)
        options.pop("channel_binding", None)
        options["sslmode"] = "disable"
    else:
        ca_bundle = Path(os.environ.get("SSL_CERT_FILE") or DEFAULT_CA_BUNDLE)
        if not ca_bundle.is_file():
            raise RuntimeError("No se encuentra el bundle de CA para validar TLS.")
        options.update({
            "sslmode": "verify-full",
            "sslrootcert": str(ca_bundle),
            "channel_binding": "require",
        })
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
