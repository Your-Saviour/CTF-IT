"""add event module plan

Revision ID: 0011_event_module_plan
Revises: 0010_event_network_planner
"""
from alembic import op
import sqlalchemy as sa

revision = "0011_event_module_plan"
down_revision = "0010_event_network_planner"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("events", sa.Column("module_plan", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("events", "module_plan")
