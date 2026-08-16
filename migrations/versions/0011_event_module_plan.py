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
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("events")}
    if "module_plan" not in columns:
        with op.batch_alter_table("events") as batch:
            batch.add_column(sa.Column("module_plan", sa.Text(), nullable=True))


def downgrade():
    pass
