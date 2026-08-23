from builder.infrastructure_planner import infrastructure_node_ids, normalize_infrastructure
from builder.infrastructure_validation import infrastructure_summary, validate_infrastructure
from builder.module_loader import load_all_modules


GATEWAY = {
    "base_type": "ubuntu_24_server", "default_plan": "small",
    "region": "syd", "listen_port": 51820,
}
SITES = [{
    "key": "hq", "name": "HQ", "region": "syd", "firewall_team": "blue",
    "firewall": {"base_type": "opnsense", "default_plan": "medium"},
    "zones": [{"key": "blue", "name": "Blue", "team": "blue", "endpoints": []}],
}]


def test_legacy_infrastructure_normalizes_to_an_empty_green_collection():
    normalized = normalize_infrastructure({"vpn_gateway": GATEWAY, "sites": SITES})
    assert normalized["green_infrastructure"] == {"vms": []}


def test_green_vm_validates_and_is_counted_once_per_event():
    infrastructure = {
        "vpn_gateway": GATEWAY,
        "sites": SITES,
        "green_infrastructure": {"vms": [{
            "key": "expo_it", "name": "Expo-IT", "base_type": "ubuntu_24_server",
            "default_plan": "small", "region": "syd",
        }]},
    }
    assert validate_infrastructure(infrastructure, {"ubuntu_24_server", "opnsense"}) == []
    assert "green:expo_it" in infrastructure_node_ids(infrastructure)
    assert infrastructure_summary(infrastructure, team_count=3)["green_vms"] == 1


def test_builtin_expo_it_module_declares_fixed_source_and_secret_fact():
    module = next(item for item in load_all_modules() if item.id == "expo_it")
    assert module.type == "green_infrastructure"
    assert module.deployment.repository == "git@github.com:Your-Saviour/Expo-IT.git"
    assert module.deployment.branch == "stable"
    assert [(item.trait, item.secret) for item in module.deployment.inputs] == [
        ("git.ssh_private_key", True),
    ]
    assert {item.trait for item in module.deployment.outputs} == {
        "expo_it.resolved_commit", "expo_it.private_url", "expo_it.api_key",
    }
