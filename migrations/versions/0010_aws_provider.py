"""Add provider-neutral AWS resource metadata without rewriting legacy IDs."""

from alembic import op
import sqlalchemy as sa

revision = "0010_aws_provider"
down_revision = "0009_existing_feature_columns"
branch_labels = None
depends_on = None


def _columns(table):
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def _add_missing(table, additions):
    existing = _columns(table)
    with op.batch_alter_table(table) as batch:
        for name, column in additions.items():
            if name not in existing:
                batch.add_column(column)


def upgrade():
    _add_missing("vms", {
        "cloud_instance_id": sa.Column("cloud_instance_id", sa.String(64), nullable=True),
        "instance_type": sa.Column("instance_type", sa.String(64), nullable=True),
        "cloud_region": sa.Column("cloud_region", sa.String(32), nullable=True),
        "availability_zone": sa.Column("availability_zone", sa.String(32), nullable=True),
        "primary_eni_id": sa.Column("primary_eni_id", sa.String(64), nullable=True),
        "wan_eni_id": sa.Column("wan_eni_id", sa.String(64), nullable=True),
        "lan_eni_id": sa.Column("lan_eni_id", sa.String(64), nullable=True),
        "subnet_id": sa.Column("subnet_id", sa.String(64), nullable=True),
        "security_group_ids_json": sa.Column("security_group_ids_json", sa.Text(), nullable=True),
        "eip_allocation_id": sa.Column("eip_allocation_id", sa.String(64), nullable=True),
    })
    _add_missing("sites", {
        "availability_zone": sa.Column("availability_zone", sa.String(32), nullable=True),
        "public_subnet_id": sa.Column("public_subnet_id", sa.String(64), nullable=True),
        "infrastructure_subnet_id": sa.Column("infrastructure_subnet_id", sa.String(64), nullable=True),
        "internet_gateway_id": sa.Column("internet_gateway_id", sa.String(64), nullable=True),
        "route_table_ids_json": sa.Column("route_table_ids_json", sa.Text(), nullable=True),
    })
    _add_missing("opnsense_images", {
        "ami_id": sa.Column("ami_id", sa.String(64), nullable=True),
        "backing_snapshot_ids_json": sa.Column("backing_snapshot_ids_json", sa.Text(), nullable=True),
        "region": sa.Column("region", sa.String(32), nullable=True),
        "availability_zone": sa.Column("availability_zone", sa.String(32), nullable=True),
        "builder_subnet_id": sa.Column("builder_subnet_id", sa.String(64), nullable=True),
        "validation_subnet_id": sa.Column("validation_subnet_id", sa.String(64), nullable=True),
    })


def downgrade():
    pass
