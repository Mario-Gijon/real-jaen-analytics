from alembic import context
from sqlalchemy import pool
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
    with engine.connect() as connection:
        context.configure(connection=connection, version_table="video_service_alembic_version")
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
