"""Procedencia del endpoint y del transporte de comentarios narrativos.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-03
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0006'
down_revision: Union[str, Sequence[str], None] = '0005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('narrative_comment_capture', sa.Column('source_path', sa.String(), nullable=True))
    op.add_column('narrative_comment_capture', sa.Column('transport_report_json', sa.Text(), nullable=True))
    op.add_column('narrative_comment_selection', sa.Column('last_successful_source_path', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('narrative_comment_selection', 'last_successful_source_path')
    op.drop_column('narrative_comment_capture', 'transport_report_json')
    op.drop_column('narrative_comment_capture', 'source_path')
