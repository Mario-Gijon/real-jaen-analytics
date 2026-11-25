"""Enforce a single queued or running update job.

Revision ID: pg0004
Revises: pg0003
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'pg0004'
down_revision: Union[str, Sequence[str], None] = 'pg0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index('uq_sports_data_update_job_one_active', table_name='sports_data_update_job')
    op.execute(
        "CREATE UNIQUE INDEX uq_sports_data_update_job_one_active "
        "ON sports_data_update_job ((1)) WHERE status IN ('queued', 'running')"
    )


def downgrade() -> None:
    op.drop_index('uq_sports_data_update_job_one_active', table_name='sports_data_update_job')
    op.create_index(
        'uq_sports_data_update_job_one_active', 'sports_data_update_job', ['status'], unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
