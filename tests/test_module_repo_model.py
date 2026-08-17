from sqlalchemy import create_engine, StaticPool
from sqlalchemy.orm import sessionmaker

from api.database import Base
from api.models import ModuleRepo


def test_module_repo_round_trip():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    repo = ModuleRepo(name="Private Modules", repo_url="git@github.com:org/private.git",
                      branch="main", ssh_key_encrypted="enc:v1:abc")
    db.add(repo); db.commit(); db.refresh(repo)
    assert repo.id is not None
    assert repo.status == "pending"
    assert repo.branch == "main"
    assert repo.last_sync_at is None
    db.close()
    Base.metadata.drop_all(bind=engine)
