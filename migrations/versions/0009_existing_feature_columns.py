"""Repair feature columns missing from upgrades of existing databases."""

from alembic import op
import sqlalchemy as sa

revision = "0009_existing_feature_columns"
down_revision = "0008_opnsense_config_generation"
branch_labels = None
depends_on = None


def _columns(table):
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    vm_columns = _columns("vms")
    if "ust_prompt" not in vm_columns:
        with op.batch_alter_table("vms") as batch:
            batch.add_column(sa.Column("ust_prompt", sa.Text(), nullable=True))

    event_columns = _columns("events")
    additions = {
        "expo_sync_status": sa.Column("expo_sync_status", sa.String(24), nullable=True),
        "expo_sync_last_error": sa.Column("expo_sync_last_error", sa.Text(), nullable=True),
        "expo_sync_attempts": sa.Column(
            "expo_sync_attempts", sa.Integer(), nullable=False, server_default="0"
        ),
        "expo_sync_completed_at": sa.Column("expo_sync_completed_at", sa.DateTime(), nullable=True),
    }
    with op.batch_alter_table("events") as batch:
        for name, column in additions.items():
            if name not in event_columns:
                batch.add_column(column)


def downgrade():
    pass
