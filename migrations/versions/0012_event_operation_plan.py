"""Add provider-neutral event operation plans.

Revision ID: 0012_event_operation_plan
Revises: 0011_event_module_plan
"""

from alembic import op
import sqlalchemy as sa

revision = "0012_event_operation_plan"
down_revision = "0011_event_module_plan"
branch_labels = None
depends_on = None


def upgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("events")}
    if "operation_plan" not in columns:
        with op.batch_alter_table("events") as batch:
            batch.add_column(sa.Column("operation_plan", sa.Text(), nullable=True))


def downgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("events")}
    if "operation_plan" in columns:
        with op.batch_alter_table("events") as batch:
            batch.drop_column("operation_plan")
