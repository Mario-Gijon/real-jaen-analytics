"""PostgreSQL photo catalog and original image bytes.

Revision ID: pg0005
Revises: pg0004
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'pg0005'
down_revision: Union[str, Sequence[str], None] = 'pg0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'sports_data_photo_meta',
        sa.Column('key', sa.Text(), nullable=False),
        sa.Column('value', sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint('key', name='pk_sports_data_photo_meta'),
    )
    op.create_table(
        'sports_data_photo_import',
        sa.Column('request_id', sa.Text(), nullable=False),
        sa.Column('bundle_sha256', sa.String(64), nullable=False),
        sa.Column('job_id', sa.Text(), nullable=False),
        sa.Column('operation_id', sa.Text(), nullable=False),
        sa.Column('recipe_id', sa.Text(), nullable=False),
        sa.Column('contract', sa.Text(), nullable=False),
        sa.Column('source_id', sa.Text(), nullable=False),
        sa.Column('collection_id', sa.Text(), nullable=False),
        sa.Column('collection_version', sa.Text(), nullable=False),
        sa.Column('collection_manifest_sha256', sa.String(64), nullable=False),
        sa.Column('collection_result', sa.Text(), nullable=False),
        sa.Column('imported_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('summary_json', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint("length(bundle_sha256) = 64 AND bundle_sha256 !~ '[^0-9a-f]'",
                           name='ck_sports_data_photo_import_bundle_sha256'),
        sa.CheckConstraint("length(collection_manifest_sha256) = 64 AND collection_manifest_sha256 !~ '[^0-9a-f]'",
                           name='ck_sports_data_photo_import_collection_sha256'),
        sa.CheckConstraint("collection_result IN ('complete', 'partial')",
                           name='ck_sports_data_photo_import_result'),
        sa.PrimaryKeyConstraint('request_id', name='pk_sports_data_photo_import'),
    )
    op.create_table(
        'sports_data_photo_blob',
        sa.Column('sha256', sa.String(64), nullable=False),
        sa.Column('media_type', sa.Text(), nullable=False),
        sa.Column('byte_count', sa.BigInteger(), nullable=False),
        sa.Column('width', sa.Integer(), nullable=False),
        sa.Column('height', sa.Integer(), nullable=False),
        sa.Column('first_request_id', sa.Text(), nullable=False),
        sa.Column('stored_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('content', postgresql.BYTEA(), nullable=False),
        sa.CheckConstraint("length(sha256) = 64 AND sha256 !~ '[^0-9a-f]'",
                           name='ck_sports_data_photo_blob_sha256'),
        sa.CheckConstraint("media_type = 'image/webp'", name='ck_sports_data_photo_blob_media_type'),
        sa.CheckConstraint('byte_count > 0 AND byte_count = octet_length(content)',
                           name='ck_sports_data_photo_blob_size'),
        sa.CheckConstraint('width > 0 AND height > 0', name='ck_sports_data_photo_blob_dimensions'),
        sa.PrimaryKeyConstraint('sha256', name='pk_sports_data_photo_blob'),
    )
    op.create_table(
        'sports_data_photo',
        sa.Column('id', sa.BigInteger(), sa.Identity(), nullable=False),
        # This is the external SofaScore id in photos-v1, not player.id in the internal sporting schema.
        sa.Column('sofascore_id', sa.BigInteger(), nullable=False),
        sa.Column('source_id', sa.Text(), nullable=False),
        sa.Column('sha256', sa.String(64), nullable=False),
        sa.Column('collection_id', sa.Text(), nullable=False),
        sa.Column('collection_version', sa.Text(), nullable=False),
        sa.Column('provenance', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('status', sa.Text(), nullable=False),
        sa.Column('request_id', sa.Text(), nullable=False),
        sa.Column('superseded_by_request_id', sa.Text(), nullable=True),
        sa.Column('imported_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('sofascore_id > 0', name='ck_sports_data_photo_sofascore_id'),
        sa.CheckConstraint("length(sha256) = 64 AND sha256 !~ '[^0-9a-f]'",
                           name='ck_sports_data_photo_sha256'),
        sa.CheckConstraint("status IN ('active', 'superseded')", name='ck_sports_data_photo_status'),
        sa.ForeignKeyConstraint(['sha256'], ['sports_data_photo_blob.sha256'], ondelete='RESTRICT',
                                name='fk_sports_data_photo_blob'),
        sa.ForeignKeyConstraint(['request_id'], ['sports_data_photo_import.request_id'], ondelete='RESTRICT',
                                name='fk_sports_data_photo_import'),
        sa.ForeignKeyConstraint(['superseded_by_request_id'], ['sports_data_photo_import.request_id'],
                                ondelete='RESTRICT', name='fk_sports_data_photo_superseded_import'),
        sa.PrimaryKeyConstraint('id', name='pk_sports_data_photo'),
    )
    op.create_index(
        'uq_sports_data_photo_one_active_per_source', 'sports_data_photo', ['sofascore_id', 'source_id'],
        unique=True, postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index('ix_sports_data_photo_by_player', 'sports_data_photo', ['sofascore_id', 'status'])
    op.create_index('ix_sports_data_photo_by_source', 'sports_data_photo', ['source_id', 'status'])
    op.create_table(
        'sports_data_photo_import_entry',
        sa.Column('request_id', sa.Text(), nullable=False),
        sa.Column('sofascore_id', sa.BigInteger(), nullable=False),
        sa.Column('outcome', sa.Text(), nullable=False),
        sa.Column('sha256', sa.String(64), nullable=True),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.CheckConstraint('sofascore_id > 0', name='ck_sports_data_photo_import_entry_sofascore_id'),
        sa.CheckConstraint("outcome IN ('new', 'changed', 'unchanged', 'invalid', 'unknown_player')",
                           name='ck_sports_data_photo_import_entry_outcome'),
        sa.CheckConstraint("sha256 IS NULL OR (length(sha256) = 64 AND sha256 !~ '[^0-9a-f]')",
                           name='ck_sports_data_photo_import_entry_sha256'),
        sa.ForeignKeyConstraint(['request_id'], ['sports_data_photo_import.request_id'], ondelete='RESTRICT',
                                name='fk_sports_data_photo_import_entry_import'),
        sa.PrimaryKeyConstraint('request_id', 'sofascore_id', name='pk_sports_data_photo_import_entry'),
    )

    op.execute('REVOKE ALL ON sports_data_photo_meta, sports_data_photo_blob, sports_data_photo_import, '
               'sports_data_photo, sports_data_photo_import_entry FROM PUBLIC')
    op.execute('REVOKE ALL ON sports_data_photo_meta, sports_data_photo_blob, sports_data_photo_import, '
               'sports_data_photo, sports_data_photo_import_entry FROM sports_data_app')
    op.execute('GRANT SELECT ON sports_data_photo_meta TO sports_data_app')
    op.execute('GRANT SELECT, INSERT ON sports_data_photo_blob TO sports_data_app')
    op.execute('GRANT SELECT, INSERT ON sports_data_photo_import TO sports_data_app')
    op.execute('GRANT SELECT, INSERT, UPDATE ON sports_data_photo TO sports_data_app')
    op.execute('GRANT SELECT, INSERT ON sports_data_photo_import_entry TO sports_data_app')
    op.execute('GRANT USAGE, SELECT ON SEQUENCE sports_data_photo_id_seq TO sports_data_app')


def downgrade() -> None:
    op.execute('REVOKE ALL ON sports_data_photo_meta, sports_data_photo_blob, sports_data_photo_import, '
               'sports_data_photo, sports_data_photo_import_entry FROM sports_data_app')
    op.drop_table('sports_data_photo_import_entry')
    op.drop_index('ix_sports_data_photo_by_source', table_name='sports_data_photo')
    op.drop_index('ix_sports_data_photo_by_player', table_name='sports_data_photo')
    op.drop_index('uq_sports_data_photo_one_active_per_source', table_name='sports_data_photo')
    op.drop_table('sports_data_photo')
    op.drop_table('sports_data_photo_blob')
    op.drop_table('sports_data_photo_import')
    op.drop_table('sports_data_photo_meta')
