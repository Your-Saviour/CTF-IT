"""Managed OPNsense image lifecycle and firewall provenance."""

from alembic import op
import sqlalchemy as sa

revision = "0005_opnsense_images"
down_revision = "0004_gamenet_infrastructure"
branch_labels = None
depends_on = None


def _columns(table):
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    from api.database import Base
    import api.models  # noqa: F401
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)
    columns = _columns("vms")
    with op.batch_alter_table("vms") as batch:
        if "opnsense_image_id" not in columns:
            batch.add_column(sa.Column("opnsense_image_id", sa.Integer(), nullable=True))
            batch.create_foreign_key("fk_vms_opnsense_image_id", "opnsense_images", ["opnsense_image_id"], ["id"])
        if "opnsense_release" not in columns:
            batch.add_column(sa.Column("opnsense_release", sa.String(16), nullable=True))
        if "opnsense_snapshot_id" not in columns:
            batch.add_column(sa.Column("opnsense_snapshot_id", sa.String(64), nullable=True))


def downgrade():
    pass
