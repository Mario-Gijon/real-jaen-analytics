"""Orden de los motivos de discrepancias y de los problemas de versión de un partido (fase 2): forma parte de lo
publicado (`reasons`, `version_issues`) y la revisión 0001 no lo guardaba. Las tablas estaban vacías: ninguna base
cargaba datos antes de esta revisión, así que la columna se añade sin valor por defecto (nunca se inventa un orden).

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0002'
down_revision: Union[str, Sequence[str], None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLAS = (('discrepancy_reason', 'discrepancy_id'), ('match_version_issue', 'match_id'))


def upgrade() -> None:
    for tabla, padre in TABLAS:
        with op.batch_alter_table(tabla, schema=None) as batch_op:
            batch_op.add_column(sa.Column('ordinal', sa.Integer(), nullable=False))
            batch_op.create_unique_constraint(batch_op.f(f'uq_{tabla}_{padre}_ordinal'), [padre, 'ordinal'])
            batch_op.create_check_constraint(batch_op.f(f'ck_{tabla}_ordinal'), 'ordinal >= 0')


def downgrade() -> None:
    for tabla, padre in reversed(TABLAS):
        with op.batch_alter_table(tabla, schema=None) as batch_op:
            batch_op.drop_constraint(batch_op.f(f'ck_{tabla}_ordinal'), type_='check')
            batch_op.drop_constraint(batch_op.f(f'uq_{tabla}_{padre}_ordinal'), type_='unique')
            batch_op.drop_column('ordinal')
