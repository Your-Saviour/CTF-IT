from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def test_fresh_database_upgrades_through_green_infrastructure(tmp_path):
    database = tmp_path / "fresh.db"
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    engine = create_engine(f"sqlite:///{database}")

    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")

    inspector = inspect(engine)
    assert "green_deployment_facts" in inspector.get_table_names()
    assert "green_deployment_states" in inspector.get_table_names()
    assert "green_key" in {column["name"] for column in inspector.get_columns("vms")}
