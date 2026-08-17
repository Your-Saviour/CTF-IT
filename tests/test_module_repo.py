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
    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    monkeypatch.setattr(mr, "MODULE_REPOS_DIR", repos)
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_dir))
    monkeypatch.setattr(mr, "decrypt_secret", lambda v: "dummy")
    mr.sync_repo(_repo(tmp_path, url))
    leftovers = [p.name for p in repos.iterdir() if p.name.startswith(".sync-")]
    assert leftovers == []
    assert list(tmp_dir.iterdir()) == []


def test_sync_writes_key_in_temp_dir_and_removes_it(tmp_path, monkeypatch):
    import os
    url = _make_local_repo(tmp_path)
    repos = tmp_path / "repos"
    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    monkeypatch.setattr(mr, "MODULE_REPOS_DIR", repos)
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_dir))
    monkeypatch.setattr(mr, "decrypt_secret", lambda v: "dummy")
    captured = {}
    orig_ssh_env = mr._ssh_env

    def fake_ssh_env(key_path):
        captured["key_path"] = key_path
        return orig_ssh_env(key_path)

    monkeypatch.setattr(mr, "_ssh_env", fake_ssh_env)
    mr.sync_repo(_repo(tmp_path, url))
    key_path = captured["key_path"]
    assert key_path.startswith(str(tmp_dir))
    assert not os.path.exists(key_path)
    assert (repos / "7" / "vulns" / "secret_backdoor").exists()


def test_sync_rejects_duplicate_builtin_module_id(tmp_path, monkeypatch):
    from builder.module_loader import MODULES_DIR, load_all_modules
    builtin_id = next(
        m.id for m in load_all_modules()
        if str(m.source_dir).startswith(str(MODULES_DIR)) and m.type == "vulnerability"
    )
    url = _make_local_repo(tmp_path, module_id=builtin_id)
    repos = tmp_path / "repos"
    monkeypatch.setattr(mr, "MODULE_REPOS_DIR", repos)
    monkeypatch.setattr(mr, "decrypt_secret", lambda v: "dummy")
    with pytest.raises(ValueError) as excinfo:
        mr.sync_repo(_repo(tmp_path, url))
    assert builtin_id in str(excinfo.value)
    assert not (repos / "7").exists()


def test_sync_rejects_cross_repo_duplicate_module_id(tmp_path, monkeypatch):
    url = _make_local_repo(tmp_path, module_id="shared_id")
    repos = tmp_path / "repos"
    monkeypatch.setattr(mr, "MODULE_REPOS_DIR", repos)
    monkeypatch.setattr(mr, "decrypt_secret", lambda v: "dummy")
    mr.sync_repo(_repo(tmp_path, url, repo_id=7))
    with pytest.raises(ValueError) as excinfo:
        mr.sync_repo(_repo(tmp_path, url, repo_id=8))
    assert "shared_id" in str(excinfo.value)
    assert not (repos / "8").exists()
    assert (repos / "7" / "vulns" / "shared_id").exists()


def test_sync_raises_with_clone_stderr(tmp_path, monkeypatch):
    repos = tmp_path / "repos"
    monkeypatch.setattr(mr, "MODULE_REPOS_DIR", repos)
    monkeypatch.setattr(mr, "decrypt_secret", lambda v: "dummy")
    with pytest.raises(ValueError) as excinfo:
        mr.sync_repo(_repo(tmp_path, "file:///nonexistent-path"))
    message = str(excinfo.value)
    assert "git clone failed" in message
    assert "fatal:" in message
    assert not (repos / "7").exists()
    assert [p.name for p in repos.iterdir() if p.name.startswith(".sync-")] == []


def test_delete_repo_removes_dir(tmp_path, monkeypatch):
    url = _make_local_repo(tmp_path)
    repos = tmp_path / "repos"
    monkeypatch.setattr(mr, "MODULE_REPOS_DIR", repos)
    monkeypatch.setattr(mr, "decrypt_secret", lambda v: "dummy")
    mr.sync_repo(_repo(tmp_path, url))
    mr.delete_repo(_repo(tmp_path, url))
    assert not (repos / "7").exists()
