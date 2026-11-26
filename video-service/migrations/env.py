from alembic import context
from sqlalchemy import pool, text
from sqlalchemy.engine import create_engine

from video_service.postgresql import database_url

config = context.config


def run_migrations_offline() -> None:
    context.configure(
        url=database_url("VIDEO_SERVICE_MIGRATION_DATABASE_URL"),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table="video_service_alembic_version",
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(
        database_url("VIDEO_SERVICE_MIGRATION_DATABASE_URL"),
        poolclass=pool.NullPool,
        connect_args={"connect_timeout": 10},
        hide_parameters=True,
    )
    try:
        with engine.connect() as connection:
            database, role, version, has_version = connection.execute(text(
                "SELECT current_database(), current_user, current_setting('server_version_num')::integer, "
                "to_regclass('public.video_service_alembic_version') IS NOT NULL"
            )).one()
            if database != "video_service" or role != "video_service_owner" or not 180000 <= version < 190000:
                raise RuntimeError("Las migraciones requieren video_service, PostgreSQL 18 y video_service_owner.")
            if not has_version:
                objects = connection.execute(text(
                    "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','S','f')"
                )).scalar_one()
                if objects:
                    raise RuntimeError("La base video_service debe estar vacía antes de la revisión inicial.")
            connection.rollback()
            context.configure(connection=connection, version_table="video_service_alembic_version")
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
