"""Vincula la selección vigente con la captura confirmada desde el endpoint español.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-03
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0007'
down_revision: Union[str, Sequence[str], None] = '0006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('narrative_comment_selection') as batch_op:
        batch_op.add_column(sa.Column('selected_spanish_sha256', sa.String(length=64), nullable=True))
        batch_op.create_check_constraint(
            batch_op.f('ck_narrative_comment_selection_selected_spanish_sha256'),
            "selected_spanish_sha256 IS NULL OR "
            "(length(selected_spanish_sha256) = 64 AND selected_spanish_sha256 NOT GLOB '*[^0-9a-f]*')",
        )


def downgrade() -> None:
    with op.batch_alter_table('narrative_comment_selection') as batch_op:
        batch_op.drop_constraint(batch_op.f('ck_narrative_comment_selection_selected_spanish_sha256'), type_='check')
        batch_op.drop_column('selected_spanish_sha256')
