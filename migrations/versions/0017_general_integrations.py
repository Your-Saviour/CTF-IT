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


def downgrade():
    op.drop_table("integration_sync_attempts")
    op.drop_table("integration_sync_jobs")
    op.drop_table("event_integrations")
    op.drop_table("integration_destinations")
