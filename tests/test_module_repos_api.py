from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, StaticPool
from sqlalchemy.orm import sessionmaker

from api.database import Base, get_db
from api.models import ModuleRepo, User
from api.main import app

_engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
_Session = sessionmaker(bind=_engine)


@pytest.fixture(scope="module", autouse=True)
def setup_db():
    Base.metadata.create_all(bind=_engine)
    yield
    Base.metadata.drop_all(bind=_engine)


@pytest.fixture(scope="module")
def admin_user():
    db = _Session()
    user = User(username="admin", password_hash="x", is_admin=True)
    db.add(user); db.commit(); db.refresh(user)
    yield user
    db.close()


@pytest.fixture()
def client(admin_user, monkeypatch):
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "test-key")

    def override_get_db():
        db = _Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with patch("api.routes.module_repos.require_admin", return_value=admin_user):
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c
    app.dependency_overrides.clear()


def test_list_empty(client):
    resp = client.get("/admin/api/module-repos")
    assert resp.status_code == 200
    assert resp.json() == {"repos": []}


def test_create_syncs_and_never_echoes_key(client):
    with patch("api.routes.module_repos.sync_repo") as mock_sync:
        resp = client.post("/admin/api/module-repos", json={
            "name": "Private", "repo_url": "git@github.com:org/x.git",
            "branch": "main", "ssh_key": "-----BEGIN OPENSSH PRIVATE KEY-----abc",
        })
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Private"
    assert body["status"] in ("synced", "error")
    assert body["has_key"] is True
    assert "ssh_key" not in body and "BEGIN OPENSSH" not in str(body)
    assert mock_sync.called


def test_sync_failure_sets_error(client):
    db = _Session()
    repo = ModuleRepo(name="Broken", repo_url="git@x", branch="main",
                      ssh_key_encrypted="enc:v1:abc")
    db.add(repo); db.commit(); db.refresh(repo)
    repo_id = repo.id
    db.close()
    with patch("api.routes.module_repos.sync_repo", side_effect=RuntimeError("boom")):
        resp = client.post(f"/admin/api/module-repos/{repo_id}/sync")
    assert resp.status_code == 200
    assert resp.json()["status"] == "error"
    assert "boom" in resp.json()["last_error"]


def test_delete_repo(client):
    db = _Session()
    repo = ModuleRepo(name="Gone", repo_url="git@x", branch="main",
                      ssh_key_encrypted="enc:v1:abc")
    db.add(repo); db.commit(); db.refresh(repo)
    repo_id = repo.id
    db.close()
    with patch("api.routes.module_repos._delete_repo_dir") as mock_del:
        resp = client.delete(f"/admin/api/module-repos/{repo_id}")
    assert resp.status_code == 204
    assert mock_del.called


def test_page_renders_for_admin(admin_user, monkeypatch):
    from api import main as main_module

    def override_get_db():
        db = _Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(main_module, "get_current_user", lambda request, db: admin_user)
    with TestClient(app, raise_server_exceptions=True) as c:
        resp = c.get("/admin/module-repos")
    app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert "Module repositories" in resp.text
