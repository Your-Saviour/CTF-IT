from __future__ import annotations

import os
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from ai_agent.config import get_config

_engine = None
_session_factory = None


def get_engine():
    global _engine
    if _engine is None:
        config = get_config()
        db_path = config.DATABASE_URL.replace("sqlite:///", "")
        if db_path in ("sqlite://", ""):
            db_path = ":memory:"
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        _engine = create_engine(config.DATABASE_URL, future=True, pool_pre_ping=True)
        from ai_agent.db.models import Base
        print(f"DEBUG: Creating tables for database: {db_path}")
        Base.metadata.create_all(bind=_engine)
        print(f"DEBUG: Tables created: {Base.metadata.tables.keys()}")
    return _engine


def get_session_factory():
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _session_factory


@contextmanager
def get_db():
    db = get_session_factory()()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db():
    from ai_agent.db.models import Base

    Base.metadata.create_all(bind=get_engine())


def dispose_engine():
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
        _engine = None
        _session_factory = None
