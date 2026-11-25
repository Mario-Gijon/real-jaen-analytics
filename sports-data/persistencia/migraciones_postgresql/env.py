"""Entorno PostgreSQL de desarrollo. La configuración y la historia SQLite permanecen separadas."""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from persistencia.postgresql import url_postgresql

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def run_migrations_online() -> None:
    url = url_postgresql('SPORTS_DATA_MIGRATION_DATABASE_URL')
    motor = create_engine(url, poolclass=NullPool, connect_args={'connect_timeout': 10})
    try:
        with motor.connect() as conexion:
            database, role, version, ya_inicializada = conexion.execute(text(
                "SELECT current_database(), current_user, current_setting('server_version_num')::integer, "
                "to_regclass('public.alembic_version') IS NOT NULL"
            )).one()
            if database != 'sports_data' or role != 'neondb_owner' or version < 180000:
                raise RuntimeError('La migración PostgreSQL solo admite sports_data, PostgreSQL 18 y neondb_owner')
            if not ya_inicializada:
                objetos = conexion.execute(text(
                    "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')"
                )).scalar_one()
                if objetos:
                    raise RuntimeError('La base sports_data debe estar vacía antes de la migración inicial')

            # The preflight SELECTs start an implicit SQLAlchemy transaction. End it
            # before Alembic configures its own transaction so the migration commits.
            conexion.rollback()
            context.configure(connection=conexion, target_metadata=None, compare_type=True,
                              compare_server_default=True, transaction_per_migration=True)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        motor.dispose()


if context.is_offline_mode():
    raise SystemExit('El modo offline no se usa para la migración PostgreSQL de desarrollo')
run_migrations_online()
