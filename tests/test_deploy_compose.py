from pathlib import Path

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


def test_expo_it_destination_is_database_managed_not_environment_configured() -> None:
    paths = [ROOT / ".env.example", ROOT / "docker-compose.yml", ROOT / "deploy" / ".env.example",
             ROOT / "deploy" / "docker-compose.yml"]
    contents = "\n".join(path.read_text() for path in paths if path.exists())
    assert "EXPO_IT_URL" not in contents
    assert "EXPO_IT_API_KEY" not in contents
