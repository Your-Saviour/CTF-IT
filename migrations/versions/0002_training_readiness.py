"""Team learner access, verification state, attempts and hint reveals."""

from alembic import op
import sqlalchemy as sa

revision = "0002_training_readiness"
down_revision = "0001_current_schema"
branch_labels = None
depends_on = None


def _columns(table):
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    inspector = sa.inspect(op.get_bind())
    users = _columns("users")
    if "team_id" not in users:
        with op.batch_alter_table("users") as batch:
            batch.add_column(sa.Column("team_id", sa.Integer(), nullable=True))
            batch.create_foreign_key("fk_users_team_id", "teams", ["team_id"], ["id"])
    tokens = _columns("account_tokens")
    if "team_id" not in tokens:
        with op.batch_alter_table("account_tokens") as batch:
            batch.add_column(sa.Column("team_id", sa.Integer(), nullable=True))
            batch.create_foreign_key("fk_account_tokens_team_id", "teams", ["team_id"], ["id"])

    module_columns = _columns("vm_modules")
    additions = {
        "status": sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        "last_verified_at": sa.Column("last_verified_at", sa.DateTime(), nullable=True),
        "first_completed_at": sa.Column("first_completed_at", sa.DateTime(), nullable=True),
        "completed_by_id": sa.Column("completed_by_id", sa.Integer(), nullable=True),
        "verification_error_code": sa.Column("verification_error_code", sa.String(64), nullable=True),
        "verification_baseline_json": sa.Column("verification_baseline_json", sa.Text(), nullable=True),
    }
    with op.batch_alter_table("vm_modules") as batch:
        for name, column in additions.items():
            if name not in module_columns:
                batch.add_column(column)
        if "completed_by_id" not in module_columns:
            batch.create_foreign_key("fk_vm_modules_completed_by", "users", ["completed_by_id"], ["id"])
    op.execute("UPDATE vm_modules SET status = CASE WHEN completed THEN 'completed' ELSE 'open' END")
    op.execute("UPDATE vm_modules SET first_completed_at = completed_at WHERE completed AND first_completed_at IS NULL")
    op.execute("UPDATE vm_modules SET stage = 'preapplied' WHERE module_type = 'hardening' AND stage IS NULL")

    if not inspector.has_table("team_training_credentials"):
        op.create_table("team_training_credentials",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("team_id", sa.Integer(), sa.ForeignKey("teams.id"), nullable=False, unique=True),
            sa.Column("username", sa.String(64), nullable=False, server_default="ctf-trainee"),
            sa.Column("private_key_encrypted", sa.Text(), nullable=False),
            sa.Column("public_key", sa.Text(), nullable=False),
            sa.Column("sudo_password_encrypted", sa.Text(), nullable=False),
            sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
            sa.Column("provisioned_vm_ids_json", sa.Text()),
            sa.Column("last_error_code", sa.String(64)),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("rotated_at", sa.DateTime()),
        )
    if not inspector.has_table("verification_attempts"):
        op.create_table("verification_attempts",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("module_assignment_id", sa.Integer(), sa.ForeignKey("vm_modules.id"), nullable=False),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id")),
            sa.Column("trigger_type", sa.String(16), nullable=False),
            sa.Column("result", sa.String(24), nullable=False),
            sa.Column("safe_summary", sa.String(256), nullable=False),
            sa.Column("error_code", sa.String(64)),
            sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
    if not inspector.has_table("hint_reveals"):
        op.create_table("hint_reveals",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("module_assignment_id", sa.Integer(), sa.ForeignKey("vm_modules.id"), nullable=False),
            sa.Column("hint_index", sa.Integer(), nullable=False),
            sa.Column("revealed_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("user_id", "module_assignment_id", "hint_index", name="uq_hint_reveal"),
        )

    # Safe automatic assignment only where event membership is unambiguous.
    op.execute("""
        UPDATE users SET team_id = (
            SELECT MIN(teams.id) FROM teams WHERE teams.event_id = users.event_id
        ) WHERE is_admin = false AND team_id IS NULL AND 1 = (
            SELECT COUNT(*) FROM teams WHERE teams.event_id = users.event_id
        )
    """)


def downgrade():
    op.drop_table("hint_reveals")
    op.drop_table("verification_attempts")
    op.drop_table("team_training_credentials")
