"""Clone, validate, and manage external module repositories on disk."""

import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from api.services.secrets import decrypt_secret

MODULE_REPOS_DIR = Path(os.environ.get("MODULE_REPOS_DIR", "/app/module_repos"))


def repo_dir(repo_id: int) -> Path:
    return MODULE_REPOS_DIR / str(repo_id)


def _module_ids(path: Path) -> set[str]:
    from builder.module_loader import module_from_yaml
    ids = set()
    for yaml_path in sorted(path.rglob("*.yaml")):
        if ".git" in yaml_path.parts:
            continue
        try:
            module = module_from_yaml(yaml_path)
        except Exception as exc:
            raise ValueError(f"invalid module definition {yaml_path.name}: {exc}") from exc
        ids.add(module.id)
    return ids


def _existing_module_ids(exclude_repo_id: int) -> set[str]:
    from builder.module_loader import MODULES_DIR
    ids = _module_ids(MODULES_DIR)
    if MODULE_REPOS_DIR.is_dir():
        for p in MODULE_REPOS_DIR.iterdir():
            if p.is_dir() and not p.name.startswith(".") and p.name != str(exclude_repo_id):
                ids |= _module_ids(p)
    return ids


def _ssh_env(key_path: Path) -> dict:
    env = os.environ.copy()
    env["GIT_SSH_COMMAND"] = (
        f"ssh -i {key_path} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
    )
    return env


def sync_repo(repo) -> None:
    key = decrypt_secret(repo.ssh_key_encrypted)
    MODULE_REPOS_DIR.mkdir(parents=True, exist_ok=True)
    fd, key_path = tempfile.mkstemp(dir=tempfile.gettempdir())
    tmpdir = MODULE_REPOS_DIR / f".sync-{uuid.uuid4().hex}"
    try:
        with os.fdopen(fd, "w") as f:
            f.write(key + "\n")
        env = _ssh_env(key_path)
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", "--branch", repo.branch,
                 repo.repo_url, str(tmpdir)],
                check=True, capture_output=True, text=True, env=env,
            )
        except subprocess.CalledProcessError as exc:
            raise ValueError(f"git clone failed: {exc.stderr.strip() or exc}") from exc
        clone_ids = _module_ids(tmpdir)
        collisions = clone_ids & _existing_module_ids(repo.id)
        if collisions:
            raise ValueError(
                f"duplicate module id(s) across module catalogue: {', '.join(sorted(collisions))}"
            )
        final = repo_dir(repo.id)
        if final.exists():
            shutil.rmtree(final, ignore_errors=True)
        os.replace(tmpdir, final)
    finally:
        if os.path.exists(key_path):
            os.unlink(key_path)
        if tmpdir.exists():
            shutil.rmtree(tmpdir, ignore_errors=True)


def delete_repo(repo) -> None:
    final = repo_dir(repo.id)
    if final.exists():
        shutil.rmtree(final, ignore_errors=True)
