"""Shared PostgreSQL update jobs, idempotency, leases and fencing.

Revision ID: pg0003
Revises: pg0002
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'pg0003'
down_revision: Union[str, Sequence[str], None] = 'pg0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'sports_data_update_state',
        sa.Column('singleton_id', sa.SmallInteger(), nullable=False),
        sa.Column('fencing_token', sa.BigInteger(), server_default='0', nullable=False),
        sa.CheckConstraint('singleton_id = 1', name='ck_sports_data_update_state_singleton'),
        sa.CheckConstraint('fencing_token >= 0', name='ck_sports_data_update_state_fencing_token'),
        sa.PrimaryKeyConstraint('singleton_id', name='pk_sports_data_update_state'),
    )
    op.execute("INSERT INTO sports_data_update_state (singleton_id, fencing_token) VALUES (1, 0)")

    op.create_table(
        'sports_data_update_job',
        sa.Column('job_id', sa.Uuid(), nullable=False),
        sa.Column('status', sa.String(length=24), nullable=False),
        sa.Column('phase', sa.String(length=24), nullable=False),
        sa.Column('request_id', sa.Uuid(), nullable=False),
        sa.Column('acquisition_id', sa.Uuid(), nullable=True),
        sa.Column('publication_id', sa.Uuid(), nullable=True),
        sa.Column('last_publication_id', sa.Uuid(), nullable=True),
        sa.Column('owner_id', sa.Uuid(), nullable=True),
        sa.Column('fencing_token', sa.BigInteger(), nullable=False),
        sa.Column('lease_expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('heartbeat_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('cancel_requested', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('published', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('requested_at', sa.DateTime(timezone=True), server_default=sa.text('clock_timestamp()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('clock_timestamp()'), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('data', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'cancelled', 'interrupted')",
            name='ck_sports_data_update_job_status',
        ),
        sa.CheckConstraint(
            "phase IN ('queued', 'acquiring', 'verifying', 'publishing', 'finished')",
            name='ck_sports_data_update_job_phase',
        ),
        sa.CheckConstraint('fencing_token > 0', name='ck_sports_data_update_job_fencing_token'),
        sa.CheckConstraint(
            "status NOT IN ('queued', 'running') OR (owner_id IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name='ck_sports_data_update_job_active_owner',
        ),
        sa.PrimaryKeyConstraint('job_id', name='pk_sports_data_update_job'),
    )
    op.create_index(
        'uq_sports_data_update_job_one_active', 'sports_data_update_job', ['status'], unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.create_index(
        'ix_sports_data_update_job_owner_lease', 'sports_data_update_job', ['owner_id', 'lease_expires_at'],
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.create_index(
        'ix_sports_data_update_job_last_finished', 'sports_data_update_job', ['requested_at'],
        postgresql_where=sa.text("status NOT IN ('queued', 'running')"),
    )
    op.create_index(
        'ix_sports_data_update_job_last_published', 'sports_data_update_job', ['published_at'],
        postgresql_where=sa.text('published'),
    )

    op.create_table(
        'sports_data_update_idempotency',
        sa.Column('idempotency_key', sa.Uuid(), nullable=False),
        sa.Column('job_id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('clock_timestamp()'), nullable=False),
        sa.ForeignKeyConstraint(
            ['job_id'], ['sports_data_update_job.job_id'], ondelete='RESTRICT',
            name='fk_sports_data_update_idempotency_job',
        ),
        sa.PrimaryKeyConstraint('idempotency_key', name='pk_sports_data_update_idempotency'),
    )
    op.create_index('ix_sports_data_update_idempotency_job', 'sports_data_update_idempotency', ['job_id'])

    op.execute('REVOKE ALL ON sports_data_update_state, sports_data_update_job, sports_data_update_idempotency FROM PUBLIC')
    # pg0001 grants application rights by default; narrow the new control tables explicitly.
    op.execute('REVOKE ALL ON sports_data_update_state, sports_data_update_job, sports_data_update_idempotency FROM sports_data_app')
    op.execute('GRANT SELECT, UPDATE (fencing_token) ON sports_data_update_state TO sports_data_app')
    op.execute('GRANT SELECT, INSERT, UPDATE ON sports_data_update_job TO sports_data_app')
    op.execute('GRANT SELECT, INSERT ON sports_data_update_idempotency TO sports_data_app')


def downgrade() -> None:
    op.execute('REVOKE ALL ON sports_data_update_state, sports_data_update_job, sports_data_update_idempotency FROM sports_data_app')
    op.drop_table('sports_data_update_idempotency')
    op.drop_table('sports_data_update_job')
    op.drop_table('sports_data_update_state')
