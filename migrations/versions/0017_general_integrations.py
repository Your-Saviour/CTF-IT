"""Add generic outbound integration persistence.

Revision ID: 0017_general_integrations
Revises: 0016_operation_runs
"""

from alembic import op
import sqlalchemy as sa


revision = "0017_general_integrations"
down_revision = "0016_operation_runs"
branch_labels = None
depends_on = None


def upgrade():
    from api.database import Base
    import api.models  # noqa: F401

    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)
    with op.batch_alter_table("events") as batch_op:
        batch_op.drop_column("expo_sync_status")
        batch_op.drop_column("expo_sync_last_error")
        batch_op.drop_column("expo_sync_attempts")
        batch_op.drop_column("expo_sync_completed_at")


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
