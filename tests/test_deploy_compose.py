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
