"""Store multiple independent operation plans per event.

Revision ID: 0013_multiple_event_operations
Revises: 0012_event_operation_plan
"""

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision = "0013_multiple_event_operations"
down_revision = "0012_event_operation_plan"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "event_operations" not in inspector.get_table_names():
        op.create_table(
            "event_operations",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("event_id", sa.Integer(), sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=False),
            sa.Column("name", sa.String(length=128), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("operation_plan", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("event_id", "name", name="uq_event_operations_event_name"),
        )
        op.create_index(
            "ix_event_operations_event_position", "event_operations", ["event_id", "position"]
        )

    event_columns = {column["name"] for column in sa.inspect(bind).get_columns("events")}
    if "operation_plan" not in event_columns:
        return
    events = sa.table("events", sa.column("id", sa.Integer), sa.column("operation_plan", sa.Text))
    operations = sa.table(
        "event_operations",
        sa.column("event_id", sa.Integer),
        sa.column("name", sa.String),
        sa.column("description", sa.Text),
        sa.column("position", sa.Integer),
        sa.column("operation_plan", sa.Text),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
    )
    now = datetime.now(timezone.utc)
    for event_id, plan in bind.execute(
        sa.select(events.c.id, events.c.operation_plan).where(events.c.operation_plan.is_not(None))
    ):
        exists = bind.execute(
            sa.select(sa.func.count()).select_from(operations).where(operations.c.event_id == event_id)
        ).scalar_one()
        if not exists:
            bind.execute(operations.insert().values(
                event_id=event_id,
                name="Operation 1",
                description=None,
                position=0,
                operation_plan=plan,
                created_at=now,
                updated_at=now,
            ))


def downgrade():
    if "event_operations" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("event_operations")
