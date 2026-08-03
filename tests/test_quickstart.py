import os
from pathlib import Path
import subprocess


REPO_ROOT = Path(__file__).resolve().parent.parent


def _run_bash(script: str):
    return subprocess.run(
        ["bash", "-c", script],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def test_generated_configuration_is_private_and_complete(tmp_path):
    root_env = tmp_path / ".env"
    deploy_env = tmp_path / "deploy.env"
    caldera = tmp_path / "local.yml"
    script = f"""
source ./quickstart.sh
ROOT_ENV={root_env!s}
DEPLOY_ENV={deploy_env!s}
CALDERA_CFG={caldera!s}
DOMAIN=ctf.example
ACME_EMAIL=admin@example.com
SERVER_IP=192.0.2.30
TRAEFIK_USER=admin
bcrypt_htpasswd() {{ printf 'admin:$$hash'; }}
gen_root_env
gen_deploy_env
gen_caldera_config
"""
    _run_bash(script)

    for path in (root_env, deploy_env, caldera):
        assert path.stat().st_mode & 0o777 == 0o600

    root_text = root_env.read_text()
    assert "SECRET_KEY=" in root_text
    assert "ADMIN_BOOTSTRAP_TOKEN=" in root_text
    assert "DATA_ENCRYPTION_KEY=" in root_text
    assert "change-me" not in root_text

    deploy_text = deploy_env.read_text()
    assert "DOMAIN=ctf.example" in deploy_text
    assert "REGISTRY_AUTH" not in deploy_text

    caldera_text = caldera.read_text()
    assert "REPLACE_ME" not in caldera_text
    assert "REPLACE_WITH_KEY_FILE_PASSPHRASE" not in caldera_text


def test_existing_deploy_environment_is_loaded_for_summary(tmp_path):
    root_env = tmp_path / ".env"
    deploy_env = tmp_path / "deploy.env"
    root_env.write_text("SECRET_KEY=existing\n")
    deploy_env.write_text("DOMAIN=existing.example\nACME_EMAIL=ops@example.com\n")
    os.chmod(root_env, 0o600)
    os.chmod(deploy_env, 0o600)

    script = f"""
source ./quickstart.sh
ROOT_ENV={root_env!s}
DEPLOY_ENV={deploy_env!s}
DOMAIN=
ACME_EMAIL=
collect_inputs
test "$DOMAIN" = existing.example
test "$ACME_EMAIL" = ops@example.com
gen_root_env
"""
    _run_bash(script)
    upgraded = root_env.read_text()
    assert "ADMIN_BOOTSTRAP_TOKEN=" in upgraded
    assert "DATA_ENCRYPTION_KEY=" in upgraded
    assert root_env.stat().st_mode & 0o777 == 0o600
