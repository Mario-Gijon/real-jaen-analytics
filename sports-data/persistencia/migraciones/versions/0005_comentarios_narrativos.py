"""Capturas inmutables de comentarios narrativos, aisladas del modelo deportivo.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-03
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0005'
down_revision: Union[str, Sequence[str], None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'narrative_comment_capture',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('match_id', sa.Integer(), nullable=False),
        sa.Column('provider', sa.String(), nullable=False),
        sa.Column('source_event_id', sa.String(), nullable=False),
        sa.Column('captured_at', sa.String(), nullable=False),
        sa.Column('http_status', sa.Integer(), nullable=False),
        sa.Column('availability', sa.String(), nullable=False),
        sa.Column('comment_count', sa.Integer(), nullable=False),
        sa.Column('response_sha256', sa.String(length=64), nullable=False),
        sa.Column('response_json', sa.Text(), nullable=False),
        sa.CheckConstraint("provider = 'sofascore'", name=op.f('ck_narrative_comment_capture_provider')),
        sa.CheckConstraint("http_status = 200", name=op.f('ck_narrative_comment_capture_http_status')),
        sa.CheckConstraint("availability IN ('available', 'empty')", name=op.f('ck_narrative_comment_capture_availability')),
        sa.CheckConstraint('comment_count >= 0', name=op.f('ck_narrative_comment_capture_comment_count')),
        sa.CheckConstraint("(availability = 'empty') = (comment_count = 0)", name=op.f('ck_narrative_comment_capture_empty')),
        sa.CheckConstraint("length(response_sha256) = 64 AND response_sha256 NOT GLOB '*[^0-9a-f]*'",
                           name=op.f('ck_narrative_comment_capture_response_sha256')),
        sa.CheckConstraint('json_valid(response_json)', name=op.f('ck_narrative_comment_capture_response_json_json')),
        sa.ForeignKeyConstraint(['match_id'], ['match.id'], name=op.f('fk_narrative_comment_capture_match_id_match')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_narrative_comment_capture')),
        sa.UniqueConstraint('id', 'match_id', name=op.f('uq_narrative_comment_capture_id_match_id')),
        sa.UniqueConstraint('match_id', 'response_sha256', name=op.f('uq_narrative_comment_capture_match_id_response_sha256')),
    )
    op.create_index('ix_narrative_comment_capture_match_id', 'narrative_comment_capture', ['match_id'], unique=False)

    op.create_table(
        'narrative_comment',
        sa.Column('capture_id', sa.Integer(), nullable=False),
        sa.Column('source_comment_id', sa.String(), nullable=False),
        sa.Column('source_position', sa.Integer(), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('type', sa.String(), nullable=False),
        sa.Column('minute', sa.Integer(), nullable=True),
        sa.Column('period_name', sa.String(), nullable=True),
        sa.Column('is_home', sa.Boolean(create_constraint=True, name='is_home'), nullable=True),
        sa.Column('home_score', sa.Integer(), nullable=True),
        sa.Column('away_score', sa.Integer(), nullable=True),
        sa.Column('incident_id', sa.String(), nullable=True),
        sa.Column('participants_json', sa.Text(), nullable=False),
        sa.Column('context_json', sa.Text(), nullable=False),
        sa.CheckConstraint('source_position >= 0', name=op.f('ck_narrative_comment_source_position')),
        sa.CheckConstraint("length(trim(text)) > 0", name=op.f('ck_narrative_comment_text')),
        sa.CheckConstraint("length(trim(type)) > 0", name=op.f('ck_narrative_comment_type')),
        sa.CheckConstraint('minute IS NULL OR minute >= 0', name=op.f('ck_narrative_comment_minute')),
        sa.CheckConstraint('home_score IS NULL OR home_score >= 0', name=op.f('ck_narrative_comment_home_score')),
        sa.CheckConstraint('away_score IS NULL OR away_score >= 0', name=op.f('ck_narrative_comment_away_score')),
        sa.CheckConstraint('json_valid(participants_json)', name=op.f('ck_narrative_comment_participants_json_json')),
        sa.CheckConstraint('json_valid(context_json)', name=op.f('ck_narrative_comment_context_json_json')),
        sa.ForeignKeyConstraint(['capture_id'], ['narrative_comment_capture.id'],
                                name=op.f('fk_narrative_comment_capture_id_narrative_comment_capture')),
        sa.PrimaryKeyConstraint('capture_id', 'source_comment_id', name=op.f('pk_narrative_comment')),
        sa.UniqueConstraint('capture_id', 'source_position', name=op.f('uq_narrative_comment_capture_id_source_position')),
    )
    op.create_index('ix_narrative_comment_order', 'narrative_comment', ['capture_id', 'source_position'], unique=False)

    op.create_table(
        'narrative_comment_selection',
        sa.Column('match_id', sa.Integer(), nullable=False),
        sa.Column('capture_id', sa.Integer(), nullable=True),
        sa.Column('last_attempt_state', sa.String(), nullable=False),
        sa.Column('last_attempt_at', sa.String(), nullable=False),
        sa.Column('last_http_status', sa.Integer(), nullable=True),
        sa.Column('last_error_code', sa.String(), nullable=True),
        sa.CheckConstraint("last_attempt_state IN ('available', 'empty', 'not_collected', 'error')",
                           name=op.f('ck_narrative_comment_selection_last_attempt_state')),
        sa.CheckConstraint('last_http_status IS NULL OR last_http_status BETWEEN 100 AND 599',
                           name=op.f('ck_narrative_comment_selection_last_http_status')),
        sa.CheckConstraint("last_error_code IS NULL OR length(trim(last_error_code)) > 0",
                           name=op.f('ck_narrative_comment_selection_last_error_code')),
        sa.ForeignKeyConstraint(['capture_id', 'match_id'],
                                ['narrative_comment_capture.id', 'narrative_comment_capture.match_id'],
                                name=op.f('fk_narrative_comment_selection_capture_id_match_id_narrative_comment_capture')),
        sa.ForeignKeyConstraint(['match_id'], ['match.id'], name=op.f('fk_narrative_comment_selection_match_id_match')),
        sa.PrimaryKeyConstraint('match_id', name=op.f('pk_narrative_comment_selection')),
    )
    op.create_index('ix_narrative_comment_selection_capture_id', 'narrative_comment_selection', ['capture_id'], unique=False)

    op.execute("""CREATE TRIGGER narrative_comment_capture_immutable_update
        BEFORE UPDATE ON narrative_comment_capture
        BEGIN SELECT RAISE(ABORT, 'narrative comment captures are immutable'); END""")
    op.execute("""CREATE TRIGGER narrative_comment_capture_immutable_delete
        BEFORE DELETE ON narrative_comment_capture
        BEGIN SELECT RAISE(ABORT, 'narrative comment captures are immutable'); END""")
    op.execute("""CREATE TRIGGER narrative_comment_immutable_update
        BEFORE UPDATE ON narrative_comment
        BEGIN SELECT RAISE(ABORT, 'narrative comments are immutable'); END""")
    op.execute("""CREATE TRIGGER narrative_comment_immutable_delete
        BEFORE DELETE ON narrative_comment
        BEGIN SELECT RAISE(ABORT, 'narrative comments are immutable'); END""")


def downgrade() -> None:
    op.execute('DROP TRIGGER IF EXISTS narrative_comment_immutable_delete')
    op.execute('DROP TRIGGER IF EXISTS narrative_comment_immutable_update')
    op.execute('DROP TRIGGER IF EXISTS narrative_comment_capture_immutable_delete')
    op.execute('DROP TRIGGER IF EXISTS narrative_comment_capture_immutable_update')
    op.drop_index('ix_narrative_comment_selection_capture_id', table_name='narrative_comment_selection')
    op.drop_table('narrative_comment_selection')
    op.drop_index('ix_narrative_comment_order', table_name='narrative_comment')
    op.drop_table('narrative_comment')
    op.drop_index('ix_narrative_comment_capture_match_id', table_name='narrative_comment_capture')
    op.drop_table('narrative_comment_capture')
