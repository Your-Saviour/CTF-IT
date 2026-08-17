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
