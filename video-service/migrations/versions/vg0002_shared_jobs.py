"""Shared Video Service job ownership, ordering and write permissions.

Revision ID: vg0002
Revises: vg0001
"""
from alembic import op
import sqlalchemy as sa

revision = "vg0002"
down_revision = "vg0001"
branch_labels = None
depends_on = None


def _youtube_id(value: str | None) -> str | None:
    if not value:
        return None
    try:
        from video_service.youtube import youtube_video_id
        return youtube_video_id(value)
    except (ImportError, ValueError):
        return None


def upgrade() -> None:
    op.execute("CREATE SEQUENCE video_service_job_order_seq AS BIGINT")
    op.execute("CREATE SEQUENCE video_service_job_fencing_seq AS BIGINT")
    op.add_column("video_service_job", sa.Column("created_order", sa.BigInteger()))
    op.add_column("video_service_job", sa.Column("heartbeat_at", sa.DateTime(timezone=True)))
    op.add_column("video_service_catalog_video", sa.Column("youtube_video_id", sa.Text()))

    connection = op.get_bind()
    rows = connection.execute(sa.text("""
        SELECT job_id, created_at, legacy_order
        FROM video_service_job
        ORDER BY created_at, legacy_order NULLS LAST, job_id
    """)).mappings().all()
    for order, row in enumerate(rows, start=1):
        connection.execute(sa.text("""
            UPDATE video_service_job SET created_order=:order WHERE job_id=:job_id
        """), {"order": order, "job_id": row["job_id"]})
    connection.execute(sa.text("""
        SELECT setval('video_service_job_order_seq', :value, :called)
    """), {"value": max(len(rows), 1), "called": bool(rows)})
    op.alter_column("video_service_job", "created_order", nullable=False,
                    server_default=sa.text("nextval('video_service_job_order_seq')"))
    op.alter_column("video_service_job", "fencing_token",
                    server_default=sa.text("nextval('video_service_job_fencing_seq')"))

    catalog_rows = connection.execute(sa.text("""
        SELECT video_id, source_url FROM video_service_catalog_video WHERE origin='youtube'
    """)).mappings().all()
    for row in catalog_rows:
        youtube_id = _youtube_id(row["source_url"])
        if youtube_id:
            connection.execute(sa.text("""
                UPDATE video_service_catalog_video SET youtube_video_id=:youtube_id WHERE video_id=:video_id
            """), {"youtube_id": youtube_id, "video_id": row["video_id"]})

    op.create_index("video_service_job_match_order", "video_service_job",
                    ["match_id", "created_at", "created_order"])
    op.create_index("video_service_job_created_order_unique", "video_service_job",
                    ["created_order"], unique=True)
    op.create_index("video_service_job_owner_active", "video_service_job",
                    ["owner_id", "lease_expires_at"],
                    postgresql_where=sa.text("status IN ('pending','downloading','calibrating','saving')"))
    op.create_index("video_service_catalog_youtube", "video_service_catalog_video", ["youtube_video_id"])
    op.create_check_constraint(
        "ck_video_service_job_lease_owner",
        "video_service_job",
        "(owner_id IS NULL AND lease_expires_at IS NULL AND heartbeat_at IS NULL) OR "
        "(owner_id IS NOT NULL AND lease_expires_at IS NOT NULL AND heartbeat_at IS NOT NULL)",
    )

    # Static catalogue rows remain immutable to the runtime role.
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'video_service_app') THEN
                GRANT CONNECT ON DATABASE video_service TO video_service_app;
                GRANT USAGE ON SCHEMA public TO video_service_app;
                REVOKE CREATE ON SCHEMA public FROM video_service_app;
                REVOKE ALL ON video_service_job, video_service_calibration,
                    video_service_catalog_video, video_service_association,
                    video_service_match_video_override FROM video_service_app;
                GRANT SELECT, INSERT, UPDATE ON video_service_job TO video_service_app;
                GRANT SELECT, INSERT ON video_service_calibration TO video_service_app;
                GRANT SELECT ON video_service_catalog_video TO video_service_app;
                GRANT SELECT, INSERT ON video_service_association TO video_service_app;
                GRANT SELECT, INSERT, UPDATE ON video_service_match_video_override TO video_service_app;
                GRANT USAGE ON SEQUENCE video_service_job_order_seq,
                    video_service_job_fencing_seq TO video_service_app;
            END IF;
        END $$
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'video_service_app') THEN
                REVOKE ALL ON video_service_job, video_service_calibration,
                    video_service_catalog_video, video_service_association,
                    video_service_match_video_override FROM video_service_app;
                REVOKE ALL ON SEQUENCE video_service_job_order_seq,
                    video_service_job_fencing_seq FROM video_service_app;
                GRANT SELECT ON video_service_job, video_service_calibration,
                    video_service_catalog_video, video_service_association,
                    video_service_match_video_override TO video_service_app;
            END IF;
        END $$
    """)
    op.drop_index("video_service_catalog_youtube", table_name="video_service_catalog_video")
    op.drop_index("video_service_job_owner_active", table_name="video_service_job")
    op.drop_constraint("ck_video_service_job_lease_owner", "video_service_job", type_="check")
    op.drop_index("video_service_job_created_order_unique", table_name="video_service_job")
    op.drop_index("video_service_job_match_order", table_name="video_service_job")
    op.drop_column("video_service_catalog_video", "youtube_video_id")
    op.drop_column("video_service_job", "heartbeat_at")
    op.drop_column("video_service_job", "created_order")
    op.alter_column("video_service_job", "fencing_token", server_default="0")
    op.execute("DROP SEQUENCE video_service_job_fencing_seq")
    op.execute("DROP SEQUENCE video_service_job_order_seq")
