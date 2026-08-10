import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///ctf.db")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {},
)
SessionLocal = sessionmaker(bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    from api.models import (  # noqa: F401
        AccountToken, AdminAudit, Event, PlatformSettings, ServiceCredential, Team, User, VM,
        VMGoal, VMModule,
    )
    Base.metadata.create_all(bind=engine)

# Export models for use in other modules
