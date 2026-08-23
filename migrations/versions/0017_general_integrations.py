"""Add generic outbound integration persistence.

Revision ID: 0017_general_integrations
Revises: 0010_aws_provider
"""

from alembic import op
import sqlalchemy as sa


revision = "0017_general_integrations"
down_revision = "0010_aws_provider"
branch_labels = None
depends_on = None


def _drop_legacy_event_columns():
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("events")}
    obsolete = [
        "expo_sync_status", "expo_sync_last_error",
        "expo_sync_attempts", "expo_sync_completed_at",
    ]
    if existing.intersection(obsolete):
        with op.batch_alter_table("events") as batch_op:
            for column in obsolete:
                if column in existing:
                    batch_op.drop_column(column)


def upgrade():
    if sa.inspect(op.get_bind()).has_table("integration_destinations"):
        _drop_legacy_event_columns()
        return
    op.create_table(
        "integration_destinations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("adapter_key", sa.String(64), nullable=False),
        sa.Column("base_url", sa.String(512), nullable=False),
        sa.Column("credential_id", sa.Integer(), sa.ForeignKey("service_credentials.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("allow_insecure_http", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("config_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("last_test_status", sa.String(24), nullable=True),
        sa.Column("last_test_error", sa.Text(), nullable=True),
        sa.Column("last_tested_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("name", name="uq_integration_destinations_name"),
    )
    op.create_table(
        "event_integrations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("destination_id", sa.Integer(), sa.ForeignKey("integration_destinations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_success_at", sa.DateTime(), nullable=True),
        sa.Column("last_status", sa.String(24), nullable=True),
        sa.Column("last_error_code", sa.String(64), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("event_id", "destination_id", name="uq_event_integration_destination"),
    )
    op.create_table(
        "integration_sync_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("binding_id", sa.Integer(), sa.ForeignKey("event_integrations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("trigger_reason", sa.String(64), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("claimed_at", sa.DateTime(), nullable=True),
        sa.Column("claim_token", sa.String(64), nullable=True),
        sa.Column("follow_up_required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_integration_jobs_due", "integration_sync_jobs", ["status", "next_attempt_at", "priority"])
    op.create_index(
        "uq_integration_jobs_active_binding", "integration_sync_jobs", ["binding_id"], unique=True,
        sqlite_where=sa.text("status IN ('pending', 'running', 'retrying')"),
        postgresql_where=sa.text("status IN ('pending', 'running', 'retrying')"),
    )
    op.create_table(
        "integration_sync_attempts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("integration_sync_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("binding_id", sa.Integer(), sa.ForeignKey("event_integrations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("result", sa.String(24), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("message", sa.String(500), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_integration_attempts_binding_created", "integration_sync_attempts", ["binding_id", "created_at"])
    _drop_legacy_event_columns()


def downgrade():
    with op.batch_alter_table("events") as batch_op:
        batch_op.add_column(sa.Column("expo_sync_status", sa.String(24), nullable=True))
        batch_op.add_column(sa.Column("expo_sync_last_error", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("expo_sync_attempts", sa.Integer(), server_default="0", nullable=False))
        batch_op.add_column(sa.Column("expo_sync_completed_at", sa.DateTime(), nullable=True))
    op.drop_table("integration_sync_attempts")
    op.drop_table("integration_sync_jobs")
    op.drop_table("event_integrations")
    op.drop_table("integration_destinations")
