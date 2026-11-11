"""Vigencia de convocatorias (fase 4, docs/fase4-sincronizacion.md 2.2).

- call_up.status (active | retired) y retired_in_run: una convocatoria que la alineación seleccionada deja de incluir
  se retira sin borrarse ni reutilizar su id; si reaparece, se reactiva la misma fila.
- UNIQUE (match_id, side, external_player_id): la identidad de la convocatoria y la clave de sincronización.
- La clave foránea compuesta a la selección (fk_call_up_selection) impediría conservar una retirada con su última
  alineación: se sustituye por una a observation (la convocatoria sale de una alineación de su partido). Que toda
  convocatoria activa salga de la alineación seleccionada se comprueba en código antes de confirmar cada escritura.
- call_up_change: cada retirada o reactivación, con su ejecución y la alineación que la provoca.
- Vista call_up_vigente: la única puerta de lectura de convocatorias.

Las filas existentes quedan `active`. downgrade: se detiene si hay convocatorias retiradas (no se pierden).

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-25
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0004'
down_revision: Union[str, Sequence[str], None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

VISTA = "CREATE VIEW call_up_vigente AS SELECT * FROM call_up WHERE status = 'active'"


def upgrade() -> None:
    with op.batch_alter_table('call_up', schema=None) as batch_op:
        # Al recrear la tabla, el modo batch reescribiría el valor por defecto como ('') : se fija tal cual.
        batch_op.alter_column('lineup_sub', existing_type=sa.String(), existing_nullable=False, server_default=sa.text("''"))
        batch_op.add_column(sa.Column('status', sa.String(), server_default=sa.text("'active'"), nullable=False))
        batch_op.add_column(sa.Column('retired_in_run', sa.Integer(), nullable=True))
        batch_op.drop_constraint('fk_call_up_selection', type_='foreignkey')
        batch_op.create_foreign_key('fk_call_up_lineup_observation', 'observation',
                                    ['lineup_observation_id', 'match_id', 'lineup_key', 'lineup_sub'], ['id', 'match_id', 'key', 'sub'])
        batch_op.create_foreign_key(batch_op.f('fk_call_up_retired_in_run_import_run'), 'import_run', ['retired_in_run'], ['id'])
        batch_op.create_unique_constraint(batch_op.f('uq_call_up_match_id_side_external_player_id'),
                                          ['match_id', 'side', 'external_player_id'])
        batch_op.create_check_constraint(batch_op.f('ck_call_up_status'), "status IN ('active', 'retired')")
        batch_op.create_check_constraint(batch_op.f('ck_call_up_retired'), "(status = 'retired') = (retired_in_run IS NOT NULL)")
    op.create_table('call_up_change',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('call_up_id', sa.Integer(), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=False),
    sa.Column('change', sa.String(), nullable=False),
    sa.Column('lineup_observation_id', sa.Integer(), nullable=True),
    sa.CheckConstraint("change IN ('retired', 'reactivated')", name=op.f('ck_call_up_change_change')),
    sa.ForeignKeyConstraint(['call_up_id'], ['call_up.id'], name=op.f('fk_call_up_change_call_up_id_call_up')),
    sa.ForeignKeyConstraint(['lineup_observation_id'], ['observation.id'], name=op.f('fk_call_up_change_lineup_observation_id_observation')),
    sa.ForeignKeyConstraint(['run_id'], ['import_run.id'], name=op.f('fk_call_up_change_run_id_import_run')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_call_up_change'))
    )
    with op.batch_alter_table('call_up_change', schema=None) as batch_op:
        batch_op.create_index('ix_call_up_change_call_up_id', ['call_up_id'], unique=False)
    op.execute(VISTA)


def downgrade() -> None:
    retiradas = op.get_bind().exec_driver_sql("SELECT count(*) FROM call_up WHERE status = 'retired'").scalar()
    if retiradas:
        raise RuntimeError(f'Hay {retiradas} convocatorias retiradas: volver a 0003 las perdería. Restaure una copia anterior.')
    op.execute('DROP VIEW IF EXISTS call_up_vigente')
    with op.batch_alter_table('call_up_change', schema=None) as batch_op:
        batch_op.drop_index('ix_call_up_change_call_up_id')
    op.drop_table('call_up_change')
    with op.batch_alter_table('call_up', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('ck_call_up_retired'), type_='check')
        batch_op.drop_constraint(batch_op.f('ck_call_up_status'), type_='check')
        batch_op.drop_constraint(batch_op.f('uq_call_up_match_id_side_external_player_id'), type_='unique')
        batch_op.drop_constraint(batch_op.f('fk_call_up_retired_in_run_import_run'), type_='foreignkey')
        batch_op.drop_constraint('fk_call_up_lineup_observation', type_='foreignkey')
        batch_op.create_foreign_key('fk_call_up_selection', 'section_selection',
                                    ['match_id', 'lineup_key', 'lineup_sub', 'lineup_observation_id'],
                                    ['match_id', 'key', 'sub', 'observation_id'], initially='DEFERRED', deferrable=True)
        batch_op.drop_column('retired_in_run')
        batch_op.drop_column('status')
