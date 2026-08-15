"""Persist VPC-only stock-image certification and endpoint NIC identity."""

from alembic import op
import sqlalchemy as sa

revision = "0007_private_boot_certification"
down_revision = "0006_freebsd_opnsense_images"
branch_labels = None
depends_on = None


def _columns(table):
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    from api.database import Base
    import api.models  # noqa: F401

    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)
    columns = _columns("vms")
    additions = {
        "vpc_mac": sa.Column("vpc_mac", sa.String(32), nullable=True),
        "network_boot_id": sa.Column("network_boot_id", sa.String(128), nullable=True),
        "network_phase": sa.Column("network_phase", sa.String(32), nullable=True),
    }
    with op.batch_alter_table("vms") as batch:
        for name, column in additions.items():
            if name not in columns:
                batch.add_column(column)
    site_columns = _columns("sites")
    if "control_plane_status" not in site_columns:
        with op.batch_alter_table("sites") as batch:
            batch.add_column(sa.Column(
                "control_plane_status", sa.String(24), nullable=False,
                server_default="pending",
            ))


def downgrade():
    pass
