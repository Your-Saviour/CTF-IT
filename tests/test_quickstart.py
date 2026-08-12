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
    caldera_ssh_key = tmp_path / "ssh_host_key"
    script = f"""
source ./quickstart.sh
ROOT_ENV={root_env!s}
DEPLOY_ENV={deploy_env!s}
CALDERA_CFG={caldera!s}
CALDERA_SSH_KEY={caldera_ssh_key!s}
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

    for path in (root_env, deploy_env, caldera, caldera_ssh_key):
        assert path.stat().st_mode & 0o777 == 0o600

    root_text = root_env.read_text()
    assert "SECRET_KEY=" in root_text
    assert "ADMIN_BOOTSTRAP_TOKEN=" in root_text
    assert "DATA_ENCRYPTION_KEY=" in root_text
    assert "AGENT_API_KEY=" in root_text
    assert "CTF_API_KEY=" in root_text
    assert "change-me" not in root_text

    deploy_text = deploy_env.read_text()
    assert "DOMAIN=ctf.example" in deploy_text
    assert "CTF_POSTGRES_PASSWORD=" in deploy_text
    assert "REGISTRY_AUTH" not in deploy_text

    caldera_text = caldera.read_text()
    assert "REPLACE_ME" not in caldera_text
    assert "REPLACE_WITH_KEY_FILE_PASSPHRASE" not in caldera_text
    assert "REPLACE_WITH_KEY_FILE_PATH" not in caldera_text
    assert "app.contact.tunnel.ssh.host_key_file: /usr/src/app/conf/ssh_host_key" in caldera_text


def test_caldera_upgrade_preserves_existing_secrets(tmp_path):
    caldera = tmp_path / "local.yml"
    caldera_ssh_key = tmp_path / "ssh_host_key"
    caldera.write_text(
        "api_key_red: keep-this-secret\n"
        "app.contact.tunnel.ssh.host_key_file: REPLACE_WITH_KEY_FILE_PATH\n"
        "app.contact.tunnel.ssh.host_key_passphrase: old-passphrase\n"
    )
    script = f"""
source ./quickstart.sh
CALDERA_CFG={caldera!s}
CALDERA_SSH_KEY={caldera_ssh_key!s}
gen_caldera_config
"""
    _run_bash(script)

    upgraded = caldera.read_text()
    assert "api_key_red: keep-this-secret" in upgraded
    assert "REPLACE_" not in upgraded
    assert caldera_ssh_key.is_file()


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


def test_cloudflare_dns_creates_and_updates_production_records(tmp_path):
    root_env = tmp_path / ".env"
    root_env.write_text(
        "CLOUDFLARE_API_TOKEN=test-token\n"
        "CLOUDFLARE_DOMAIN=example.com\n"
    )
    calls = tmp_path / "calls"
    script = f"""
source ./quickstart.sh
ROOT_ENV={root_env!s}
SERVER_IP=192.0.2.30
DOMAIN=example.com
CLOUDFLARE_API_TOKEN=
require_cmd() {{ :; }}
cloudflare_api() {{
  printf '%s %s %s\n' "$1" "$2" "${{3:-}}" >> {calls!s}
  case "$2" in
    *'/zones?name='*) printf '{{"success":true,"result":[{{"id":"zone-id"}}]}}' ;;
    *'name=ctf.example.com'*) printf '{{"success":true,"result":[{{"id":"record-id"}}]}}' ;;
    *'/dns_records?'*) printf '{{"success":true,"result":[]}}' ;;
    *) printf '{{"success":true,"result":{{}}}}' ;;
  esac
}}
configure_cloudflare_dns
"""
    _run_bash(script)

    text = calls.read_text()
    assert "PATCH https://api.cloudflare.com/client/v4/zones/zone-id/dns_records/record-id" in text
    assert text.count("POST https://api.cloudflare.com/client/v4/zones/zone-id/dns_records") == 4
    for service in ("ctf", "caldera", "semaphore", "dockhand", "traefik"):
        assert f'"name":"{service}.example.com"' in text
    assert text.count('"content":"192.0.2.30"') == 5
    assert text.count('"proxied":false') == 5
