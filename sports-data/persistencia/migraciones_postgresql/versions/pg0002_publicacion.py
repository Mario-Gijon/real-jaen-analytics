"""Durable PostgreSQL sports publication state.

Revision ID: pg0002
Revises: pg0001
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'pg0002'
down_revision: Union[str, Sequence[str], None] = 'pg0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'sports_data_publication',
        sa.Column('publication_id', sa.Uuid(), nullable=False),
        sa.Column('source_generation', sa.Uuid(), nullable=False),
        sa.Column('result_generation', sa.Uuid(), nullable=False),
        sa.Column('input_sha256', sa.String(length=64), nullable=False),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('run_status', sa.String(length=24), nullable=False),
        sa.Column('result_json', sa.JSON(), nullable=False),
        sa.CheckConstraint("length(input_sha256) = 64 AND input_sha256 !~ '[^0-9a-f]'",
                           name='ck_sports_data_publication_input_sha256'),
        sa.CheckConstraint("run_status IN ('committed', 'no_changes')",
                           name='ck_sports_data_publication_run_status'),
        sa.PrimaryKeyConstraint('publication_id', name='pk_sports_data_publication'),
        sa.UniqueConstraint('result_generation', name='uq_sports_data_publication_result_generation'),
    )
    op.create_table(
        'sports_data_publication_state',
        sa.Column('singleton_id', sa.SmallInteger(), nullable=False),
        sa.Column('generation_id', sa.Uuid(), nullable=False),
        sa.Column('generation_number', sa.BigInteger(), nullable=False),
        sa.Column('code_sha256', sa.String(length=64), nullable=True),
        sa.Column('scope_sha256', sa.String(length=64), nullable=True),
        sa.Column('last_publication_id', sa.Uuid(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.CheckConstraint('singleton_id = 1', name='ck_sports_data_publication_state_singleton'),
        sa.CheckConstraint('generation_number >= 0', name='ck_sports_data_publication_state_generation_number'),
        sa.CheckConstraint("code_sha256 IS NULL OR (length(code_sha256) = 64 AND code_sha256 !~ '[^0-9a-f]')",
                           name='ck_sports_data_publication_state_code_sha256'),
        sa.CheckConstraint("scope_sha256 IS NULL OR (length(scope_sha256) = 64 AND scope_sha256 !~ '[^0-9a-f]')",
                           name='ck_sports_data_publication_state_scope_sha256'),
        sa.ForeignKeyConstraint(['last_publication_id'], ['sports_data_publication.publication_id'],
                                name='fk_sports_data_publication_state_last_publication'),
        sa.PrimaryKeyConstraint('singleton_id', name='pk_sports_data_publication_state'),
    )
    op.execute("""
        INSERT INTO sports_data_publication_state
            (singleton_id, generation_id, generation_number, updated_at)
        VALUES (1, '00000000-0000-4000-8000-000000000001', 0, now())
    """)
    op.execute('REVOKE ALL ON sports_data_publication, sports_data_publication_state FROM PUBLIC')
    # pg0001's default table privileges are intentionally broader for the business schema;
    # narrow these two control tables back down to their explicit execution-role needs.
    op.execute('REVOKE ALL ON sports_data_publication, sports_data_publication_state FROM sports_data_app')
    op.execute('GRANT SELECT, INSERT ON sports_data_publication TO sports_data_app')
    op.execute('GRANT SELECT, UPDATE ON sports_data_publication_state TO sports_data_app')


def downgrade() -> None:
    op.execute('REVOKE ALL ON sports_data_publication, sports_data_publication_state FROM sports_data_app')
    op.drop_table('sports_data_publication_state')
    op.drop_table('sports_data_publication')
