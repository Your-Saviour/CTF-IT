"""Add operation_runs and operation_run_steps tables.

Revision ID: 0016_operation_runs
Revises: 0015_module_repos
"""

from alembic import op

revision = "0016_operation_runs"
down_revision = "0015_module_repos"
branch_labels = None
depends_on = None


def upgrade():
    from api.database import Base
    import api.models  # noqa: F401
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)


def downgrade():
    op.drop_table("operation_run_steps")
    op.drop_table("operation_runs")
