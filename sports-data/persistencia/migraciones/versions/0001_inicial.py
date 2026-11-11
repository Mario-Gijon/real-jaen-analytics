"""Esquema inicial de la persistencia (docs/persistencia-sqlite.md, sección 2): identidad, evidencia, decisiones
locales y estado operativo. Solo esquema: no carga datos.

Revision ID: 0001
Revises:
Create Date: 2026-10-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0001'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('content',
    sa.Column('hash', sa.String(length=64), nullable=False),
    sa.Column('payload_text', sa.Text(), nullable=False),
    sa.Column('text_sha256', sa.String(length=64), nullable=False),
    sa.Column('bytes', sa.Integer(), nullable=False),
    sa.CheckConstraint("length(hash) = 64 AND hash NOT GLOB '*[^0-9a-f]*'", name=op.f('ck_content_hash')),
    sa.CheckConstraint("length(text_sha256) = 64 AND text_sha256 NOT GLOB '*[^0-9a-f]*'", name=op.f('ck_content_text_sha256')),
    sa.CheckConstraint('bytes >= 0', name=op.f('ck_content_bytes')),
    sa.CheckConstraint('json_valid(payload_text)', name=op.f('ck_content_payload_text_json')),
    sa.PrimaryKeyConstraint('hash', name=op.f('pk_content'))
    )
    op.create_table('import_run',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(), nullable=False),
    sa.Column('started_at', sa.String(), nullable=False),
    sa.Column('finished_at', sa.String(), nullable=True),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('tool_version', sa.String(), nullable=True),
    sa.Column('command', sa.Text(), nullable=True),
    sa.Column('rule_set_ids', sa.Text(), nullable=True),
    sa.Column('summary', sa.Text(), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.CheckConstraint("(status = 'running') = (finished_at IS NULL)", name=op.f('ck_import_run_finished')),
    sa.CheckConstraint("kind IN ('initial_load', 'import', 'rebuild', 'decisions', 'identity_decisions')", name=op.f('ck_import_run_kind')),
    sa.CheckConstraint("status <> 'failed' OR error IS NOT NULL", name=op.f('ck_import_run_failed_error')),
    sa.CheckConstraint("status IN ('running', 'committed', 'failed', 'no_changes')", name=op.f('ck_import_run_status')),
    sa.CheckConstraint('rule_set_ids IS NULL OR json_valid(rule_set_ids)', name=op.f('ck_import_run_rule_set_ids_json')),
    sa.CheckConstraint('summary IS NULL OR json_valid(summary)', name=op.f('ck_import_run_summary_json')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_import_run'))
    )
    op.create_table('rule_set',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('version', sa.String(), nullable=False),
    sa.Column('code_ref', sa.String(), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_rule_set')),
    sa.UniqueConstraint('name', 'version', name=op.f('uq_rule_set_name_version'))
    )
    op.create_table('competition',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('uid', sa.String(length=36), nullable=False),
    sa.Column('status', sa.String(), server_default=sa.text("'active'"), nullable=False),
    sa.Column('merged_into_id', sa.Integer(), nullable=True),
    sa.Column('created_in_run', sa.Integer(), nullable=False),
    sa.CheckConstraint("(status = 'merged') = (merged_into_id IS NOT NULL)", name=op.f('ck_competition_merged_into')),
    sa.CheckConstraint("status IN ('active', 'merged')", name=op.f('ck_competition_status')),
    sa.CheckConstraint('length(uid) = 36', name=op.f('ck_competition_uid')),
    sa.CheckConstraint('merged_into_id IS NULL OR merged_into_id <> id', name=op.f('ck_competition_not_self')),
    sa.ForeignKeyConstraint(['created_in_run'], ['import_run.id'], name=op.f('fk_competition_created_in_run_import_run')),
    sa.ForeignKeyConstraint(['merged_into_id'], ['competition.id'], name=op.f('fk_competition_merged_into_id_competition')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_competition')),
    sa.UniqueConstraint('uid', name=op.f('uq_competition_uid'))
    )
    op.create_table('identity_decision',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('uid', sa.String(length=36), nullable=False),
    sa.Column('kind', sa.String(), nullable=False),
    sa.Column('entity_type', sa.String(), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('decided_by', sa.String(), nullable=False),
    sa.Column('decided_on', sa.Date(), nullable=False),
    sa.Column('evidence', sa.Text(), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=False),
    sa.Column('reverts_decision_id', sa.Integer(), nullable=True),
    sa.CheckConstraint("(kind = 'revert') = (reverts_decision_id IS NOT NULL)", name=op.f('ck_identity_decision_revert')),
    sa.CheckConstraint("entity_type IN ('team', 'player', 'match', 'competition', 'season', 'stage')", name=op.f('ck_identity_decision_entity_type')),
    sa.CheckConstraint("kind IN ('merge', 'split', 'reassign_external_id', 'reject_external_id', 'revert')", name=op.f('ck_identity_decision_kind')),
    sa.CheckConstraint('length(trim(decided_by)) > 0', name=op.f('ck_identity_decision_decided_by')),
    sa.CheckConstraint('length(trim(evidence)) > 0', name=op.f('ck_identity_decision_evidence')),
    sa.CheckConstraint('length(trim(reason)) > 0', name=op.f('ck_identity_decision_reason')),
    sa.CheckConstraint('length(uid) = 36', name=op.f('ck_identity_decision_uid')),
    sa.ForeignKeyConstraint(['reverts_decision_id'], ['identity_decision.id'], name=op.f('fk_identity_decision_reverts_decision_id_identity_decision')),
    sa.ForeignKeyConstraint(['run_id'], ['import_run.id'], name=op.f('fk_identity_decision_run_id_import_run')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_identity_decision')),
    sa.UniqueConstraint('uid', name=op.f('uq_identity_decision_uid'))
    )
    op.create_table('match',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('uid', sa.String(length=36), nullable=False),
    sa.Column('status', sa.String(), server_default=sa.text("'active'"), nullable=False),
    sa.Column('merged_into_id', sa.Integer(), nullable=True),
    sa.Column('created_in_run', sa.Integer(), nullable=False),
    sa.CheckConstraint("(status = 'merged') = (merged_into_id IS NOT NULL)", name=op.f('ck_match_merged_into')),
    sa.CheckConstraint("status IN ('active', 'merged')", name=op.f('ck_match_status')),
    sa.CheckConstraint('length(uid) = 36', name=op.f('ck_match_uid')),
    sa.CheckConstraint('merged_into_id IS NULL OR merged_into_id <> id', name=op.f('ck_match_not_self')),
    sa.ForeignKeyConstraint(['created_in_run'], ['import_run.id'], name=op.f('fk_match_created_in_run_import_run')),
    sa.ForeignKeyConstraint(['merged_into_id'], ['match.id'], name=op.f('fk_match_merged_into_id_match')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_match')),
    sa.UniqueConstraint('uid', name=op.f('uq_match_uid'))
    )
    op.create_table('player',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('uid', sa.String(length=36), nullable=False),
    sa.Column('status', sa.String(), server_default=sa.text("'active'"), nullable=False),
    sa.Column('merged_into_id', sa.Integer(), nullable=True),
    sa.Column('created_in_run', sa.Integer(), nullable=False),
    sa.CheckConstraint("(status = 'merged') = (merged_into_id IS NOT NULL)", name=op.f('ck_player_merged_into')),
    sa.CheckConstraint("status IN ('active', 'merged')", name=op.f('ck_player_status')),
    sa.CheckConstraint('length(uid) = 36', name=op.f('ck_player_uid')),
    sa.CheckConstraint('merged_into_id IS NULL OR merged_into_id <> id', name=op.f('ck_player_not_self')),
    sa.ForeignKeyConstraint(['created_in_run'], ['import_run.id'], name=op.f('fk_player_created_in_run_import_run')),
    sa.ForeignKeyConstraint(['merged_into_id'], ['player.id'], name=op.f('fk_player_merged_into_id_player')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_player')),
    sa.UniqueConstraint('uid', name=op.f('uq_player_uid'))
    )
    op.create_table('source_file',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('kind', sa.String(), nullable=False),
    sa.Column('label', sa.String(), nullable=False),
    sa.Column('records', sa.Integer(), nullable=False),
    sa.Column('events', sa.Integer(), nullable=True),
    sa.Column('players', sa.Integer(), nullable=True),
    sa.Column('invalid_fetched_at', sa.Integer(), nullable=True),
    sa.Column('without_profile', sa.Integer(), nullable=True),
    sa.Column('fetched_from', sa.String(), nullable=True),
    sa.Column('fetched_to', sa.String(), nullable=True),
    sa.Column('first_run_id', sa.Integer(), nullable=False),
    sa.CheckConstraint("kind IN ('raw', 'listing', 'player_profile', 'local_decisions', 'identity_decisions')", name=op.f('ck_source_file_kind')),
    sa.CheckConstraint("length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'", name=op.f('ck_source_file_sha256')),
    sa.CheckConstraint('length(trim(label)) > 0', name=op.f('ck_source_file_label')),
    sa.CheckConstraint('records >= 0', name=op.f('ck_source_file_records')),
    sa.ForeignKeyConstraint(['first_run_id'], ['import_run.id'], name=op.f('fk_source_file_first_run_id_import_run')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_source_file')),
    sa.UniqueConstraint('label', name=op.f('uq_source_file_label')),
    sa.UniqueConstraint('sha256', name=op.f('uq_source_file_sha256'))
    )
    op.create_table('team',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('uid', sa.String(length=36), nullable=False),
    sa.Column('status', sa.String(), server_default=sa.text("'active'"), nullable=False),
    sa.Column('merged_into_id', sa.Integer(), nullable=True),
    sa.Column('created_in_run', sa.Integer(), nullable=False),
    sa.CheckConstraint("(status = 'merged') = (merged_into_id IS NOT NULL)", name=op.f('ck_team_merged_into')),
    sa.CheckConstraint("status IN ('active', 'merged')", name=op.f('ck_team_status')),
    sa.CheckConstraint('length(uid) = 36', name=op.f('ck_team_uid')),
    sa.CheckConstraint('merged_into_id IS NULL OR merged_into_id <> id', name=op.f('ck_team_not_self')),
    sa.ForeignKeyConstraint(['created_in_run'], ['import_run.id'], name=op.f('fk_team_created_in_run_import_run')),
    sa.ForeignKeyConstraint(['merged_into_id'], ['team.id'], name=op.f('fk_team_merged_into_id_team')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_team')),
    sa.UniqueConstraint('uid', name=op.f('uq_team_uid'))
    )
    op.create_table('competition_external_id',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('provider', sa.String(), nullable=False),
    sa.Column('namespace', sa.String(), server_default=sa.text("''"), nullable=False),
    sa.Column('external_id', sa.String(), nullable=False),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('entity_id', sa.Integer(), nullable=False),
    sa.Column('decision_id', sa.Integer(), nullable=True),
    sa.Column('created_in_run', sa.Integer(), nullable=False),
    sa.CheckConstraint("status = 'origin' OR decision_id IS NOT NULL", name=op.f('ck_competition_external_id_decision')),
    sa.CheckConstraint("status IN ('origin', 'verified', 'rejected')", name=op.f('ck_competition_external_id_status')),
    sa.CheckConstraint('length(trim(external_id)) > 0', name=op.f('ck_competition_external_id_external_id')),
    sa.CheckConstraint('length(trim(provider)) > 0', name=op.f('ck_competition_external_id_provider')),
    sa.ForeignKeyConstraint(['created_in_run'], ['import_run.id'], name=op.f('fk_competition_external_id_created_in_run_import_run')),
    sa.ForeignKeyConstraint(['decision_id'], ['identity_decision.id'], name=op.f('fk_competition_external_id_decision_id_identity_decision')),
    sa.ForeignKeyConstraint(['entity_id'], ['competition.id'], name=op.f('fk_competition_external_id_entity_id_competition')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_competition_external_id'))
    )
    with op.batch_alter_table('competition_external_id', schema=None) as batch_op:
        batch_op.create_index('ix_competition_external_id_entity_id', ['entity_id'], unique=False)
        batch_op.create_index('uq_competition_external_id_live', ['provider', 'namespace', 'external_id'], unique=True, sqlite_where=sa.text("status <> 'rejected'"))
        batch_op.create_index('uq_competition_external_id_origin', ['entity_id'], unique=True, sqlite_where=sa.text("status = 'origin'"))

    op.create_table('file_team_declaration',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('source_file_id', sa.Integer(), nullable=False),
    sa.Column('team_id', sa.Integer(), nullable=False),
    sa.Column('external_team_id', sa.String(), nullable=False),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('declared_in_run', sa.Integer(), nullable=False),
    sa.Column('declared_by', sa.String(), nullable=True),
    sa.Column('withdrawn_in_run', sa.Integer(), nullable=True),
    sa.Column('withdrawn_by', sa.String(), nullable=True),
    sa.Column('withdrawn_reason', sa.Text(), nullable=True),
    sa.CheckConstraint("(status = 'withdrawn') = (withdrawn_in_run IS NOT NULL AND withdrawn_by IS NOT NULL AND withdrawn_reason IS NOT NULL)", name=op.f('ck_file_team_declaration_withdrawn')),
    sa.CheckConstraint("status IN ('active', 'withdrawn')", name=op.f('ck_file_team_declaration_status')),
    sa.ForeignKeyConstraint(['declared_in_run'], ['import_run.id'], name=op.f('fk_file_team_declaration_declared_in_run_import_run')),
    sa.ForeignKeyConstraint(['source_file_id'], ['source_file.id'], name=op.f('fk_file_team_declaration_source_file_id_source_file')),
    sa.ForeignKeyConstraint(['team_id'], ['team.id'], name=op.f('fk_file_team_declaration_team_id_team')),
    sa.ForeignKeyConstraint(['withdrawn_in_run'], ['import_run.id'], name=op.f('fk_file_team_declaration_withdrawn_in_run_import_run')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_file_team_declaration'))
    )
    with op.batch_alter_table('file_team_declaration', schema=None) as batch_op:
        batch_op.create_index('uq_file_team_declaration_active', ['source_file_id', 'team_id'], unique=True, sqlite_where=sa.text("status = 'active'"))

    op.create_table('import_run_file',
    sa.Column('run_id', sa.Integer(), nullable=False),
    sa.Column('source_file_id', sa.Integer(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('presented_name', sa.String(), nullable=False),
    sa.Column('presented_path', sa.Text(), nullable=True),
    sa.Column('outcome', sa.String(), nullable=False),
    sa.CheckConstraint("outcome IN ('ingested', 'already_known')", name=op.f('ck_import_run_file_outcome')),
    sa.CheckConstraint('position >= 1', name=op.f('ck_import_run_file_position')),
    sa.ForeignKeyConstraint(['run_id'], ['import_run.id'], name=op.f('fk_import_run_file_run_id_import_run')),
    sa.ForeignKeyConstraint(['source_file_id'], ['source_file.id'], name=op.f('fk_import_run_file_source_file_id_source_file')),
    sa.PrimaryKeyConstraint('run_id', 'source_file_id', name=op.f('pk_import_run_file')),
    sa.UniqueConstraint('run_id', 'position', name=op.f('uq_import_run_file_run_id_position'))
    )
    op.create_table('local_replacement',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('replaced_match_id', sa.Integer(), nullable=False),
    sa.Column('current_match_id', sa.Integer(), nullable=False),
    sa.Column('external_replaced_id', sa.String(), nullable=False),
    sa.Column('external_current_id', sa.String(), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('decided_by', sa.String(), nullable=False),
    sa.Column('decided_on', sa.Date(), nullable=False),
    sa.Column('evidence', sa.Text(), nullable=True),
    sa.Column('source_file_id', sa.Integer(), nullable=True),
    sa.Column('created_in_run', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('reverted_in_run', sa.Integer(), nullable=True),
    sa.Column('reverted_by', sa.String(), nullable=True),
    sa.Column('reverted_on', sa.Date(), nullable=True),
    sa.Column('revert_reason', sa.Text(), nullable=True),
    sa.CheckConstraint("(status = 'reverted') = (reverted_in_run IS NOT NULL AND reverted_by IS NOT NULL AND reverted_on IS NOT NULL AND revert_reason IS NOT NULL)", name=op.f('ck_local_replacement_reverted')),
    sa.CheckConstraint("status IN ('active', 'reverted')", name=op.f('ck_local_replacement_status')),
    sa.CheckConstraint('length(trim(decided_by)) > 0', name=op.f('ck_local_replacement_decided_by')),
    sa.CheckConstraint('length(trim(reason)) > 0', name=op.f('ck_local_replacement_reason')),
    sa.CheckConstraint('replaced_match_id <> current_match_id', name=op.f('ck_local_replacement_distinct')),
    sa.ForeignKeyConstraint(['created_in_run'], ['import_run.id'], name=op.f('fk_local_replacement_created_in_run_import_run')),
    sa.ForeignKeyConstraint(['current_match_id'], ['match.id'], name=op.f('fk_local_replacement_current_match_id_match')),
    sa.ForeignKeyConstraint(['replaced_match_id'], ['match.id'], name=op.f('fk_local_replacement_replaced_match_id_match')),
    sa.ForeignKeyConstraint(['reverted_in_run'], ['import_run.id'], name=op.f('fk_local_replacement_reverted_in_run_import_run')),
    sa.ForeignKeyConstraint(['source_file_id'], ['source_file.id'], name=op.f('fk_local_replacement_source_file_id_source_file')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_local_replacement'))
    )
    with op.batch_alter_table('local_replacement', schema=None) as batch_op:
        batch_op.create_index('uq_local_replacement_active', ['replaced_match_id'], unique=True, sqlite_where=sa.text("status = 'active'"))

    op.create_table('match_external_id',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('provider', sa.String(), nullable=False),
    sa.Column('namespace', sa.String(), server_default=sa.text("''"), nullable=False),
    sa.Column('external_id', sa.String(), nullable=False),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('entity_id', sa.Integer(), nullable=False),
    sa.Column('decision_id', sa.Integer(), nullable=True),
    sa.Column('created_in_run', sa.Integer(), nullable=False),
    sa.CheckConstraint("status = 'origin' OR decision_id IS NOT NULL", name=op.f('ck_match_external_id_decision')),
    sa.CheckConstraint("status IN ('origin', 'verified', 'rejected')", name=op.f('ck_match_external_id_status')),
    sa.CheckConstraint('length(trim(external_id)) > 0', name=op.f('ck_match_external_id_external_id')),
    sa.CheckConstraint('length(trim(provider)) > 0', name=op.f('ck_match_external_id_provider')),
    sa.ForeignKeyConstraint(['created_in_run'], ['import_run.id'], name=op.f('fk_match_external_id_created_in_run_import_run')),
    sa.ForeignKeyConstraint(['decision_id'], ['identity_decision.id'], name=op.f('fk_match_external_id_decision_id_identity_decision')),
    sa.ForeignKeyConstraint(['entity_id'], ['match.id'], name=op.f('fk_match_external_id_entity_id_match')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_match_external_id'))
    )
    with op.batch_alter_table('match_external_id', schema=None) as batch_op:
        batch_op.create_index('ix_match_external_id_entity_id', ['entity_id'], unique=False)
        batch_op.create_index('uq_match_external_id_live', ['provider', 'namespace', 'external_id'], unique=True, sqlite_where=sa.text("status <> 'rejected'"))
        batch_op.create_index('uq_match_external_id_origin', ['entity_id'], unique=True, sqlite_where=sa.text("status = 'origin'"))

    op.create_table('observation',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('source_file_id', sa.Integer(), nullable=False),
    sa.Column('line', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(), nullable=False),
    sa.Column('provider', sa.String(), nullable=False),
    sa.Column('external_event_id', sa.String(), nullable=True),
    sa.Column('external_player_id', sa.String(), nullable=True),
    sa.Column('key', sa.String(), nullable=False),
    sa.Column('sub', sa.String(), server_default=sa.text("''"), nullable=False),
    sa.Column('endpoint', sa.String(), nullable=True),
    sa.Column('fetched_at_raw', sa.String(), nullable=True),
    sa.Column('fetched_instant', sa.String(), nullable=True),
    sa.Column('fetched_at_valid', sa.Boolean(create_constraint=True, name='fetched_at_valid'), nullable=False),
    sa.Column('content_hash', sa.String(length=64), nullable=False),
    sa.Column('envelope_extra', sa.Text(), nullable=True),
    sa.Column('match_id', sa.Integer(), nullable=True),
    sa.Column('player_id', sa.Integer(), nullable=True),
    sa.CheckConstraint("(kind = 'listing') = (key = 'listing')", name=op.f('ck_observation_listing')),
    sa.CheckConstraint("kind <> 'player_profile' OR (external_player_id IS NOT NULL AND key = 'player_profile')", name=op.f('ck_observation_profile')),
    sa.CheckConstraint("kind = 'player_profile' OR external_event_id IS NOT NULL", name=op.f('ck_observation_event_id')),
    sa.CheckConstraint("kind IN ('section', 'listing', 'player_profile')", name=op.f('ck_observation_kind')),
    sa.CheckConstraint('envelope_extra IS NULL OR json_valid(envelope_extra)', name=op.f('ck_observation_envelope_extra_json')),
    sa.CheckConstraint('fetched_at_valid = (fetched_instant IS NOT NULL)', name=op.f('ck_observation_fetched')),
    sa.CheckConstraint('length(trim(key)) > 0', name=op.f('ck_observation_key')),
    sa.CheckConstraint('length(trim(provider)) > 0', name=op.f('ck_observation_provider')),
    sa.CheckConstraint('line >= 1', name=op.f('ck_observation_line')),
    sa.ForeignKeyConstraint(['content_hash'], ['content.hash'], name=op.f('fk_observation_content_hash_content')),
    sa.ForeignKeyConstraint(['match_id'], ['match.id'], name=op.f('fk_observation_match_id_match')),
    sa.ForeignKeyConstraint(['player_id'], ['player.id'], name=op.f('fk_observation_player_id_player')),
    sa.ForeignKeyConstraint(['source_file_id'], ['source_file.id'], name=op.f('fk_observation_source_file_id_source_file')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_observation')),
    sa.UniqueConstraint('id', 'match_id', 'key', 'sub', name=op.f('uq_observation_id_match_id_key_sub')),
    sa.UniqueConstraint('source_file_id', 'line', name=op.f('uq_observation_source_file_id_line'))
    )
    with op.batch_alter_table('observation', schema=None) as batch_op:
        batch_op.create_index('ix_observation_content_hash', ['content_hash'], unique=False)
        batch_op.create_index('ix_observation_external_section', ['provider', 'external_event_id', 'key', 'sub'], unique=False)
        batch_op.create_index('ix_observation_player_id', ['player_id'], unique=False)
        batch_op.create_index('ix_observation_section', ['match_id', 'key', 'sub'], unique=False)

    op.create_table('player_external_id',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('provider', sa.String(), nullable=False),
    sa.Column('namespace', sa.String(), server_default=sa.text("''"), nullable=False),
    sa.Column('external_id', sa.String(), nullable=False),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('entity_id', sa.Integer(), nullable=False),
    sa.Column('decision_id', sa.Integer(), nullable=True),
    sa.Column('created_in_run', sa.Integer(), nullable=False),
    sa.CheckConstraint("status = 'origin' OR decision_id IS NOT NULL", name=op.f('ck_player_external_id_decision')),
    sa.CheckConstraint("status IN ('origin', 'verified', 'rejected')", name=op.f('ck_player_external_id_status')),
    sa.CheckConstraint('length(trim(external_id)) > 0', name=op.f('ck_player_external_id_external_id')),
    sa.CheckConstraint('length(trim(provider)) > 0', name=op.f('ck_player_external_id_provider')),
    sa.ForeignKeyConstraint(['created_in_run'], ['import_run.id'], name=op.f('fk_player_external_id_created_in_run_import_run')),
    sa.ForeignKeyConstraint(['decision_id'], ['identity_decision.id'], name=op.f('fk_player_external_id_decision_id_identity_decision')),
    sa.ForeignKeyConstraint(['entity_id'], ['player.id'], name=op.f('fk_player_external_id_entity_id_player')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_player_external_id'))
    )
    with op.batch_alter_table('player_external_id', schema=None) as batch_op:
        batch_op.create_index('ix_player_external_id_entity_id', ['entity_id'], unique=False)
        batch_op.create_index('uq_player_external_id_live', ['provider', 'namespace', 'external_id'], unique=True, sqlite_where=sa.text("status <> 'rejected'"))
        batch_op.create_index('uq_player_external_id_origin', ['entity_id'], unique=True, sqlite_where=sa.text("status = 'origin'"))

    op.create_table('season',
    sa.Column('competition_id', sa.Integer(), nullable=True),
    sa.Column('label', sa.String(), nullable=True),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('uid', sa.String(length=36), nullable=False),
    sa.Column('status', sa.String(), server_default=sa.text("'active'"), nullable=False),
    sa.Column('merged_into_id', sa.Integer(), nullable=True),
    sa.Column('created_in_run', sa.Integer(), nullable=False),
    sa.CheckConstraint("(status = 'merged') = (merged_into_id IS NOT NULL)", name=op.f('ck_season_merged_into')),
    sa.CheckConstraint("status IN ('active', 'merged')", name=op.f('ck_season_status')),
    sa.CheckConstraint('length(uid) = 36', name=op.f('ck_season_uid')),
    sa.CheckConstraint('merged_into_id IS NULL OR merged_into_id <> id', name=op.f('ck_season_not_self')),
    sa.ForeignKeyConstraint(['competition_id'], ['competition.id'], name=op.f('fk_season_competition_id_competition')),
    sa.ForeignKeyConstraint(['created_in_run'], ['import_run.id'], name=op.f('fk_season_created_in_run_import_run')),
    sa.ForeignKeyConstraint(['merged_into_id'], ['season.id'], name=op.f('fk_season_merged_into_id_season')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_season')),
    sa.UniqueConstraint('uid', name=op.f('uq_season_uid'))
    )
    op.create_table('stage',
    sa.Column('competition_id', sa.Integer(), nullable=True),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('uid', sa.String(length=36), nullable=False),
    sa.Column('status', sa.String(), server_default=sa.text("'active'"), nullable=False),
    sa.Column('merged_into_id', sa.Integer(), nullable=True),
    sa.Column('created_in_run', sa.Integer(), nullable=False),
    sa.CheckConstraint("(status = 'merged') = (merged_into_id IS NOT NULL)", name=op.f('ck_stage_merged_into')),
    sa.CheckConstraint("status IN ('active', 'merged')", name=op.f('ck_stage_status')),
    sa.CheckConstraint('length(uid) = 36', name=op.f('ck_stage_uid')),
    sa.CheckConstraint('merged_into_id IS NULL OR merged_into_id <> id', name=op.f('ck_stage_not_self')),
    sa.ForeignKeyConstraint(['competition_id'], ['competition.id'], name=op.f('fk_stage_competition_id_competition')),
    sa.ForeignKeyConstraint(['created_in_run'], ['import_run.id'], name=op.f('fk_stage_created_in_run_import_run')),
    sa.ForeignKeyConstraint(['merged_into_id'], ['stage.id'], name=op.f('fk_stage_merged_into_id_stage')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_stage')),
    sa.UniqueConstraint('uid', name=op.f('uq_stage_uid'))
    )
    op.create_table('team_external_id',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('provider', sa.String(), nullable=False),
    sa.Column('namespace', sa.String(), server_default=sa.text("''"), nullable=False),
    sa.Column('external_id', sa.String(), nullable=False),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('entity_id', sa.Integer(), nullable=False),
    sa.Column('decision_id', sa.Integer(), nullable=True),
    sa.Column('created_in_run', sa.Integer(), nullable=False),
    sa.CheckConstraint("status = 'origin' OR decision_id IS NOT NULL", name=op.f('ck_team_external_id_decision')),
    sa.CheckConstraint("status IN ('origin', 'verified', 'rejected')", name=op.f('ck_team_external_id_status')),
    sa.CheckConstraint('length(trim(external_id)) > 0', name=op.f('ck_team_external_id_external_id')),
    sa.CheckConstraint('length(trim(provider)) > 0', name=op.f('ck_team_external_id_provider')),
    sa.ForeignKeyConstraint(['created_in_run'], ['import_run.id'], name=op.f('fk_team_external_id_created_in_run_import_run')),
    sa.ForeignKeyConstraint(['decision_id'], ['identity_decision.id'], name=op.f('fk_team_external_id_decision_id_identity_decision')),
    sa.ForeignKeyConstraint(['entity_id'], ['team.id'], name=op.f('fk_team_external_id_entity_id_team')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_team_external_id'))
    )
    with op.batch_alter_table('team_external_id', schema=None) as batch_op:
        batch_op.create_index('ix_team_external_id_entity_id', ['entity_id'], unique=False)
        batch_op.create_index('uq_team_external_id_live', ['provider', 'namespace', 'external_id'], unique=True, sqlite_where=sa.text("status <> 'rejected'"))
        batch_op.create_index('uq_team_external_id_origin', ['entity_id'], unique=True, sqlite_where=sa.text("status = 'origin'"))

    op.create_table('discrepancy',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(), nullable=False),
    sa.Column('match_id', sa.Integer(), nullable=True),
    sa.Column('player_id', sa.Integer(), nullable=True),
    sa.Column('key', sa.String(), nullable=False),
    sa.Column('sub', sa.String(), server_default=sa.text("''"), nullable=False),
    sa.Column('integrity', sa.Boolean(create_constraint=True, name='integrity'), nullable=False),
    sa.Column('selected_observation_id', sa.Integer(), nullable=True),
    sa.Column('changed_fields', sa.Text(), nullable=True),
    sa.Column('superseded_in_run', sa.Integer(), nullable=True),
    sa.Column('rule_set_id', sa.Integer(), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=False),
    sa.CheckConstraint("(kind = 'player_attribute') = (player_id IS NOT NULL AND match_id IS NULL)", name=op.f('ck_discrepancy_subject_player')),
    sa.CheckConstraint("kind = 'player_attribute' OR (match_id IS NOT NULL AND player_id IS NULL)", name=op.f('ck_discrepancy_subject_match')),
    sa.CheckConstraint("kind IN ('section', 'listing_vs_event', 'player_attribute')", name=op.f('ck_discrepancy_kind')),
    sa.CheckConstraint('changed_fields IS NULL OR json_valid(changed_fields)', name=op.f('ck_discrepancy_changed_fields_json')),
    sa.ForeignKeyConstraint(['match_id'], ['match.id'], name=op.f('fk_discrepancy_match_id_match')),
    sa.ForeignKeyConstraint(['player_id'], ['player.id'], name=op.f('fk_discrepancy_player_id_player')),
    sa.ForeignKeyConstraint(['rule_set_id'], ['rule_set.id'], name=op.f('fk_discrepancy_rule_set_id_rule_set')),
    sa.ForeignKeyConstraint(['run_id'], ['import_run.id'], name=op.f('fk_discrepancy_run_id_import_run')),
    sa.ForeignKeyConstraint(['selected_observation_id'], ['observation.id'], name=op.f('fk_discrepancy_selected_observation_id_observation')),
    sa.ForeignKeyConstraint(['superseded_in_run'], ['import_run.id'], name=op.f('fk_discrepancy_superseded_in_run_import_run')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_discrepancy'))
    )
    with op.batch_alter_table('discrepancy', schema=None) as batch_op:
        batch_op.create_index('ix_discrepancy_match_id', ['match_id'], unique=False)
        batch_op.create_index('ix_discrepancy_player_id', ['player_id'], unique=False)

    op.create_table('player_attribute_observation',
    sa.Column('observation_id', sa.Integer(), nullable=False),
    sa.Column('entry_index', sa.Integer(), nullable=False),
    sa.Column('field', sa.String(), nullable=False),
    sa.Column('external_player_id', sa.String(), nullable=False),
    sa.Column('player_id', sa.Integer(), nullable=True),
    sa.Column('value_json', sa.Text(), nullable=False),
    sa.Column('fetched_instant', sa.String(), nullable=True),
    sa.CheckConstraint("field IN ('name', 'usual_position', 'country', 'birth_date', 'height_cm', 'preferred_foot')", name=op.f('ck_player_attribute_observation_field')),
    sa.CheckConstraint('entry_index >= 0', name=op.f('ck_player_attribute_observation_entry_index')),
    sa.CheckConstraint('json_valid(value_json)', name=op.f('ck_player_attribute_observation_value_json_json')),
    sa.ForeignKeyConstraint(['observation_id'], ['observation.id'], name=op.f('fk_player_attribute_observation_observation_id_observation')),
    sa.ForeignKeyConstraint(['player_id'], ['player.id'], name=op.f('fk_player_attribute_observation_player_id_player')),
    sa.PrimaryKeyConstraint('observation_id', 'entry_index', 'field', name=op.f('pk_player_attribute_observation'))
    )
    with op.batch_alter_table('player_attribute_observation', schema=None) as batch_op:
        batch_op.create_index('ix_player_attribute_observation_player_id', ['player_id'], unique=False)

    op.create_table('season_external_id',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('provider', sa.String(), nullable=False),
    sa.Column('namespace', sa.String(), server_default=sa.text("''"), nullable=False),
    sa.Column('external_id', sa.String(), nullable=False),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('entity_id', sa.Integer(), nullable=False),
    sa.Column('decision_id', sa.Integer(), nullable=True),
    sa.Column('created_in_run', sa.Integer(), nullable=False),
    sa.CheckConstraint("status = 'origin' OR decision_id IS NOT NULL", name=op.f('ck_season_external_id_decision')),
    sa.CheckConstraint("status IN ('origin', 'verified', 'rejected')", name=op.f('ck_season_external_id_status')),
    sa.CheckConstraint('length(trim(external_id)) > 0', name=op.f('ck_season_external_id_external_id')),
    sa.CheckConstraint('length(trim(provider)) > 0', name=op.f('ck_season_external_id_provider')),
    sa.ForeignKeyConstraint(['created_in_run'], ['import_run.id'], name=op.f('fk_season_external_id_created_in_run_import_run')),
    sa.ForeignKeyConstraint(['decision_id'], ['identity_decision.id'], name=op.f('fk_season_external_id_decision_id_identity_decision')),
    sa.ForeignKeyConstraint(['entity_id'], ['season.id'], name=op.f('fk_season_external_id_entity_id_season')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_season_external_id'))
    )
    with op.batch_alter_table('season_external_id', schema=None) as batch_op:
        batch_op.create_index('ix_season_external_id_entity_id', ['entity_id'], unique=False)
        batch_op.create_index('uq_season_external_id_live', ['provider', 'namespace', 'external_id'], unique=True, sqlite_where=sa.text("status <> 'rejected'"))
        batch_op.create_index('uq_season_external_id_origin', ['entity_id'], unique=True, sqlite_where=sa.text("status = 'origin'"))

    op.create_table('section_selection',
    sa.Column('match_id', sa.Integer(), nullable=False),
    sa.Column('key', sa.String(), nullable=False),
    sa.Column('sub', sa.String(), server_default=sa.text("''"), nullable=False),
    sa.Column('observation_id', sa.Integer(), nullable=False),
    sa.Column('versions', sa.Integer(), nullable=False),
    sa.Column('rule_set_id', sa.Integer(), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=False),
    sa.CheckConstraint('versions >= 1', name=op.f('ck_section_selection_versions')),
    sa.ForeignKeyConstraint(['match_id'], ['match.id'], name=op.f('fk_section_selection_match_id_match')),
    sa.ForeignKeyConstraint(['observation_id', 'match_id', 'key', 'sub'], ['observation.id', 'observation.match_id', 'observation.key', 'observation.sub'], name='fk_section_selection_observation'),
    sa.ForeignKeyConstraint(['rule_set_id'], ['rule_set.id'], name=op.f('fk_section_selection_rule_set_id_rule_set')),
    sa.ForeignKeyConstraint(['run_id'], ['import_run.id'], name=op.f('fk_section_selection_run_id_import_run')),
    sa.PrimaryKeyConstraint('match_id', 'key', 'sub', name=op.f('pk_section_selection')),
    sa.UniqueConstraint('match_id', 'key', 'sub', 'observation_id', name=op.f('uq_section_selection_match_id_key_sub_observation_id'))
    )
    op.create_table('section_selection_history',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('match_id', sa.Integer(), nullable=False),
    sa.Column('key', sa.String(), nullable=False),
    sa.Column('sub', sa.String(), server_default=sa.text("''"), nullable=False),
    sa.Column('observation_id', sa.Integer(), nullable=False),
    sa.Column('versions', sa.Integer(), nullable=False),
    sa.Column('rule_set_id', sa.Integer(), nullable=False),
    sa.Column('valid_from_run', sa.Integer(), nullable=False),
    sa.Column('valid_to_run', sa.Integer(), nullable=True),
    sa.CheckConstraint('versions >= 1', name=op.f('ck_section_selection_history_versions')),
    sa.ForeignKeyConstraint(['match_id'], ['match.id'], name=op.f('fk_section_selection_history_match_id_match')),
    sa.ForeignKeyConstraint(['observation_id', 'match_id', 'key', 'sub'], ['observation.id', 'observation.match_id', 'observation.key', 'observation.sub'], name='fk_section_selection_history_observation'),
    sa.ForeignKeyConstraint(['rule_set_id'], ['rule_set.id'], name=op.f('fk_section_selection_history_rule_set_id_rule_set')),
    sa.ForeignKeyConstraint(['valid_from_run'], ['import_run.id'], name=op.f('fk_section_selection_history_valid_from_run_import_run')),
    sa.ForeignKeyConstraint(['valid_to_run'], ['import_run.id'], name=op.f('fk_section_selection_history_valid_to_run_import_run')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_section_selection_history'))
    )
    with op.batch_alter_table('section_selection_history', schema=None) as batch_op:
        batch_op.create_index('ix_section_selection_history_section', ['match_id', 'key', 'sub'], unique=False)

    op.create_table('stage_external_id',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('provider', sa.String(), nullable=False),
    sa.Column('namespace', sa.String(), server_default=sa.text("''"), nullable=False),
    sa.Column('external_id', sa.String(), nullable=False),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('entity_id', sa.Integer(), nullable=False),
    sa.Column('decision_id', sa.Integer(), nullable=True),
    sa.Column('created_in_run', sa.Integer(), nullable=False),
    sa.CheckConstraint("status = 'origin' OR decision_id IS NOT NULL", name=op.f('ck_stage_external_id_decision')),
    sa.CheckConstraint("status IN ('origin', 'verified', 'rejected')", name=op.f('ck_stage_external_id_status')),
    sa.CheckConstraint('length(trim(external_id)) > 0', name=op.f('ck_stage_external_id_external_id')),
    sa.CheckConstraint('length(trim(provider)) > 0', name=op.f('ck_stage_external_id_provider')),
    sa.ForeignKeyConstraint(['created_in_run'], ['import_run.id'], name=op.f('fk_stage_external_id_created_in_run_import_run')),
    sa.ForeignKeyConstraint(['decision_id'], ['identity_decision.id'], name=op.f('fk_stage_external_id_decision_id_identity_decision')),
    sa.ForeignKeyConstraint(['entity_id'], ['stage.id'], name=op.f('fk_stage_external_id_entity_id_stage')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_stage_external_id'))
    )
    with op.batch_alter_table('stage_external_id', schema=None) as batch_op:
        batch_op.create_index('ix_stage_external_id_entity_id', ['entity_id'], unique=False)
        batch_op.create_index('uq_stage_external_id_live', ['provider', 'namespace', 'external_id'], unique=True, sqlite_where=sa.text("status <> 'rejected'"))
        batch_op.create_index('uq_stage_external_id_origin', ['entity_id'], unique=True, sqlite_where=sa.text("status = 'origin'"))

    op.create_table('team_name',
    sa.Column('team_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(), nullable=True),
    sa.Column('source_observation_id', sa.Integer(), nullable=False),
    sa.Column('rule_set_id', sa.Integer(), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['rule_set_id'], ['rule_set.id'], name=op.f('fk_team_name_rule_set_id_rule_set')),
    sa.ForeignKeyConstraint(['run_id'], ['import_run.id'], name=op.f('fk_team_name_run_id_import_run')),
    sa.ForeignKeyConstraint(['source_observation_id'], ['observation.id'], name=op.f('fk_team_name_source_observation_id_observation')),
    sa.ForeignKeyConstraint(['team_id'], ['team.id'], name=op.f('fk_team_name_team_id_team')),
    sa.PrimaryKeyConstraint('team_id', name=op.f('pk_team_name'))
    )
    op.create_table('call_up',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('match_id', sa.Integer(), nullable=False),
    sa.Column('lineup_key', sa.String(), server_default=sa.text("'lineups'"), nullable=False),
    sa.Column('lineup_sub', sa.String(), server_default=sa.text("''"), nullable=False),
    sa.Column('lineup_observation_id', sa.Integer(), nullable=False),
    sa.Column('side', sa.String(), nullable=False),
    sa.Column('entry_index', sa.Integer(), nullable=False),
    sa.Column('team_id', sa.Integer(), nullable=False),
    sa.Column('external_player_id', sa.String(), nullable=False),
    sa.Column('player_id', sa.Integer(), nullable=True),
    sa.Column('external_team_id', sa.String(), nullable=True),
    sa.Column('substitute', sa.Text(), nullable=True),
    sa.Column('shirt_number', sa.Text(), nullable=True),
    sa.Column('minutes_played', sa.Text(), nullable=True),
    sa.Column('match_position', sa.Text(), nullable=True),
    sa.Column('played', sa.Boolean(create_constraint=True, name='played'), nullable=True),
    sa.Column('statistics_kind', sa.String(), nullable=False),
    sa.Column('rule_set_id', sa.Integer(), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=False),
    sa.CheckConstraint("lineup_key = 'lineups'", name=op.f('ck_call_up_lineup_key')),
    sa.CheckConstraint("side IN ('home', 'away')", name=op.f('ck_call_up_side')),
    sa.CheckConstraint("statistics_kind IN ('object', 'null', 'absent', 'other')", name=op.f('ck_call_up_statistics_kind')),
    sa.CheckConstraint('entry_index >= 0', name=op.f('ck_call_up_entry_index')),
    sa.CheckConstraint('length(trim(external_player_id)) > 0', name=op.f('ck_call_up_external_player_id')),
    sa.CheckConstraint('match_position IS NULL OR json_valid(match_position)', name=op.f('ck_call_up_match_position_json')),
    sa.CheckConstraint('minutes_played IS NULL OR json_valid(minutes_played)', name=op.f('ck_call_up_minutes_played_json')),
    sa.CheckConstraint('shirt_number IS NULL OR json_valid(shirt_number)', name=op.f('ck_call_up_shirt_number_json')),
    sa.CheckConstraint('substitute IS NULL OR json_valid(substitute)', name=op.f('ck_call_up_substitute_json')),
    sa.ForeignKeyConstraint(['match_id', 'lineup_key', 'lineup_sub', 'lineup_observation_id'], ['section_selection.match_id', 'section_selection.key', 'section_selection.sub', 'section_selection.observation_id'], name='fk_call_up_selection', initially='DEFERRED', deferrable=True),
    sa.ForeignKeyConstraint(['match_id'], ['match.id'], name=op.f('fk_call_up_match_id_match')),
    sa.ForeignKeyConstraint(['player_id'], ['player.id'], name=op.f('fk_call_up_player_id_player')),
    sa.ForeignKeyConstraint(['rule_set_id'], ['rule_set.id'], name=op.f('fk_call_up_rule_set_id_rule_set')),
    sa.ForeignKeyConstraint(['run_id'], ['import_run.id'], name=op.f('fk_call_up_run_id_import_run')),
    sa.ForeignKeyConstraint(['team_id'], ['team.id'], name=op.f('fk_call_up_team_id_team')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_call_up')),
    sa.UniqueConstraint('lineup_observation_id', 'side', 'entry_index', name=op.f('uq_call_up_lineup_observation_id_side_entry_index'))
    )
    with op.batch_alter_table('call_up', schema=None) as batch_op:
        batch_op.create_index('ix_call_up_match_player', ['match_id', 'player_id'], unique=False)
        batch_op.create_index('ix_call_up_player_id', ['player_id'], unique=False)

    op.create_table('discrepancy_observation',
    sa.Column('discrepancy_id', sa.Integer(), nullable=False),
    sa.Column('observation_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['discrepancy_id'], ['discrepancy.id'], name=op.f('fk_discrepancy_observation_discrepancy_id_discrepancy')),
    sa.ForeignKeyConstraint(['observation_id'], ['observation.id'], name=op.f('fk_discrepancy_observation_observation_id_observation')),
    sa.PrimaryKeyConstraint('discrepancy_id', 'observation_id', name=op.f('pk_discrepancy_observation'))
    )
    op.create_table('discrepancy_reason',
    sa.Column('discrepancy_id', sa.Integer(), nullable=False),
    sa.Column('reason', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['discrepancy_id'], ['discrepancy.id'], name=op.f('fk_discrepancy_reason_discrepancy_id_discrepancy')),
    sa.PrimaryKeyConstraint('discrepancy_id', 'reason', name=op.f('pk_discrepancy_reason'))
    )
    op.create_table('discrepancy_value',
    sa.Column('discrepancy_id', sa.Integer(), nullable=False),
    sa.Column('field', sa.String(), nullable=False),
    sa.Column('ordinal', sa.Integer(), nullable=False),
    sa.Column('value_json', sa.Text(), nullable=False),
    sa.CheckConstraint('json_valid(value_json)', name=op.f('ck_discrepancy_value_value_json_json')),
    sa.ForeignKeyConstraint(['discrepancy_id'], ['discrepancy.id'], name=op.f('fk_discrepancy_value_discrepancy_id_discrepancy')),
    sa.PrimaryKeyConstraint('discrepancy_id', 'field', 'ordinal', name=op.f('pk_discrepancy_value'))
    )
    op.create_table('identity_decision_subject',
    sa.Column('decision_id', sa.Integer(), nullable=False),
    sa.Column('ordinal', sa.Integer(), nullable=False),
    sa.Column('role', sa.String(), nullable=False),
    sa.Column('team_id', sa.Integer(), nullable=True),
    sa.Column('player_id', sa.Integer(), nullable=True),
    sa.Column('match_id', sa.Integer(), nullable=True),
    sa.Column('competition_id', sa.Integer(), nullable=True),
    sa.Column('season_id', sa.Integer(), nullable=True),
    sa.Column('stage_id', sa.Integer(), nullable=True),
    sa.Column('team_external_id_id', sa.Integer(), nullable=True),
    sa.Column('player_external_id_id', sa.Integer(), nullable=True),
    sa.Column('match_external_id_id', sa.Integer(), nullable=True),
    sa.Column('competition_external_id_id', sa.Integer(), nullable=True),
    sa.Column('season_external_id_id', sa.Integer(), nullable=True),
    sa.Column('stage_external_id_id', sa.Integer(), nullable=True),
    sa.CheckConstraint("role IN ('source', 'target', 'created', 'external_id')", name=op.f('ck_identity_decision_subject_role')),
    sa.CheckConstraint('(CASE WHEN team_external_id_id IS NOT NULL THEN 1 ELSE 0 END) + (CASE WHEN player_external_id_id IS NOT NULL THEN 1 ELSE 0 END) + (CASE WHEN match_external_id_id IS NOT NULL THEN 1 ELSE 0 END) + (CASE WHEN competition_external_id_id IS NOT NULL THEN 1 ELSE 0 END) + (CASE WHEN season_external_id_id IS NOT NULL THEN 1 ELSE 0 END) + (CASE WHEN stage_external_id_id IS NOT NULL THEN 1 ELSE 0 END) <= 1', name=op.f('ck_identity_decision_subject_one_external_id')),
    sa.CheckConstraint('(CASE WHEN team_id IS NOT NULL THEN 1 ELSE 0 END) + (CASE WHEN player_id IS NOT NULL THEN 1 ELSE 0 END) + (CASE WHEN match_id IS NOT NULL THEN 1 ELSE 0 END) + (CASE WHEN competition_id IS NOT NULL THEN 1 ELSE 0 END) + (CASE WHEN season_id IS NOT NULL THEN 1 ELSE 0 END) + (CASE WHEN stage_id IS NOT NULL THEN 1 ELSE 0 END) + (CASE WHEN team_external_id_id IS NOT NULL THEN 1 ELSE 0 END) + (CASE WHEN player_external_id_id IS NOT NULL THEN 1 ELSE 0 END) + (CASE WHEN match_external_id_id IS NOT NULL THEN 1 ELSE 0 END) + (CASE WHEN competition_external_id_id IS NOT NULL THEN 1 ELSE 0 END) + (CASE WHEN season_external_id_id IS NOT NULL THEN 1 ELSE 0 END) + (CASE WHEN stage_external_id_id IS NOT NULL THEN 1 ELSE 0 END) >= 1', name=op.f('ck_identity_decision_subject_some_subject')),
    sa.CheckConstraint('(CASE WHEN team_id IS NOT NULL THEN 1 ELSE 0 END) + (CASE WHEN player_id IS NOT NULL THEN 1 ELSE 0 END) + (CASE WHEN match_id IS NOT NULL THEN 1 ELSE 0 END) + (CASE WHEN competition_id IS NOT NULL THEN 1 ELSE 0 END) + (CASE WHEN season_id IS NOT NULL THEN 1 ELSE 0 END) + (CASE WHEN stage_id IS NOT NULL THEN 1 ELSE 0 END) <= 1', name=op.f('ck_identity_decision_subject_one_entity')),
    sa.ForeignKeyConstraint(['competition_external_id_id'], ['competition_external_id.id'], name=op.f('fk_identity_decision_subject_competition_external_id_id_competition_external_id')),
    sa.ForeignKeyConstraint(['competition_id'], ['competition.id'], name=op.f('fk_identity_decision_subject_competition_id_competition')),
    sa.ForeignKeyConstraint(['decision_id'], ['identity_decision.id'], name=op.f('fk_identity_decision_subject_decision_id_identity_decision')),
    sa.ForeignKeyConstraint(['match_external_id_id'], ['match_external_id.id'], name=op.f('fk_identity_decision_subject_match_external_id_id_match_external_id')),
    sa.ForeignKeyConstraint(['match_id'], ['match.id'], name=op.f('fk_identity_decision_subject_match_id_match')),
    sa.ForeignKeyConstraint(['player_external_id_id'], ['player_external_id.id'], name=op.f('fk_identity_decision_subject_player_external_id_id_player_external_id')),
    sa.ForeignKeyConstraint(['player_id'], ['player.id'], name=op.f('fk_identity_decision_subject_player_id_player')),
    sa.ForeignKeyConstraint(['season_external_id_id'], ['season_external_id.id'], name=op.f('fk_identity_decision_subject_season_external_id_id_season_external_id')),
    sa.ForeignKeyConstraint(['season_id'], ['season.id'], name=op.f('fk_identity_decision_subject_season_id_season')),
    sa.ForeignKeyConstraint(['stage_external_id_id'], ['stage_external_id.id'], name=op.f('fk_identity_decision_subject_stage_external_id_id_stage_external_id')),
    sa.ForeignKeyConstraint(['stage_id'], ['stage.id'], name=op.f('fk_identity_decision_subject_stage_id_stage')),
    sa.ForeignKeyConstraint(['team_external_id_id'], ['team_external_id.id'], name=op.f('fk_identity_decision_subject_team_external_id_id_team_external_id')),
    sa.ForeignKeyConstraint(['team_id'], ['team.id'], name=op.f('fk_identity_decision_subject_team_id_team')),
    sa.PrimaryKeyConstraint('decision_id', 'ordinal', name=op.f('pk_identity_decision_subject'))
    )
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
    sa.CheckConstraint('added_time IS NULL OR json_valid(added_time)', name=op.f('ck_incident_added_time_json')),
    sa.CheckConstraint('is_home IS NULL OR json_valid(is_home)', name=op.f('ck_incident_is_home_json')),
    sa.CheckConstraint('seq >= 0', name=op.f('ck_incident_seq')),
    sa.CheckConstraint('time IS NULL OR json_valid(time)', name=op.f('ck_incident_time_json')),
    sa.ForeignKeyConstraint(['assist1_player_id'], ['player.id'], name=op.f('fk_incident_assist1_player_id_player')),
    sa.ForeignKeyConstraint(['match_id', 'section_key', 'section_sub', 'observation_id'], ['section_selection.match_id', 'section_selection.key', 'section_selection.sub', 'section_selection.observation_id'], name='fk_incident_selection', initially='DEFERRED', deferrable=True),
    sa.ForeignKeyConstraint(['match_id'], ['match.id'], name=op.f('fk_incident_match_id_match')),
    sa.ForeignKeyConstraint(['player_id'], ['player.id'], name=op.f('fk_incident_player_id_player')),
    sa.ForeignKeyConstraint(['player_in_id'], ['player.id'], name=op.f('fk_incident_player_in_id_player')),
    sa.ForeignKeyConstraint(['player_out_id'], ['player.id'], name=op.f('fk_incident_player_out_id_player')),
    sa.ForeignKeyConstraint(['rule_set_id'], ['rule_set.id'], name=op.f('fk_incident_rule_set_id_rule_set')),
    sa.ForeignKeyConstraint(['run_id'], ['import_run.id'], name=op.f('fk_incident_run_id_import_run')),
    sa.PrimaryKeyConstraint('match_id', 'observation_id', 'seq', name=op.f('pk_incident'))
    )
    op.create_table('match_state',
    sa.Column('match_id', sa.Integer(), nullable=False),
    sa.Column('source_key', sa.String(), nullable=False),
    sa.Column('source_sub', sa.String(), server_default=sa.text("''"), nullable=False),
    sa.Column('source_observation_id', sa.Integer(), nullable=False),
    sa.Column('stage_id', sa.Integer(), nullable=True),
    sa.Column('season_id', sa.Integer(), nullable=True),
    sa.Column('competition_id', sa.Integer(), nullable=True),
    sa.Column('external_stage_id', sa.String(), nullable=True),
    sa.Column('external_season_id', sa.String(), nullable=True),
    sa.Column('external_competition_id', sa.String(), nullable=True),
    sa.Column('external_home_team_id', sa.String(), nullable=False),
    sa.Column('external_away_team_id', sa.String(), nullable=False),
    sa.Column('competition_name', sa.String(), nullable=True),
    sa.Column('season_label', sa.String(), nullable=True),
    sa.Column('status', sa.String(), nullable=True),
    sa.Column('kickoff_utc', sa.String(), nullable=True),
    sa.Column('round', sa.Text(), nullable=True),
    sa.Column('home_team_id', sa.Integer(), nullable=False),
    sa.Column('away_team_id', sa.Integer(), nullable=False),
    sa.Column('home_score', sa.Text(), nullable=True),
    sa.Column('away_score', sa.Text(), nullable=True),
    sa.Column('calendar_source', sa.String(), nullable=False),
    sa.Column('version_conflict', sa.Boolean(create_constraint=True, name='version_conflict'), nullable=False),
    sa.Column('rule_set_id', sa.Integer(), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=False),
    sa.CheckConstraint("source_key IN ('event', 'listing')", name=op.f('ck_match_state_source_key')),
    sa.CheckConstraint('away_score IS NULL OR json_valid(away_score)', name=op.f('ck_match_state_away_score_json')),
    sa.CheckConstraint('home_score IS NULL OR json_valid(home_score)', name=op.f('ck_match_state_home_score_json')),
    sa.CheckConstraint('round IS NULL OR json_valid(round)', name=op.f('ck_match_state_round_json')),
    sa.CheckConstraint('source_key = calendar_source', name=op.f('ck_match_state_calendar_source')),
    sa.ForeignKeyConstraint(['away_team_id'], ['team.id'], name=op.f('fk_match_state_away_team_id_team')),
    sa.ForeignKeyConstraint(['competition_id'], ['competition.id'], name=op.f('fk_match_state_competition_id_competition')),
    sa.ForeignKeyConstraint(['home_team_id'], ['team.id'], name=op.f('fk_match_state_home_team_id_team')),
    sa.ForeignKeyConstraint(['match_id', 'source_key', 'source_sub', 'source_observation_id'], ['section_selection.match_id', 'section_selection.key', 'section_selection.sub', 'section_selection.observation_id'], name='fk_match_state_selection', initially='DEFERRED', deferrable=True),
    sa.ForeignKeyConstraint(['match_id'], ['match.id'], name=op.f('fk_match_state_match_id_match')),
    sa.ForeignKeyConstraint(['rule_set_id'], ['rule_set.id'], name=op.f('fk_match_state_rule_set_id_rule_set')),
    sa.ForeignKeyConstraint(['run_id'], ['import_run.id'], name=op.f('fk_match_state_run_id_import_run')),
    sa.ForeignKeyConstraint(['season_id'], ['season.id'], name=op.f('fk_match_state_season_id_season')),
    sa.ForeignKeyConstraint(['stage_id'], ['stage.id'], name=op.f('fk_match_state_stage_id_stage')),
    sa.PrimaryKeyConstraint('match_id', name=op.f('pk_match_state'))
    )
    with op.batch_alter_table('match_state', schema=None) as batch_op:
        batch_op.create_index('ix_match_state_away_team_id', ['away_team_id'], unique=False)
        batch_op.create_index('ix_match_state_home_team_id', ['home_team_id'], unique=False)
        batch_op.create_index('ix_match_state_stage_season', ['stage_id', 'season_id'], unique=False)

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
    sa.CheckConstraint('away_total IS NULL OR json_valid(away_total)', name=op.f('ck_team_match_stat_away_total_json')),
    sa.CheckConstraint('away_value IS NULL OR json_valid(away_value)', name=op.f('ck_team_match_stat_away_value_json')),
    sa.CheckConstraint('home_total IS NULL OR json_valid(home_total)', name=op.f('ck_team_match_stat_home_total_json')),
    sa.CheckConstraint('home_value IS NULL OR json_valid(home_value)', name=op.f('ck_team_match_stat_home_value_json')),
    sa.ForeignKeyConstraint(['match_id', 'section_key', 'section_sub', 'observation_id'], ['section_selection.match_id', 'section_selection.key', 'section_selection.sub', 'section_selection.observation_id'], name='fk_team_match_stat_selection', initially='DEFERRED', deferrable=True),
    sa.ForeignKeyConstraint(['match_id'], ['match.id'], name=op.f('fk_team_match_stat_match_id_match')),
    sa.ForeignKeyConstraint(['rule_set_id'], ['rule_set.id'], name=op.f('fk_team_match_stat_rule_set_id_rule_set')),
    sa.ForeignKeyConstraint(['run_id'], ['import_run.id'], name=op.f('fk_team_match_stat_run_id_import_run')),
    sa.PrimaryKeyConstraint('match_id', 'observation_id', 'period_index', 'group_index', 'item_index', name=op.f('pk_team_match_stat'))
    )
    with op.batch_alter_table('team_match_stat', schema=None) as batch_op:
        batch_op.create_index('ix_team_match_stat_key', ['key'], unique=False)

    op.create_table('call_up_stat',
    sa.Column('call_up_id', sa.Integer(), nullable=False),
    sa.Column('key', sa.String(), nullable=False),
    sa.Column('ordinal', sa.Integer(), nullable=False),
    sa.Column('value_json', sa.Text(), nullable=False),
    sa.CheckConstraint('json_valid(value_json)', name=op.f('ck_call_up_stat_value_json_json')),
    sa.CheckConstraint('ordinal >= 0', name=op.f('ck_call_up_stat_ordinal')),
    sa.ForeignKeyConstraint(['call_up_id'], ['call_up.id'], name=op.f('fk_call_up_stat_call_up_id_call_up')),
    sa.PrimaryKeyConstraint('call_up_id', 'key', name=op.f('pk_call_up_stat')),
    sa.UniqueConstraint('call_up_id', 'ordinal', name=op.f('uq_call_up_stat_call_up_id_ordinal'))
    )
    with op.batch_alter_table('call_up_stat', schema=None) as batch_op:
        batch_op.create_index('ix_call_up_stat_key', ['key'], unique=False)

    op.create_table('match_version_issue',
    sa.Column('match_id', sa.Integer(), nullable=False),
    sa.Column('reason', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['match_id'], ['match_state.match_id'], name=op.f('fk_match_version_issue_match_id_match_state')),
    sa.PrimaryKeyConstraint('match_id', 'reason', name=op.f('pk_match_version_issue'))
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('match_version_issue')
    with op.batch_alter_table('call_up_stat', schema=None) as batch_op:
        batch_op.drop_index('ix_call_up_stat_key')

    op.drop_table('call_up_stat')
    with op.batch_alter_table('team_match_stat', schema=None) as batch_op:
        batch_op.drop_index('ix_team_match_stat_key')

    op.drop_table('team_match_stat')
    with op.batch_alter_table('match_state', schema=None) as batch_op:
        batch_op.drop_index('ix_match_state_stage_season')
        batch_op.drop_index('ix_match_state_home_team_id')
        batch_op.drop_index('ix_match_state_away_team_id')

    op.drop_table('match_state')
    op.drop_table('incident')
    op.drop_table('identity_decision_subject')
    op.drop_table('discrepancy_value')
    op.drop_table('discrepancy_reason')
    op.drop_table('discrepancy_observation')
    with op.batch_alter_table('call_up', schema=None) as batch_op:
        batch_op.drop_index('ix_call_up_player_id')
        batch_op.drop_index('ix_call_up_match_player')

    op.drop_table('call_up')
    op.drop_table('team_name')
    with op.batch_alter_table('stage_external_id', schema=None) as batch_op:
        batch_op.drop_index('uq_stage_external_id_origin', sqlite_where=sa.text("status = 'origin'"))
        batch_op.drop_index('uq_stage_external_id_live', sqlite_where=sa.text("status <> 'rejected'"))
        batch_op.drop_index('ix_stage_external_id_entity_id')

    op.drop_table('stage_external_id')
    with op.batch_alter_table('section_selection_history', schema=None) as batch_op:
        batch_op.drop_index('ix_section_selection_history_section')

    op.drop_table('section_selection_history')
    op.drop_table('section_selection')
    with op.batch_alter_table('season_external_id', schema=None) as batch_op:
        batch_op.drop_index('uq_season_external_id_origin', sqlite_where=sa.text("status = 'origin'"))
        batch_op.drop_index('uq_season_external_id_live', sqlite_where=sa.text("status <> 'rejected'"))
        batch_op.drop_index('ix_season_external_id_entity_id')

    op.drop_table('season_external_id')
    with op.batch_alter_table('player_attribute_observation', schema=None) as batch_op:
        batch_op.drop_index('ix_player_attribute_observation_player_id')

    op.drop_table('player_attribute_observation')
    with op.batch_alter_table('discrepancy', schema=None) as batch_op:
        batch_op.drop_index('ix_discrepancy_player_id')
        batch_op.drop_index('ix_discrepancy_match_id')

    op.drop_table('discrepancy')
    with op.batch_alter_table('team_external_id', schema=None) as batch_op:
        batch_op.drop_index('uq_team_external_id_origin', sqlite_where=sa.text("status = 'origin'"))
        batch_op.drop_index('uq_team_external_id_live', sqlite_where=sa.text("status <> 'rejected'"))
        batch_op.drop_index('ix_team_external_id_entity_id')

    op.drop_table('team_external_id')
    op.drop_table('stage')
    op.drop_table('season')
    with op.batch_alter_table('player_external_id', schema=None) as batch_op:
        batch_op.drop_index('uq_player_external_id_origin', sqlite_where=sa.text("status = 'origin'"))
        batch_op.drop_index('uq_player_external_id_live', sqlite_where=sa.text("status <> 'rejected'"))
        batch_op.drop_index('ix_player_external_id_entity_id')

    op.drop_table('player_external_id')
    with op.batch_alter_table('observation', schema=None) as batch_op:
        batch_op.drop_index('ix_observation_section')
        batch_op.drop_index('ix_observation_player_id')
        batch_op.drop_index('ix_observation_external_section')
        batch_op.drop_index('ix_observation_content_hash')

    op.drop_table('observation')
    with op.batch_alter_table('match_external_id', schema=None) as batch_op:
        batch_op.drop_index('uq_match_external_id_origin', sqlite_where=sa.text("status = 'origin'"))
        batch_op.drop_index('uq_match_external_id_live', sqlite_where=sa.text("status <> 'rejected'"))
        batch_op.drop_index('ix_match_external_id_entity_id')

    op.drop_table('match_external_id')
    with op.batch_alter_table('local_replacement', schema=None) as batch_op:
        batch_op.drop_index('uq_local_replacement_active', sqlite_where=sa.text("status = 'active'"))

    op.drop_table('local_replacement')
    op.drop_table('import_run_file')
    with op.batch_alter_table('file_team_declaration', schema=None) as batch_op:
        batch_op.drop_index('uq_file_team_declaration_active', sqlite_where=sa.text("status = 'active'"))

    op.drop_table('file_team_declaration')
    with op.batch_alter_table('competition_external_id', schema=None) as batch_op:
        batch_op.drop_index('uq_competition_external_id_origin', sqlite_where=sa.text("status = 'origin'"))
        batch_op.drop_index('uq_competition_external_id_live', sqlite_where=sa.text("status <> 'rejected'"))
        batch_op.drop_index('ix_competition_external_id_entity_id')

    op.drop_table('competition_external_id')
    op.drop_table('team')
    op.drop_table('source_file')
    op.drop_table('player')
    op.drop_table('match')
    op.drop_table('identity_decision')
    op.drop_table('competition')
    op.drop_table('rule_set')
    op.drop_table('import_run')
    op.drop_table('content')
