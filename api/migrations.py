from pathlib import Path

from alembic import command
from alembic.config import Config


def upgrade_database(connection) -> None:
    config = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    config.attributes["connection"] = connection
    command.upgrade(config, "head")
