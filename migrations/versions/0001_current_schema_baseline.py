"""Establish the pre-training-release schema as the migration baseline."""

revision = "0001_current_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # A fresh installation can be built entirely through Alembic. Existing
    # installations are left untouched and the next revision performs the
    # compatibility-safe column/backfill work.
    from alembic import op
    from api.database import Base
    import api.models  # noqa: F401
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)


def downgrade():
    pass
