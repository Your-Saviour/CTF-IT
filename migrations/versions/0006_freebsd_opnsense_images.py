"""Automated FreeBSD-based OPNsense golden images."""

from alembic import op
import sqlalchemy as sa

revision = "0006_freebsd_opnsense_images"
down_revision = "0005_opnsense_images"
branch_labels = None
depends_on = None


def _columns(table):
    return {column["name"]: column for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    columns = _columns("opnsense_images")
    additions = {
        "second_test_instance_id": sa.Column("second_test_instance_id", sa.String(64), nullable=True),
        "validation_vpc_id": sa.Column("validation_vpc_id", sa.String(64), nullable=True),
        "build_method": sa.Column("build_method", sa.String(32), nullable=True),
        "base_os": sa.Column("base_os", sa.String(64), nullable=True),
        "bootstrap_source_url": sa.Column("bootstrap_source_url", sa.Text(), nullable=True),
        "bootstrap_sha256": sa.Column("bootstrap_sha256", sa.String(64), nullable=True),
        "validation_results": sa.Column("validation_results", sa.Text(), nullable=True),
    }
    with op.batch_alter_table("opnsense_images") as batch:
        for name, column in additions.items():
            if name not in columns:
                batch.add_column(column)
        for name in ("artifact_url", "checksum_url", "signature_url"):
            if name in columns and not columns[name]["nullable"]:
                batch.alter_column(name, existing_type=sa.Text(), nullable=True)


def downgrade():
    pass
