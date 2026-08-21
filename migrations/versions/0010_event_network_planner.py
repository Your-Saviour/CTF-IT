"""Add event network planner layout and revision columns."""

from alembic import op
import sqlalchemy as sa

revision = "0010_event_network_planner"
down_revision = "0009_existing_feature_columns"
branch_labels = None
depends_on = None


def _columns(table):
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    columns = _columns("events")
    with op.batch_alter_table("events") as batch:
        if "infrastructure_layout" not in columns:
            batch.add_column(sa.Column("infrastructure_layout", sa.Text(), nullable=True))
        if "updated_at" not in columns:
            batch.add_column(sa.Column("updated_at", sa.DateTime(), nullable=True,
                                       server_default=sa.func.current_timestamp()))
    op.execute(sa.text(
        "UPDATE events SET updated_at = COALESCE(updated_at, created_at, CURRENT_TIMESTAMP)"
    ))
    if "updated_at" not in columns:
        with op.batch_alter_table("events") as batch:
            batch.alter_column("updated_at", nullable=False)


def downgrade():
    pass
