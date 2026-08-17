"""Add module_repos table.

Revision ID: 0015_module_repos
Revises: 0014_scenario_and_timeline
"""

from alembic import op

revision = "0015_module_repos"
down_revision = "0014_scenario_and_timeline"
branch_labels = None
depends_on = None


def upgrade():
    from api.database import Base
    import api.models  # noqa: F401
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)


def downgrade():
    op.drop_table("module_repos")
