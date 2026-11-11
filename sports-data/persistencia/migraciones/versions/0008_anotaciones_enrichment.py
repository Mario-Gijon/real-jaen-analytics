"""Anotaciones versionadas de comentarios narrativos.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0008'
down_revision: Union[str, Sequence[str], None] = '0007'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'narrative_comment_enrichment',
        sa.Column('capture_id', sa.Integer(), nullable=False),
        sa.Column('source_comment_id', sa.String(), nullable=False),
        sa.Column('enrichment_version', sa.String(), nullable=False),
        sa.Column('category', sa.String(), nullable=True),
        sa.Column('attributes_json', sa.Text(), nullable=False),
        sa.CheckConstraint('length(trim(enrichment_version)) > 0',
                           name=op.f('ck_narrative_comment_enrichment_enrichment_version')),
        sa.CheckConstraint('category IS NULL OR length(trim(category)) > 0',
                           name=op.f('ck_narrative_comment_enrichment_category')),
        sa.CheckConstraint("json_valid(attributes_json) AND json_type(attributes_json) = 'object'",
                           name=op.f('ck_narrative_comment_enrichment_attributes_object_json')),
        sa.ForeignKeyConstraint(['capture_id', 'source_comment_id'],
                                ['narrative_comment.capture_id', 'narrative_comment.source_comment_id'],
                                name=op.f('fk_narrative_comment_enrichment_capture_id_source_comment_id_narrative_comment')),
        sa.PrimaryKeyConstraint('capture_id', 'source_comment_id', 'enrichment_version',
                                name=op.f('pk_narrative_comment_enrichment')),
    )
    op.create_index('ix_narrative_comment_enrichment_category_version', 'narrative_comment_enrichment',
                    ['category', 'enrichment_version'], unique=False)
    op.create_index('ix_narrative_comment_enrichment_version', 'narrative_comment_enrichment',
                    ['enrichment_version'], unique=False)

    op.create_table(
        'narrative_comment_enrichment_participant',
        sa.Column('capture_id', sa.Integer(), nullable=False),
        sa.Column('source_comment_id', sa.String(), nullable=False),
        sa.Column('enrichment_version', sa.String(), nullable=False),
        sa.Column('participant_position', sa.Integer(), nullable=False),
        sa.Column('source_role', sa.String(), nullable=False),
        sa.Column('role', sa.String(), nullable=True),
        sa.Column('source_id_json', sa.Text(), nullable=True),
        sa.Column('normalizer_role', sa.String(), nullable=True),
        sa.CheckConstraint('participant_position >= 0',
                           name=op.f('ck_narrative_comment_enrichment_participant_participant_position')),
        sa.CheckConstraint('length(trim(source_role)) > 0',
                           name=op.f('ck_narrative_comment_enrichment_participant_source_role')),
        sa.CheckConstraint('role IS NULL OR length(trim(role)) > 0',
                           name=op.f('ck_narrative_comment_enrichment_participant_role')),
        sa.CheckConstraint('normalizer_role IS NULL OR length(trim(normalizer_role)) > 0',
                           name=op.f('ck_narrative_comment_enrichment_participant_normalizer_role')),
        sa.CheckConstraint("source_id_json IS NULL OR (json_valid(source_id_json) AND "
                           "json_type(source_id_json) IN ('integer', 'text'))",
                           name=op.f('ck_narrative_comment_enrichment_participant_source_id_json')),
        sa.ForeignKeyConstraint(['capture_id', 'source_comment_id', 'enrichment_version'],
                                ['narrative_comment_enrichment.capture_id',
                                 'narrative_comment_enrichment.source_comment_id',
                                 'narrative_comment_enrichment.enrichment_version'],
                                name=op.f('fk_narrative_comment_enrichment_participant_capture_id_source_comment_id_enrichment_version_narrative_comment_enrichment')),
        sa.PrimaryKeyConstraint('capture_id', 'source_comment_id', 'enrichment_version', 'participant_position',
                                name=op.f('pk_narrative_comment_enrichment_participant')),
        sa.UniqueConstraint('capture_id', 'source_comment_id', 'enrichment_version', 'source_role',
                            name=op.f('uq_narrative_comment_enrichment_participant_capture_id_source_comment_id_enrichment_version_source_role')),
    )
    op.create_index('ix_narrative_comment_enrichment_participant_player_version',
                    'narrative_comment_enrichment_participant', ['source_id_json', 'enrichment_version'],
                    unique=False)
    op.create_index('ix_narrative_comment_enrichment_participant_role_version',
                    'narrative_comment_enrichment_participant', ['role', 'enrichment_version'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_narrative_comment_enrichment_participant_role_version',
                  table_name='narrative_comment_enrichment_participant')
    op.drop_index('ix_narrative_comment_enrichment_participant_player_version',
                  table_name='narrative_comment_enrichment_participant')
    op.drop_table('narrative_comment_enrichment_participant')
    op.drop_index('ix_narrative_comment_enrichment_version', table_name='narrative_comment_enrichment')
    op.drop_index('ix_narrative_comment_enrichment_category_version', table_name='narrative_comment_enrichment')
    op.drop_table('narrative_comment_enrichment')
