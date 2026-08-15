"""Persist tokenized OPNsense site-configuration generations."""

from alembic import op
import sqlalchemy as sa

revision = "0008_opnsense_config_generation"
down_revision = "0007_private_boot_certification"
branch_labels = None
depends_on = None


def _columns(table):
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    columns = _columns("vms")
    additions = {
        "opnsense_config_token": sa.Column("opnsense_config_token", sa.String(64), nullable=True),
        "opnsense_config_fingerprint": sa.Column("opnsense_config_fingerprint", sa.String(64), nullable=True),
        "opnsense_config_status": sa.Column("opnsense_config_status", sa.String(24), nullable=True),
        "opnsense_config_started_at": sa.Column("opnsense_config_started_at", sa.DateTime(), nullable=True),
        "opnsense_config_completed_at": sa.Column("opnsense_config_completed_at", sa.DateTime(), nullable=True),
    }
    with op.batch_alter_table("vms") as batch:
        for name, column in additions.items():
            if name not in columns:
                batch.add_column(column)


def downgrade():
    pass
