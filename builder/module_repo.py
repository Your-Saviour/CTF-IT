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
