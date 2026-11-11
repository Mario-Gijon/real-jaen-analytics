"""Retira team_match_stat e incident (fase 4, docs/fase4-sincronizacion.md 5): ninguna lectura las usa, no salen de las
reglas del importador ni de la API (las llenaba un analizador propio de la persistencia, que se retira) y retirarlas
reduce complejidad. Los originales de las secciones statistics e incidents siguen completos en `content`.

downgrade: las recrea vacías con su definición de 0001. No se rellenan: el código que las llenaba ya no existe.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-25
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0003'
down_revision: Union[str, Sequence[str], None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('team_match_stat', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_team_match_stat_key'))
    op.drop_table('team_match_stat')
    op.drop_table('incident')


def downgrade() -> None:
    seleccion = ['section_selection.match_id', 'section_selection.key', 'section_selection.sub', 'section_selection.observation_id']
    op.create_table('team_match_stat',
    sa.Column('match_id', sa.Integer(), nullable=False),
    sa.Column('section_key', sa.String(), server_default=sa.text("'statistics'"), nullable=False),
    sa.Column('section_sub', sa.String(), server_default=sa.text("''"), nullable=False),
    sa.Column('observation_id', sa.Integer(), nullable=False),
    sa.Column('period_index', sa.Integer(), nullable=False),
    sa.Column('group_index', sa.Integer(), nullable=False),
    sa.Column('item_index', sa.Integer(), nullable=False),
    sa.Column('period', sa.String(), nullable=True),
    sa.Column('group_name', sa.String(), nullable=True),
    sa.Column('key', sa.String(), nullable=True),
    sa.Column('statistics_type', sa.String(), nullable=True),
    sa.Column('home_value', sa.Text(), nullable=True),
    sa.Column('away_value', sa.Text(), nullable=True),
    sa.Column('home_total', sa.Text(), nullable=True),
    sa.Column('away_total', sa.Text(), nullable=True),
    sa.Column('rule_set_id', sa.Integer(), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=False),
    sa.CheckConstraint("section_key = 'statistics'", name=op.f('ck_team_match_stat_section_key')),
    sa.CheckConstraint('home_value IS NULL OR json_valid(home_value)', name=op.f('ck_team_match_stat_home_value_json')),
    sa.CheckConstraint('away_value IS NULL OR json_valid(away_value)', name=op.f('ck_team_match_stat_away_value_json')),
    sa.CheckConstraint('home_total IS NULL OR json_valid(home_total)', name=op.f('ck_team_match_stat_home_total_json')),
    sa.CheckConstraint('away_total IS NULL OR json_valid(away_total)', name=op.f('ck_team_match_stat_away_total_json')),
    sa.ForeignKeyConstraint(['match_id', 'section_key', 'section_sub', 'observation_id'], seleccion, name='fk_team_match_stat_selection', initially='DEFERRED', deferrable=True),
    sa.ForeignKeyConstraint(['match_id'], ['match.id'], name=op.f('fk_team_match_stat_match_id_match')),
    sa.ForeignKeyConstraint(['rule_set_id'], ['rule_set.id'], name=op.f('fk_team_match_stat_rule_set_id_rule_set')),
    sa.ForeignKeyConstraint(['run_id'], ['import_run.id'], name=op.f('fk_team_match_stat_run_id_import_run')),
    sa.PrimaryKeyConstraint('match_id', 'observation_id', 'period_index', 'group_index', 'item_index', name=op.f('pk_team_match_stat'))
    )
    with op.batch_alter_table('team_match_stat', schema=None) as batch_op:
        batch_op.create_index('ix_team_match_stat_key', ['key'], unique=False)
    op.create_table('incident',
    sa.Column('match_id', sa.Integer(), nullable=False),
    sa.Column('section_key', sa.String(), server_default=sa.text("'incidents'"), nullable=False),
    sa.Column('section_sub', sa.String(), server_default=sa.text("''"), nullable=False),
    sa.Column('observation_id', sa.Integer(), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('incident_type', sa.String(), nullable=True),
    sa.Column('incident_class', sa.String(), nullable=True),
    sa.Column('is_home', sa.Text(), nullable=True),
    sa.Column('time', sa.Text(), nullable=True),
    sa.Column('added_time', sa.Text(), nullable=True),
    sa.Column('external_player_id', sa.String(), nullable=True),
    sa.Column('external_assist1_id', sa.String(), nullable=True),
    sa.Column('external_player_in_id', sa.String(), nullable=True),
    sa.Column('external_player_out_id', sa.String(), nullable=True),
    sa.Column('player_id', sa.Integer(), nullable=True),
    sa.Column('assist1_player_id', sa.Integer(), nullable=True),
    sa.Column('player_in_id', sa.Integer(), nullable=True),
    sa.Column('player_out_id', sa.Integer(), nullable=True),
    sa.Column('rule_set_id', sa.Integer(), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=False),
    sa.CheckConstraint("section_key = 'incidents'", name=op.f('ck_incident_section_key')),
    sa.CheckConstraint('seq >= 0', name=op.f('ck_incident_seq')),
    sa.CheckConstraint('is_home IS NULL OR json_valid(is_home)', name=op.f('ck_incident_is_home_json')),
    sa.CheckConstraint('time IS NULL OR json_valid(time)', name=op.f('ck_incident_time_json')),
    sa.CheckConstraint('added_time IS NULL OR json_valid(added_time)', name=op.f('ck_incident_added_time_json')),
    sa.ForeignKeyConstraint(['match_id', 'section_key', 'section_sub', 'observation_id'], seleccion, name='fk_incident_selection', initially='DEFERRED', deferrable=True),
    sa.ForeignKeyConstraint(['match_id'], ['match.id'], name=op.f('fk_incident_match_id_match')),
    sa.ForeignKeyConstraint(['player_id'], ['player.id'], name=op.f('fk_incident_player_id_player')),
    sa.ForeignKeyConstraint(['assist1_player_id'], ['player.id'], name=op.f('fk_incident_assist1_player_id_player')),
    sa.ForeignKeyConstraint(['player_in_id'], ['player.id'], name=op.f('fk_incident_player_in_id_player')),
    sa.ForeignKeyConstraint(['player_out_id'], ['player.id'], name=op.f('fk_incident_player_out_id_player')),
    sa.ForeignKeyConstraint(['rule_set_id'], ['rule_set.id'], name=op.f('fk_incident_rule_set_id_rule_set')),
    sa.ForeignKeyConstraint(['run_id'], ['import_run.id'], name=op.f('fk_incident_run_id_import_run')),
    sa.PrimaryKeyConstraint('match_id', 'observation_id', 'seq', name=op.f('pk_incident'))
    )
