"""Replace flat VM quotas with multi-site GameNet infrastructure."""

from alembic import op
import sqlalchemy as sa

revision = "0004_gamenet_infrastructure"
down_revision = "0003_training_analytics_indexes"
branch_labels = None
depends_on = None


def _columns(table):
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    # New runtime tables are defined by the same metadata used for clean
    # installs. checkfirst keeps this compatible with databases created from a
    # newer baseline during development.
    from api.database import Base
    import api.models  # noqa: F401
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)

    event_columns = _columns("events")
    if "infrastructure" not in event_columns:
        with op.batch_alter_table("events") as batch:
            batch.add_column(sa.Column("infrastructure", sa.Text(), nullable=True))
    if "vm_quota" in event_columns:
        with op.batch_alter_table("events") as batch:
            batch.drop_column("vm_quota")

    site_columns = _columns("sites")
    with op.batch_alter_table("sites") as batch:
        if "tunnel_public_key" not in site_columns:
            batch.add_column(sa.Column("tunnel_public_key", sa.Text()))
        if "tunnel_private_key_encrypted" not in site_columns:
            batch.add_column(sa.Column("tunnel_private_key_encrypted", sa.Text()))
        if "tunnel_address" not in site_columns:
            batch.add_column(sa.Column("tunnel_address", sa.String(45)))

    gateway_columns = _columns("team_vpn_gateways")
    if "platform_address" not in gateway_columns:
        with op.batch_alter_table("team_vpn_gateways") as batch:
            batch.add_column(sa.Column("platform_address", sa.String(45)))

    vm_columns = _columns("vms")
    additions = {
        "role": sa.Column("role", sa.String(24)),
        "site_id": sa.Column("site_id", sa.Integer()),
        "zone_id": sa.Column("zone_id", sa.Integer()),
        "public_ip": sa.Column("public_ip", sa.String(45)),
        "private_ip": sa.Column("private_ip", sa.String(45)),
    }
    with op.batch_alter_table("vms") as batch:
        for name, column in additions.items():
            if name not in vm_columns:
                batch.add_column(column)
        if "site_id" not in vm_columns:
            batch.create_foreign_key("fk_vms_site_id", "sites", ["site_id"], ["id"])
        if "zone_id" not in vm_columns:
            batch.create_foreign_key("fk_vms_zone_id", "zones", ["zone_id"], ["id"])


def downgrade():
    pass
