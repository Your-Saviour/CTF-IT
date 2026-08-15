from pathlib import Path
import json

import yaml


ROOT = Path(__file__).resolve().parents[1]
COMPOSE_PATH = ROOT / "deploy" / "docker-compose.yml"


def _services() -> dict:
    with COMPOSE_PATH.open() as handle:
        return yaml.safe_load(handle)["services"]


def test_caldera_builds_official_release_source() -> None:
    caldera = _services()["caldera"]

    assert "ghcr.io/mitre/caldera" not in caldera.get("image", "")
    assert caldera["build"]["context"].startswith(
        "https://github.com/apache/caldera.git#"
    )
    assert caldera["build"]["args"]["VARIANT"] == "full"


def test_dockhand_healthcheck_uses_available_node_runtime() -> None:
    healthcheck = _services()["dockhand"]["healthcheck"]["test"]

    assert healthcheck[:2] == ["CMD", "node"]
    assert all("wget" not in part for part in healthcheck)


def test_production_components_use_service_discovery_and_shared_networks() -> None:
    services = _services()
    api = services["api"]
    agent = services["ai-agent"]
    caldera = services["caldera"]

    assert "network_mode" not in agent
    assert "ctf-internal" in agent["networks"]
    assert "ctf-internal" in caldera["networks"]
    assert any("CTF_API_URL=http://api:8000" in value for value in agent["environment"])
    assert any("CALDERA_INTERNAL_URL=http://caldera:8888" in value for value in agent["environment"])
    assert not any("172." in value for value in agent["environment"])
    assert any("AGENT_API_URL=http://ai-agent:8000" in value for value in api["environment"])


def test_production_api_uses_postgres() -> None:
    services = _services()
    api_environment = services["api"]["environment"]

    assert "api-postgres" in services
    assert any("DATABASE_URL=postgresql+psycopg://" in value for value in api_environment)


def test_opnsense_iso_sidecar_and_shared_volume_are_removed() -> None:
    services = _services()
    api = services["api"]
    assert "opnsense-iso" not in services
    assert not any("opnsense" in volume.lower() for volume in api.get("volumes", []))


def test_iso_nginx_configuration_is_removed() -> None:
    assert not (ROOT / "deploy" / "nginx" / "opnsense-iso.conf").exists()


def test_caldera_ssh_host_key_is_mounted_read_only() -> None:
    caldera = _services()["caldera"]
    assert "./caldera/config/ssh_host_key:/usr/src/app/conf/ssh_host_key:ro" in caldera["volumes"]


def test_compose_passes_aws_configuration_without_static_secret_values() -> None:
    for path in (ROOT / "docker-compose.yml", COMPOSE_PATH):
        services = yaml.safe_load(path.read_text())["services"]
        environment = services["api"]["environment"]
        assert any("AWS_DEFAULT_REGION" in value for value in environment)
        assert not any("AWS_ACCESS_KEY_ID" in value or "AWS_SECRET_ACCESS_KEY" in value
                       for value in environment)


def test_iam_policy_uses_only_documented_aws_services_and_no_service_wildcards() -> None:
    policy = json.loads((ROOT / "deploy" / "aws" / "iam-policy.json").read_text())
    actions = {action for statement in policy["Statement"] for action in statement["Action"]}
    assert all(action.split(":", 1)[0] in {"ec2", "sts", "servicequotas", "pricing"}
               for action in actions)
    assert "iam:*" not in actions and "ec2:*" not in actions
