# External Module Repositories Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let administrators attach private git repositories (via SSH key, configured in the admin UI) whose modules merge additively into the existing module catalogue.

**Architecture:** `load_all_modules()` scans multiple roots (built-in `modules/` plus cloned repos under a persistent `MODULE_REPOS_DIR`). A new `ModuleRepo` model stores repo URL/branch/encrypted key; a pure `builder/module_repo.py` service does clone-fresh → validate → atomic swap; a new admin router + page manage repos and sync; `start_event` syncs repos before module selection.

**Tech Stack:** FastAPI + SQLAlchemy (SQLite/Postgres), Alembic, Jinja2 templates + vanilla JS, pytest, git + OpenSSH client (subprocess).

**Spec:** `docs/superpowers/specs/2026-08-17-external-module-repositories-design.md`

## Global Constraints

- Private repos mirror the built-in `modules/` layout: `<type>/<module_id>/<module_id>.yaml` (+ scripts/files) at the repo root.
- Module IDs remain globally unique across all roots (existing loader composition stays intact).
- All new API routes are admin-only via `require_admin`.
- SSH keys are encrypted at rest with `encrypt_secret`/`decrypt_secret` (`DATA_ENCRYPTION_KEY`); never logged, never returned by any API.
- Sync is clone-fresh → validate → atomic swap; a broken repo must never leave `load_all_modules()` raising.
- Tests run in the disposable Docker test service (`docker compose --profile test run --rm tests`); individual files runnable with `pytest tests/test_*.py -v`.

---

### Task 1: Multi-root module loader

**Files:**
- Modify: `builder/module_loader.py:150-204`
- Test: `tests/test_module_loader.py` (append new test class)

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `MODULE_REPOS_DIR = Path(os.environ.get("MODULE_REPOS_DIR", "/app/module_repos"))`
  - `module_from_yaml(yaml_path: Path) -> Module` (extracted from `load_all_modules`; reused by Task 3 validation)
  - `_module_roots() -> list[Path]` (built-in `modules/` plus each subdir of `MODULE_REPOS_DIR`)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_module_loader.py`:

```python
class TestExternalRepos:
    def test_loads_modules_from_extra_roots(self, tmp_path, monkeypatch):
        import builder.module_loader as ml
        repo = tmp_path / "repo"
        (repo / "vulns" / "secret_backdoor").mkdir(parents=True)
        (repo / "vulns" / "secret_backdoor" / "secret_backdoor.yaml").write_text(
            "id: secret_backdoor\nname: Secret Backdoor\ndescription: d\n"
            "type: vulnerability\ndifficulty: hard\npoints: 200\ncategory: persistence\n"
        )
        monkeypatch.setattr(ml, "MODULE_REPOS_DIR", repo)
        ids = {m.id for m in ml.load_all_modules()}
        assert "secret_backdoor" in ids

    def test_skips_dot_git(self, tmp_path, monkeypatch):
        import builder.module_loader as ml
        repo = tmp_path / "repo"
        (repo / ".git").mkdir(parents=True)
        (repo / ".git" / "config.yaml").write_text("id: should_not_load\n")
        monkeypatch.setattr(ml, "MODULE_REPOS_DIR", repo)
        ids = {m.id for m in ml.load_all_modules()}
        assert "should_not_load" not in ids
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_module_loader.py::TestExternalRepos -v`
Expected: FAIL — `MODULE_REPOS_DIR` does not exist (`AttributeError`) and `secret_backdoor`/`should_not_load` are not loaded.

- [ ] **Step 3: Implement the multi-root loader**

In `builder/module_loader.py`, add `import os` to the top imports (after `from pathlib import Path`). Then replace the `MODULES_DIR = ...` block and the `load_all_modules` function (lines 150-204) with:

```python
MODULES_DIR = Path(__file__).resolve().parent.parent / "modules"
MODULE_REPOS_DIR = Path(os.environ.get("MODULE_REPOS_DIR", "/app/module_repos"))


def module_from_yaml(yaml_path: Path) -> Module:
    with open(yaml_path) as f:
        data = yaml.safe_load(f)
    module_type = data["type"]
    return Module(
        id=data["id"],
        name=data["name"],
        description=data["description"],
        type=module_type,
        difficulty=data["difficulty"],
        points=data["points"],
        category=data["category"],
        tags=data.get("tags", []),
        conflicts=data.get("conflicts", []),
        requires=data.get("requires", []),
        script=data.get("script"),
        steps=_parse_steps(data),
        verification=data.get("verification", {}),
        hints=data.get("hints", []),
        learning_objectives=data.get("learning_objectives", []),
        estimated_minutes=data.get("estimated_minutes", 0),
        prerequisites=data.get("prerequisites", data.get("requires", [])),
        phases=data.get("phases", []),
        narrative=data.get("narrative", ""),
        references=_parse_references(data.get("references", [])),
        debrief=data.get("debrief", {}),
        suggested_fix=data.get("suggested_fix"),
        caldera=data.get("caldera"),
        source_dir=yaml_path.parent,
        disabled=bool(data.get("disabled", False)),
        min_ram_mb=data.get("min_ram_mb", 0),
        min_vcpu=data.get("min_vcpu", 0),
        supported_bases=data.get("supported_bases", []),
        stage=data.get("stage"),
        red_points=data.get("red_points", 0),
        defend_points=data.get("defend_points", 0),
        revert_verification=data.get("revert_verification", {}),
    )


def _module_roots() -> list[Path]:
    roots = [MODULES_DIR]
    if MODULE_REPOS_DIR.is_dir():
        roots.extend(sorted(p for p in MODULE_REPOS_DIR.iterdir() if p.is_dir()))
    return roots


def load_all_modules() -> list[Module]:
    modules = []
    for root in _module_roots():
        for yaml_path in sorted(root.rglob("*.yaml")):
            if ".git" in yaml_path.parts:
                continue
            modules.append(module_from_yaml(yaml_path))
    # Dependent remediation must preserve its application foundation. Compose
    # that health contract automatically so catalogue authors cannot silently
    # ship a file-only check that rewards breaking the service.
    by_id = {module.id: module for module in modules}
    for module in modules:
        foundations = [by_id[item] for item in module.requires
                       if item in by_id and by_id[item].type in {"application_external", "application_internal"}]
        if module.type in {"vulnerability", "hardening", "payload"} and foundations and module.verification:
            health_checks = [foundation.verification for foundation in foundations if foundation.verification]
            if health_checks:
                module.verification = {"type": "all_of", "checks": [module.verification, *health_checks]}
    return modules
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_module_loader.py -v`
Expected: PASS (all existing loader tests plus the two new ones).

- [ ] **Step 5: Commit**

```bash
git add builder/module_loader.py tests/test_module_loader.py
git commit -m "feat: scan external module repository roots in loader"
```

---

### Task 2: `ModuleRepo` model + migration

**Files:**
- Modify: `api/models.py` (add class)
- Create: `migrations/versions/0015_module_repos.py`
- Test: `tests/test_module_repo_model.py`

**Interfaces:**
- Consumes: `utcnow` (already in `api.models`).
- Produces: `ModuleRepo` with columns `id`, `name`, `repo_url`, `branch`, `ssh_key_encrypted`, `status`, `last_sync_at`, `last_error`, `created_at`, `updated_at`.

- [ ] **Step 1: Write the failing test**

`tests/test_module_repo_model.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_module_repo_model.py -v`
Expected: FAIL — `ImportError: cannot import name 'ModuleRepo'`.

- [ ] **Step 3: Add the model**

In `api/models.py`, append the class (after `VMGoal` / before `ServiceCredential` is fine; placement only needs to be inside the module):

```python
class ModuleRepo(Base):
    """An external git repository that contributes additional modules."""

    __tablename__ = "module_repos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    repo_url: Mapped[str] = mapped_column(String(512), nullable=False)
    branch: Mapped[str] = mapped_column(String(128), nullable=False, default="main")
    ssh_key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    last_sync_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
```

- [ ] **Step 4: Write the migration**

`migrations/versions/0015_module_repos.py`:

```python
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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_module_repo_model.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add api/models.py migrations/versions/0015_module_repos.py tests/test_module_repo_model.py
git commit -m "feat: add ModuleRepo model and migration"
```

---

### Task 3: Git sync service

**Files:**
- Create: `builder/module_repo.py`
- Modify: `Dockerfile` (add `git` + `openssh-client` to the `base` stage so both `test` and `runtime` images have them)
- Test: `tests/test_module_repo.py`

**Interfaces:**
- Consumes: `builder.module_loader.module_from_yaml` (Task 1), `api.services.secrets.decrypt_secret` (existing).
- Produces:
  - `MODULE_REPOS_DIR = Path(os.environ.get("MODULE_REPOS_DIR", "/app/module_repos"))`
  - `repo_dir(repo_id: int) -> Path`
  - `sync_repo(repo) -> None` (raises on clone/validation failure)
  - `delete_repo(repo) -> None`

- [ ] **Step 1: Add git to the base image**

In `Dockerfile`, change the `base` stage:

```dockerfile
FROM python:3.12.13-slim-trixie@sha256:423ed6ab25b1921a477529254bfeeabf5855151dc2c3141699a1bfc852199fbf AS base

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && apt-get update \
    && apt-get install -y --no-install-recommends git openssh-client \
    && rm -rf /var/lib/apt/lists/*
```

- [ ] **Step 2: Write the failing test**

`tests/test_module_repo.py`:

```python
import subprocess
from types import SimpleNamespace

import pytest

import builder.module_repo as mr


def _make_local_repo(tmp_path, module_id="secret_backdoor"):
    src = tmp_path / "src"
    (src / "vulns" / module_id).mkdir(parents=True)
    (src / "vulns" / module_id / f"{module_id}.yaml").write_text(
        f"id: {module_id}\nname: Secret\ndescription: d\ntype: vulnerability\n"
        f"difficulty: hard\npoints: 100\ncategory: persistence\n"
    )
    subprocess.run(["git", "init", "-b", "master"], cwd=src, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=src, check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "init"],
                   cwd=src, check=True, capture_output=True)
    return src.as_uri()


def _repo(tmp_path, url, repo_id=7):
    return SimpleNamespace(id=repo_id, branch="master", repo_url=url, ssh_key_encrypted="enc:v1:x")


def test_sync_clones_to_repo_dir(tmp_path, monkeypatch):
    url = _make_local_repo(tmp_path)
    repos = tmp_path / "repos"
    monkeypatch.setattr(mr, "MODULE_REPOS_DIR", repos)
    monkeypatch.setattr(mr, "decrypt_secret", lambda v: "dummy")
    mr.sync_repo(_repo(tmp_path, url))
    assert (repos / "7" / "vulns" / "secret_backdoor" / "secret_backdoor.yaml").exists()


def test_sync_rejects_malformed_module(tmp_path, monkeypatch):
    src = tmp_path / "src"
    (src / "vulns" / "bad").mkdir(parents=True)
    # missing `points` key -> KeyError wrapped as ValueError
    (src / "vulns" / "bad" / "bad.yaml").write_text(
        "id: bad\nname: Bad\ndescription: d\ntype: vulnerability\ndifficulty: hard\ncategory: c\n"
    )
    subprocess.run(["git", "init", "-b", "master"], cwd=src, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=src, check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "init"],
                   cwd=src, check=True, capture_output=True)
    repos = tmp_path / "repos"
    monkeypatch.setattr(mr, "MODULE_REPOS_DIR", repos)
    monkeypatch.setattr(mr, "decrypt_secret", lambda v: "dummy")
    with pytest.raises(ValueError):
        mr.sync_repo(_repo(tmp_path, src.as_uri()))
    assert not (repos / "7").exists()


def test_sync_keeps_previous_on_failure(tmp_path, monkeypatch):
    url = _make_local_repo(tmp_path, module_id="good_module")
    repos = tmp_path / "repos"
    monkeypatch.setattr(mr, "MODULE_REPOS_DIR", repos)
    monkeypatch.setattr(mr, "decrypt_secret", lambda v: "dummy")
    mr.sync_repo(_repo(tmp_path, url))
    assert (repos / "7" / "vulns" / "good_module").exists()

    src = tmp_path / "src"
    (src / "vulns" / "good_module" / "good_module.yaml").write_text(
        "id: good_module\nname: X\ndescription: d\ntype: vulnerability\n"
        "difficulty: hard\ncategory: c\n"
    )
    subprocess.run(["git", "add", "."], cwd=src, check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "bad"],
                   cwd=src, check=True, capture_output=True)
    with pytest.raises(ValueError):
        mr.sync_repo(_repo(tmp_path, url))
    assert (repos / "7" / "vulns" / "good_module" / "good_module.yaml").exists()


def test_sync_cleans_up_temp_files(tmp_path, monkeypatch):
    url = _make_local_repo(tmp_path)
    repos = tmp_path / "repos"
    monkeypatch.setattr(mr, "MODULE_REPOS_DIR", repos)
    monkeypatch.setattr(mr, "decrypt_secret", lambda v: "dummy")
    mr.sync_repo(_repo(tmp_path, url))
    leftovers = [p.name for p in repos.iterdir()
                 if p.name.startswith(".key-") or p.name.startswith(".sync-")]
    assert leftovers == []


def test_delete_repo_removes_dir(tmp_path, monkeypatch):
    url = _make_local_repo(tmp_path)
    repos = tmp_path / "repos"
    monkeypatch.setattr(mr, "MODULE_REPOS_DIR", repos)
    monkeypatch.setattr(mr, "decrypt_secret", lambda v: "dummy")
    mr.sync_repo(_repo(tmp_path, url))
    mr.delete_repo(_repo(tmp_path, url))
    assert not (repos / "7").exists()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_module_repo.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'builder.module_repo'`.

- [ ] **Step 4: Implement the service**

`builder/module_repo.py`:

```python
"""Clone, validate, and manage external module repositories on disk."""

import os
import shutil
import subprocess
import uuid
from pathlib import Path

from api.services.secrets import decrypt_secret

MODULE_REPOS_DIR = Path(os.environ.get("MODULE_REPOS_DIR", "/app/module_repos"))


def repo_dir(repo_id: int) -> Path:
    return MODULE_REPOS_DIR / str(repo_id)


def _validate_clone(path: Path) -> None:
    from builder.module_loader import module_from_yaml
    for yaml_path in sorted(path.rglob("*.yaml")):
        if ".git" in yaml_path.parts:
            continue
        try:
            module_from_yaml(yaml_path)
        except Exception as exc:
            raise ValueError(f"invalid module definition {yaml_path.name}: {exc}") from exc


def _ssh_env(key_path: Path) -> dict:
    env = os.environ.copy()
    env["GIT_SSH_COMMAND"] = (
        f"ssh -i {key_path} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
    )
    return env


def sync_repo(repo) -> None:
    key = decrypt_secret(repo.ssh_key_encrypted)
    MODULE_REPOS_DIR.mkdir(parents=True, exist_ok=True)
    key_path = MODULE_REPOS_DIR / f".key-{uuid.uuid4().hex}"
    tmpdir = MODULE_REPOS_DIR / f".sync-{uuid.uuid4().hex}"
    try:
        key_path.write_text(key + "\n")
        os.chmod(key_path, 0o600)
        env = _ssh_env(key_path)
        subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", repo.branch,
             repo.repo_url, str(tmpdir)],
            check=True, capture_output=True, text=True, env=env,
        )
        _validate_clone(tmpdir)
        final = repo_dir(repo.id)
        if final.exists():
            shutil.rmtree(final, ignore_errors=True)
        os.replace(tmpdir, final)
    finally:
        if key_path.exists():
            key_path.unlink(missing_ok=True)
        if tmpdir.exists():
            shutil.rmtree(tmpdir, ignore_errors=True)


def delete_repo(repo) -> None:
    final = repo_dir(repo.id)
    if final.exists():
        shutil.rmtree(final, ignore_errors=True)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_module_repo.py -v`
Expected: PASS (5 tests).

- [ ] **Step 6: Commit**

```bash
git add builder/module_repo.py Dockerfile tests/test_module_repo.py
git commit -m "feat: add external module repo git sync service"
```

---

### Task 4: Admin API routes

**Files:**
- Create: `api/routes/module_repos.py`
- Modify: `api/main.py:17` (import list) and `api/main.py:367-380` (include_router block)
- Test: `tests/test_module_repos_api.py`

**Interfaces:**
- Consumes: `require_admin` (from `api.routes.admin`), `encrypt_secret` (existing), `ModuleRepo` (Task 2), `sync_repo`/`delete_repo` (Task 3).
- Produces:
  - `_repo_dict(repo) -> dict`
  - `_run_sync(repo, db) -> dict`
  - `sync_all_repos(db) -> None` (used by Task 6)
  - Routes: `GET/POST /admin/api/module-repos`, `POST /admin/api/module-repos/{repo_id}/sync`, `DELETE /admin/api/module-repos/{repo_id}`

- [ ] **Step 1: Write the failing test**

`tests/test_module_repos_api.py`:

```python
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


@pytest.fixture()
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_module_repos_api.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'api.routes.module_repos'`.

- [ ] **Step 3: Implement the router**

`api/routes/module_repos.py`:

```python
"""Admin CRUD and sync for external module repositories."""

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from api.database import get_db
from api.models import ModuleRepo, utcnow
from api.routes.admin import require_admin
from api.services.secrets import encrypt_secret
from builder.module_repo import delete_repo as _delete_repo_dir, sync_repo

router = APIRouter(prefix="/admin/api", tags=["module_repos"])


def _repo_dict(repo: ModuleRepo) -> dict:
    return {
        "id": repo.id,
        "name": repo.name,
        "repo_url": repo.repo_url,
        "branch": repo.branch,
        "status": repo.status,
        "last_sync_at": repo.last_sync_at.isoformat() if repo.last_sync_at else None,
        "last_error": repo.last_error,
        "has_key": bool(repo.ssh_key_encrypted),
    }


def _validate_repo_fields(name, repo_url, branch, ssh_key):
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name is required")
    if not isinstance(repo_url, str) or not repo_url.strip():
        raise ValueError("repo_url is required")
    if not isinstance(ssh_key, str) or not ssh_key.strip():
        raise ValueError("ssh_key is required")
    return name.strip(), repo_url.strip(), (branch or "main").strip(), ssh_key


def _run_sync(repo: ModuleRepo, db: Session) -> dict:
    try:
        sync_repo(repo)
        repo.status = "synced"
        repo.last_error = None
    except Exception as exc:
        repo.status = "error"
        repo.last_error = str(exc)
    repo.last_sync_at = utcnow()
    repo.updated_at = utcnow()
    db.commit()
    db.refresh(repo)
    return _repo_dict(repo)


def sync_all_repos(db: Session) -> None:
    for repo in db.query(ModuleRepo).order_by(ModuleRepo.id).all():
        _run_sync(repo, db)


@router.get("/module-repos")
async def list_repos(request: Request, db: Session = Depends(get_db)):
    if not require_admin(request, db):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    return {"repos": [_repo_dict(r) for r in db.query(ModuleRepo).order_by(ModuleRepo.id).all()]}


@router.post("/module-repos", status_code=201)
async def create_repo(request: Request, db: Session = Depends(get_db)):
    if not require_admin(request, db):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    body = await request.json()
    try:
        name, repo_url, branch, ssh_key = _validate_repo_fields(
            body.get("name"), body.get("repo_url"), body.get("branch"), body.get("ssh_key"))
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    repo = ModuleRepo(name=name, repo_url=repo_url, branch=branch,
                      ssh_key_encrypted=encrypt_secret(ssh_key))
    db.add(repo)
    db.commit()
    db.refresh(repo)
    return _run_sync(repo, db)


@router.post("/module-repos/{repo_id}/sync")
async def sync_repo_endpoint(repo_id: int, request: Request, db: Session = Depends(get_db)):
    if not require_admin(request, db):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    repo = db.get(ModuleRepo, repo_id)
    if not repo:
        return JSONResponse({"error": "Module repository not found"}, status_code=404)
    return _run_sync(repo, db)


@router.delete("/module-repos/{repo_id}", status_code=204)
async def delete_repo_endpoint(repo_id: int, request: Request, db: Session = Depends(get_db)):
    if not require_admin(request, db):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    repo = db.get(ModuleRepo, repo_id)
    if not repo:
        return JSONResponse({"error": "Module repository not found"}, status_code=404)
    _delete_repo_dir(repo)
    db.delete(repo)
    db.commit()
    return Response(status_code=204)
```

- [ ] **Step 4: Register the router**

In `api/main.py`:
- Line 17: add `module_repos` to the import list (keep alphabetical order):
  `from api.routes import admin, ai_agent, ansible_export, auth, caldera_export, caldera_ops, caldera_setup, caldera_tree, event_dashboard, learner, module_repos, scenarios, service_credentials, vm, vm_goals`
- After `app.include_router(learner.router)` (line 376): add `app.include_router(module_repos.router)`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_module_repos_api.py -v`
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add api/routes/module_repos.py api/main.py tests/test_module_repos_api.py
git commit -m "feat: add module repository admin API"
```

---

### Task 5: Admin UI page

**Files:**
- Create: `frontend/templates/module_repos.html`
- Modify: `frontend/templates/admin_base.html:20` (add nav link)
- Modify: `api/main.py` (add HTML page route near the other `/admin/*` page routes, ~line 522)
- Test: append to `tests/test_module_repos_api.py`

**Interfaces:**
- Consumes: `GET/POST/DELETE /admin/api/module-repos*` (Task 4), `admin_base.html` layout + `active_nav` mechanism.
- Produces: `GET /admin/module-repos` (HTML page).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_module_repos_api.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_module_repos_api.py::test_page_renders_for_admin -v`
Expected: FAIL — `404` (no route) or assertion error.

- [ ] **Step 3: Add the page route**

In `api/main.py`, after the `modules_page` route (around line 522-524), add:

```python
@app.get("/admin/module-repos", response_class=HTMLResponse)
async def module_repos_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return RedirectResponse("/", status_code=303)

    return templates.TemplateResponse(request, "module_repos.html", {
        "user": user, "page_title": "Module repositories", "active_nav": "module_repos",
        "page_description": "Attach private git repositories that contribute additional modules.",
        "breadcrumbs": [{"label": "Module repositories"}],
    })
```

- [ ] **Step 4: Add the nav link**

In `frontend/templates/admin_base.html:20`, replace:

```html
<div class="nav-group"><div class="nav-label">Training Content</div><a href="/admin/modules" {% if active_nav == 'modules' %}class="active" aria-current="page"{% endif %}>Module library</a></div>
```

with:

```html
<div class="nav-group"><div class="nav-label">Training Content</div><a href="/admin/modules" {% if active_nav == 'modules' %}class="active" aria-current="page"{% endif %}>Module library</a><a href="/admin/module-repos" {% if active_nav == 'module_repos' %}class="active" aria-current="page"{% endif %}>Module repositories</a></div>
```

- [ ] **Step 5: Write the template**

`frontend/templates/module_repos.html`:

```html
{% extends "admin_base.html" %}
{% block admin_style %}<style>
  .repo-meta { font-size: .8rem; color: var(--text-muted); }
  .status-synced { color: var(--green, #4ade80); }
  .status-error { color: var(--red, #f87171); }
  .status-pending { color: var(--text-muted); }
  .stack-form { display: flex; flex-direction: column; gap: .6rem; max-width: 640px; }
  .stack-form label { display: flex; flex-direction: column; gap: .25rem; font-size: .8rem; }
  .stack-form input, .stack-form textarea { padding: .5rem; background: var(--bg); border: 1px solid var(--border); border-radius: 6px; color: var(--text-primary); }
  .stack-form textarea { font-family: monospace; }
</style>{% endblock %}
{% block admin_content %}
<section class="card">
  <h2>Add repository</h2>
  <form id="add-repo-form" class="stack-form">
    <label>Name <input id="repo-name" required placeholder="Private CTF modules"></label>
    <label>Repository URL <input id="repo-url" required placeholder="git@github.com:org/private-modules.git"></label>
    <label>Branch <input id="repo-branch" placeholder="main"></label>
    <label>SSH private key <textarea id="repo-key" required rows="6" placeholder="-----BEGIN OPENSSH PRIVATE KEY-----"></textarea></label>
    <button class="btn btn-primary" type="submit">Add &amp; sync</button>
  </form>
</section>

<section class="card">
  <h2>Repositories</h2>
  <div class="table-wrap"><table class="compact-table">
    <thead><tr><th>Name</th><th>URL</th><th>Branch</th><th>Status</th><th>Last sync</th><th></th></tr></thead>
    <tbody id="repos-body"><tr><td colspan="6" style="color: var(--text-secondary);">Loading…</td></tr></tbody>
  </table></div>
</section>
{% endblock %}
{% block admin_script %}
<script>
(function () {
  var body = document.getElementById('repos-body');
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c]; }); }
  function statusClass(s) { return s === 'synced' ? 'status-synced' : (s === 'error' ? 'status-error' : 'status-pending'); }
  function load() {
    fetch('/admin/api/module-repos').then(function (r) { return r.json(); }).then(function (data) {
      var repos = data.repos || [];
      if (!repos.length) { body.innerHTML = '<tr><td colspan="6" style="color: var(--text-secondary);">No repositories attached.</td></tr>'; return; }
      body.innerHTML = repos.map(function (r) {
        var err = r.last_error ? '<div class="repo-meta status-error">' + esc(r.last_error) + '</div>' : '';
        return '<tr><td><strong>' + esc(r.name) + '</strong>' + err + '</td>' +
          '<td><span class="repo-meta">' + esc(r.repo_url) + '</span></td>' +
          '<td>' + esc(r.branch) + '</td>' +
          '<td class="' + statusClass(r.status) + '">' + esc(r.status) + '</td>' +
          '<td>' + esc(r.last_sync_at || '—') + '</td>' +
          '<td><button class="btn btn-sm" data-sync="' + r.id + '">Sync</button> ' +
          '<button class="btn-outline danger" data-delete="' + r.id + '">Delete</button></td></tr>';
      }).join('');
    });
  }
  body.addEventListener('click', function (e) {
    var syncId = e.target.getAttribute('data-sync');
    var delId = e.target.getAttribute('data-delete');
    if (syncId) {
      fetch('/admin/api/module-repos/' + syncId + '/sync', { method: 'POST' }).then(function (r) { return r.json(); }).then(load);
    } else if (delId) {
      if (!confirm('Delete this repository and remove its cloned modules?')) return;
      fetch('/admin/api/module-repos/' + delId, { method: 'DELETE' }).then(load);
    }
  });
  document.getElementById('add-repo-form').addEventListener('submit', function (e) {
    e.preventDefault();
    fetch('/admin/api/module-repos', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: document.getElementById('repo-name').value,
        repo_url: document.getElementById('repo-url').value,
        branch: document.getElementById('repo-branch').value || 'main',
        ssh_key: document.getElementById('repo-key').value,
      }),
    }).then(function (r) { return r.json(); }).then(function () {
      document.getElementById('add-repo-form').reset();
      load();
    });
  });
  load();
})();
</script>
{% endblock %}
```

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/test_module_repos_api.py::test_page_renders_for_admin -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add frontend/templates/module_repos.html frontend/templates/admin_base.html api/main.py tests/test_module_repos_api.py
git commit -m "feat: add module repositories admin page"
```

---

### Task 6: Event start sync hook

**Files:**
- Modify: `api/routes/admin.py` (`start_event`, ~line 1220)
- Test: `tests/test_module_repo_start_hook.py`

**Interfaces:**
- Consumes: `sync_all_repos` (Task 4), `start_event` (existing).
- Produces: `start_event` syncs all `ModuleRepo` rows before any module selection/provisioning.

- [ ] **Step 1: Write the failing test**

`tests/test_module_repo_start_hook.py`:

```python
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, StaticPool
from sqlalchemy.orm import sessionmaker

from api.database import Base, get_db
from api.models import Event, User
from api.main import app

_engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
_Session = sessionmaker(bind=_engine)


@pytest.fixture(scope="module", autouse=True)
def setup_db():
    Base.metadata.create_all(bind=_engine)
    yield
    Base.metadata.drop_all(bind=_engine)


@pytest.fixture()
def draft_event():
    db = _Session()
    admin = User(username="admin", password_hash="x", is_admin=True)
    db.add(admin); db.flush()
    event = Event(name="Draft", quota="{}", status="draft")
    db.add(event); db.commit(); db.refresh(event)
    yield admin, event
    db.close()


def test_start_event_syncs_repos(draft_event, monkeypatch):
    admin, event = draft_event

    def override_get_db():
        db = _Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with patch("api.routes.admin.require_admin", return_value=admin), \
         patch("api.routes.module_repos.sync_all_repos") as mock_sync, \
         patch("api.services.expo_ust.schedule", return_value=True):
        with TestClient(app, raise_server_exceptions=True) as c:
            resp = c.post(f"/admin/api/events/{event.id}/start")
    app.dependency_overrides.clear()
    assert resp.status_code == 200
    mock_sync.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_module_repo_start_hook.py -v`
Expected: FAIL — `mock_sync.assert_called_once()` fails (never called).

- [ ] **Step 3: Wire the hook into `start_event`**

In `api/routes/admin.py`, inside `start_event`, immediately after the draft-status check (after the `if event.status != "draft": ... return` block, i.e. right before `# A GameNet event is not public until lockdown...`), add:

```python
    from api.routes.module_repos import sync_all_repos
    sync_all_repos(db)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_module_repo_start_hook.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/routes/admin.py tests/test_module_repo_start_hook.py
git commit -m "feat: sync module repos on event start"
```

---

### Task 7: Deployment wiring & docs

**Files:**
- Modify: `docker-compose.yml` (add `module_repos` volume + mount + env)
- Modify: `deploy/docker-compose.yml` (add `ctf-module_repos` volume + mount + env)
- Modify: `CLAUDE.md` (document the feature)

**Interfaces:**
- Consumes: `MODULE_REPOS_DIR` env var (read by `builder/module_loader.py` and `builder/module_repo.py`).

- [ ] **Step 1: Wire the dev compose file**

In `docker-compose.yml`, under the `api` service `volumes:` (currently `- api_data:/app/data`), add `- module_repos:/app/module_repos`. Under `environment:`, add `- MODULE_REPOS_DIR=/app/module_repos`. Under the top-level `volumes:` section, add `module_repos:`.

- [ ] **Step 2: Wire the deploy compose file**

In `deploy/docker-compose.yml`:
- In the `api` service `volumes:` (currently includes `- ctf-shared_playbooks:/shared/playbooks`), add `- ctf-module_repos:/app/module_repos`.
- In `environment:`, add `- MODULE_REPOS_DIR=/app/module_repos`.
- In the top-level `volumes:` section, add `ctf-module_repos:`.

- [ ] **Step 3: Document the feature**

In `CLAUDE.md`, under the "Module System" section, add a short subsection:

```markdown
### External Module Repositories

Admins can attach private git repositories that contribute additional modules (kept out of the public repo). Configured at `/admin/module-repos` with a repo URL + SSH private key (encrypted at rest via `DATA_ENCRYPTION_KEY`). The platform does a fresh shallow clone → validate → atomic swap into `/app/module_repos/<repo_id>/`; `load_all_modules()` scans these roots in addition to `modules/`. Repos are synced manually or at event start. Private repos mirror the built-in `modules/` layout.
```

- [ ] **Step 4: Verify the full stack builds and tests pass**

Run:
```bash
docker compose --profile test build tests
docker compose --profile test run --rm tests
```
Expected: image builds (git + openssh-client present) and the full suite passes.

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml deploy/docker-compose.yml CLAUDE.md
git commit -m "chore: wire module repo volume and document feature"
```

---

## Self-Review Notes (already applied)

- Spec coverage: multi-root loader (Task 1), model+migration (Task 2), git service + validation + atomic swap (Task 3), admin API (Task 4), admin UI + nav (Task 5), event-start hook (Task 6), persistent volume + docs (Task 7). Security (encryption, transient key, no key echo) covered in Tasks 3-4.
- The `module_from_yaml` extraction keeps validation DRY (Task 1 produces it, Task 3 consumes it).
- Route test patches `api.routes.module_repos._delete_repo_dir` (matches the aliased import in the router).
