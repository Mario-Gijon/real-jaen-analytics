"""Initial independent Video Service schema.

Revision ID: vg0001
Revises:
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "vg0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "video_service_job",
        sa.Column("job_id", sa.Text(), primary_key=True),
        sa.Column("match_id", sa.Text(), nullable=False),
        sa.Column("youtube_video_id", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("error_code", sa.Text()),
        sa.Column("sports_events_json", postgresql.JSONB()),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("processed_at", sa.Text()),
        sa.Column("calibration_file", sa.Text()),
        sa.Column("legacy_order", sa.BigInteger()),
        sa.Column("owner_id", sa.Text()),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False, server_default="0"),
        sa.CheckConstraint(
            "status IN ('pending','downloading','calibrating','saving','completed','failed')",
            name="ck_video_service_job_status",
        ),
    )
    op.create_index(
        "one_active_video_service_job_per_match",
        "video_service_job",
        ["match_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending','downloading','calibrating','saving')"),
    )
    op.create_index(
        "one_active_video_service_job_per_youtube_id",
        "video_service_job",
        ["youtube_video_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending','downloading','calibrating','saving')"),
    )

    op.create_table(
        "video_service_calibration",
        sa.Column("calibration_id", sa.Text(), primary_key=True),
        sa.Column("source_kind", sa.Text(), nullable=False),
        sa.Column("source_name", sa.Text(), nullable=False),
        sa.Column("document", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint("source_kind IN ('static','runtime')", name="ck_video_service_calibration_source"),
    )

    op.create_table(
        "video_service_catalog_video",
        sa.Column("video_id", sa.Text(), primary_key=True),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("match_ref", sa.Text(), nullable=False),
        sa.Column("origin", sa.Text()),
        sa.Column("source_url", sa.Text()),
        sa.Column("calibration_file", sa.Text()),
        sa.Column("calibration_id", sa.Text(), sa.ForeignKey(
            "video_service_calibration.calibration_id", ondelete="RESTRICT")),
        sa.Column("synchronization_status", sa.Text(), nullable=False),
        sa.CheckConstraint("origin IS NULL OR origin IN ('youtube','local_file','unknown')",
                           name="ck_video_service_catalog_origin"),
        sa.CheckConstraint("synchronization_status IN ('calibrated','uncertain','not_calibrated')",
                           name="ck_video_service_catalog_sync_status"),
    )
    op.create_index("video_service_catalog_match", "video_service_catalog_video", ["match_ref"])

    op.create_table(
        "video_service_association",
        sa.Column("video_id", sa.Text(), primary_key=True),
        sa.Column("match_id", sa.Text(), nullable=False),
        sa.Column("youtube_video_id", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("calibration_file", sa.Text(), nullable=False),
        sa.Column("calibration_id", sa.Text(), nullable=False),
        sa.Column("synchronization_status", sa.Text(), nullable=False),
        sa.Column("processed_at", sa.Text(), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=False),
        sa.Column("file_bytes", sa.BigInteger(), nullable=False),
        sa.Column("width", sa.Integer()),
        sa.Column("height", sa.Integer()),
        sa.Column("codec", sa.Text()),
        sa.Column("calibration_version", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Text(), sa.ForeignKey("video_service_job.job_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(["calibration_id"], ["video_service_calibration.calibration_id"],
                                ondelete="RESTRICT", name="fk_video_service_association_calibration"),
        sa.CheckConstraint("synchronization_status IN ('calibrated','uncertain','not_calibrated')",
                           name="ck_video_service_association_sync_status"),
    )
    op.create_index("video_service_association_match", "video_service_association", ["match_id"])
    op.create_index("video_service_association_youtube", "video_service_association", ["youtube_video_id"])

    op.create_table(
        "video_service_match_video_override",
        sa.Column("match_id", sa.Text(), primary_key=True),
        sa.Column("active_video_id", sa.Text(), sa.ForeignKey(
            "video_service_association.video_id", ondelete="RESTRICT")),
        sa.Column("updated_at", sa.Text(), nullable=False),
    )

    # Runtime credentials only need the read path in Hito 07A. Provisioning the
    # role is an infrastructure concern, so this grant is conditional.
    op.execute("REVOKE CONNECT ON DATABASE video_service FROM PUBLIC")
    op.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'video_service_app') THEN
                GRANT CONNECT ON DATABASE video_service TO video_service_app;
                GRANT USAGE ON SCHEMA public TO video_service_app;
                REVOKE CREATE ON SCHEMA public FROM video_service_app;
                REVOKE ALL ON video_service_job, video_service_calibration,
                    video_service_catalog_video, video_service_association,
                    video_service_match_video_override FROM video_service_app;
                GRANT SELECT ON video_service_job, video_service_calibration,
                    video_service_catalog_video, video_service_association,
                    video_service_match_video_override TO video_service_app;
            END IF;
        END $$
    """)


def downgrade() -> None:
    op.execute("REVOKE ALL ON video_service_job, video_service_calibration, video_service_catalog_video, video_service_association, video_service_match_video_override FROM video_service_app")
    op.drop_table("video_service_match_video_override")
    op.drop_index("video_service_association_youtube", table_name="video_service_association")
    op.drop_index("video_service_association_match", table_name="video_service_association")
    op.drop_table("video_service_association")
    op.drop_index("video_service_catalog_match", table_name="video_service_catalog_video")
    op.drop_table("video_service_catalog_video")
    op.drop_table("video_service_calibration")
    op.drop_index("one_active_video_service_job_per_youtube_id", table_name="video_service_job")
    op.drop_index("one_active_video_service_job_per_match", table_name="video_service_job")
    op.drop_table("video_service_job")
