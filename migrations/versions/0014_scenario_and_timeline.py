"""Add scenarios table and event timeline/provenance columns.

Revision ID: 0014_scenario_and_timeline
Revises: 0013_multiple_event_operations
"""

from alembic import op
import sqlalchemy as sa


revision = "0014_scenario_and_timeline"
down_revision = "0013_multiple_event_operations"
branch_labels = None
depends_on = None


def _columns(table):
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    from api.database import Base
    import api.models  # noqa: F401
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)
    columns = _columns("events")
    with op.batch_alter_table("events") as batch:
        if "timeline" not in columns:
            batch.add_column(sa.Column("timeline", sa.Text(), nullable=True))
        if "scenario_id" not in columns:
            batch.add_column(sa.Column("scenario_id", sa.Integer(), nullable=True))
            batch.create_foreign_key("fk_events_scenario_id", "scenarios", ["scenario_id"], ["id"])
        if "scenario_version" not in columns:
            batch.add_column(sa.Column("scenario_version", sa.Integer(), nullable=True))
        if "scenario_fingerprint" not in columns:
            batch.add_column(sa.Column("scenario_fingerprint", sa.String(length=64), nullable=True))


def downgrade():
    columns = _columns("events")
    with op.batch_alter_table("events") as batch:
        if "scenario_id" in columns:
            batch.drop_constraint("fk_events_scenario_id", type_="foreignkey")
        for column in ("timeline", "scenario_id", "scenario_version", "scenario_fingerprint"):
            if column in columns:
                batch.drop_column(column)
    if "scenarios" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("scenarios")
