"""Indexes supporting event-scoped training analytics queries."""

from alembic import op

revision = "0003_training_analytics_indexes"
down_revision = "0002_training_readiness"
branch_labels = None
depends_on = None


def upgrade():
    import sqlalchemy as sa
    inspector = sa.inspect(op.get_bind())
    indexes = {table: {item["name"] for item in inspector.get_indexes(table)} for table in (
        "vms", "vm_modules", "verification_attempts", "hint_reveals"
    )}
    definitions = (
        ("ix_vms_event_team", "vms", ["event_id", "team_id"]),
        ("ix_vm_modules_vm_module", "vm_modules", ["vm_id", "module_id"]),
        ("ix_verification_attempts_assignment_created", "verification_attempts", ["module_assignment_id", "created_at", "id"]),
        ("ix_hint_reveals_assignment_revealed", "hint_reveals", ["module_assignment_id", "revealed_at"]),
    )
    for name, table, columns in definitions:
        if name not in indexes[table]:
            op.create_index(name, table, columns)


def downgrade():
    import sqlalchemy as sa
    inspector = sa.inspect(op.get_bind())
    for name, table in (
        ("ix_hint_reveals_assignment_revealed", "hint_reveals"),
        ("ix_verification_attempts_assignment_created", "verification_attempts"),
        ("ix_vm_modules_vm_module", "vm_modules"),
        ("ix_vms_event_team", "vms"),
    ):
        if name in {item["name"] for item in inspector.get_indexes(table)}:
            op.drop_index(name, table_name=table)
