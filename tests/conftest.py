"""Isolation defaults shared by the complete test suite."""

import pytest


@pytest.fixture(autouse=True)
def isolated_agent_database(tmp_path, monkeypatch):
    from ai_agent.config import get_config
    from ai_agent.db import dispose_engine

    dispose_engine()
    get_config.cache_clear()
    monkeypatch.setenv("AGENT_DATABASE_URL", f"sqlite:///{tmp_path / 'agent.db'}")
    yield
    dispose_engine()
    get_config.cache_clear()
