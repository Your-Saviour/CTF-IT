"""Add green-team infrastructure deployment persistence.

Revision ID: 0018_green_infrastructure
Revises: 0017_general_integrations
"""

from alembic import op
import sqlalchemy as sa


revision = "0018_green_infrastructure"
down_revision = "0017_general_integrations"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    vm_columns = {column["name"] for column in inspector.get_columns("vms")}
    team_column = next(column for column in inspector.get_columns("vms") if column["name"] == "team_id")
    if "green_key" not in vm_columns or not team_column["nullable"]:
        with op.batch_alter_table("vms") as batch_op:
            if "green_key" not in vm_columns:
                batch_op.add_column(sa.Column("green_key", sa.String(64), nullable=True))
            if not team_column["nullable"]:
                batch_op.alter_column("team_id", existing_type=sa.Integer(), nullable=True)
    vm_indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("vms")}
    if "uq_vms_event_green_key" not in vm_indexes:
        op.create_index(
            "uq_vms_event_green_key", "vms", ["event_id", "green_key"], unique=True,
            sqlite_where=sa.text("green_key IS NOT NULL"),
            postgresql_where=sa.text("green_key IS NOT NULL"),
        )

    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "green_deployment_facts" not in tables:
        op.create_table(
            "green_deployment_facts",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("event_id", sa.Integer(), sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=False),
            sa.Column("vm_key", sa.String(64), nullable=False),
            sa.Column("trait", sa.String(128), nullable=False),
            sa.Column("encrypted_value", sa.Text(), nullable=False),
            sa.Column("secret", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("event_id", "vm_key", "trait", name="uq_green_fact_scope"),
        )
        op.create_index("ix_green_facts_event_vm", "green_deployment_facts", ["event_id", "vm_key"])
    if "green_deployment_states" not in tables:
        op.create_table(
            "green_deployment_states",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("vm_id", sa.Integer(), sa.ForeignKey("vms.id", ondelete="CASCADE"), nullable=False),
            sa.Column("module_id", sa.String(128), nullable=False),
            sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
            sa.Column("current_step", sa.String(128), nullable=True),
            sa.Column("resolved_commit", sa.String(64), nullable=True),
            sa.Column("service_url", sa.String(512), nullable=True),
            sa.Column("health_status", sa.String(24), nullable=True),
            sa.Column("error_code", sa.String(64), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("vm_id", "module_id", name="uq_green_deployment_module"),
        )
        op.create_index("ix_green_deployments_vm", "green_deployment_states", ["vm_id"])
    for table in ("service_credentials", "integration_destinations"):
        columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}
        if "owner_green_vm_id" not in columns:
            with op.batch_alter_table(table) as batch_op:
                batch_op.add_column(sa.Column("owner_green_vm_id", sa.Integer(), nullable=True))
                batch_op.create_foreign_key(
                    f"fk_{table}_owner_green_vm", "vms", ["owner_green_vm_id"], ["id"],
                    ondelete="SET NULL",
                )


def downgrade():
    for table in ("integration_destinations", "service_credentials"):
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_constraint(f"fk_{table}_owner_green_vm", type_="foreignkey")
            batch_op.drop_column("owner_green_vm_id")
    op.drop_index("ix_green_deployments_vm", table_name="green_deployment_states")
    op.drop_table("green_deployment_states")
    op.drop_index("ix_green_facts_event_vm", table_name="green_deployment_facts")
    op.drop_table("green_deployment_facts")
    op.drop_index("uq_vms_event_green_key", table_name="vms")
    with op.batch_alter_table("vms") as batch_op:
        batch_op.drop_column("green_key")
        batch_op.alter_column("team_id", existing_type=sa.Integer(), nullable=False)
