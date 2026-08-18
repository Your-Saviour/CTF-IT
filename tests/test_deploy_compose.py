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


def test_iam_policy_separates_run_instances_dependencies_from_tagged_resources() -> None:
    policy = json.loads((ROOT / "deploy" / "aws" / "iam-policy.json").read_text())
    statements = {statement["Sid"]: statement for statement in policy["Statement"]}
    dependencies = statements["RunInstancesDependencies"]
    created = statements["RunTaggedCompute"]
    existing_interfaces = statements["RunWithOwnedNetworkInterfaces"]

    assert dependencies["Action"] == ["ec2:RunInstances"]
    assert "Condition" not in dependencies
    assert {
        "arn:aws:ec2:*::image/*",
        "arn:aws:ec2:*:*:key-pair/*",
        "arn:aws:ec2:*:*:security-group/*",
        "arn:aws:ec2:*:*:subnet/*",
        "arn:aws:ec2:*:*:snapshot/*",
    } <= set(dependencies["Resource"])
    assert created["Action"] == ["ec2:RunInstances"]
    assert set(created["Resource"]) == {
        "arn:aws:ec2:*:*:instance/*",
        "arn:aws:ec2:*:*:network-interface/*",
        "arn:aws:ec2:*:*:volume/*",
    }
    assert created["Condition"]["StringEquals"]["aws:RequestTag/ManagedBy"] == "ctf-it"
    assert existing_interfaces["Action"] == ["ec2:RunInstances"]
    assert existing_interfaces["Resource"] == "arn:aws:ec2:*:*:network-interface/*"
    assert existing_interfaces["Condition"]["StringEquals"]["aws:ResourceTag/ManagedBy"] == "ctf-it"


def test_iam_policy_allows_explicit_create_actions_that_reference_existing_resources() -> None:
    policy = json.loads((ROOT / "deploy" / "aws" / "iam-policy.json").read_text())
    statements = {statement["Sid"]: statement for statement in policy["Statement"]}
    creates = statements["CreateInfrastructure"]

    assert "Condition" not in creates
    assert set(creates["Action"]) == {
        "ec2:AllocateAddress", "ec2:CreateImage", "ec2:CreateInternetGateway",
        "ec2:CreateNetworkInterface", "ec2:CreateRouteTable",
        "ec2:CreateSecurityGroup", "ec2:CreateSubnet", "ec2:CreateTags",
        "ec2:CreateVpc", "ec2:ImportKeyPair",
    }


def test_iam_policy_allows_removing_run_tags_only_from_owned_resources() -> None:
    policy = json.loads((ROOT / "deploy" / "aws" / "iam-policy.json").read_text())
    statements = {statement["Sid"]: statement for statement in policy["Statement"]}
    operated = statements["OperateOwnedInfrastructure"]

    assert "ec2:DeleteTags" in operated["Action"]
    assert operated["Condition"]["StringEquals"]["aws:ResourceTag/ManagedBy"] == "ctf-it"


def test_iam_policy_allows_disabling_source_checks_on_owned_network_interfaces() -> None:
    policy = json.loads((ROOT / "deploy" / "aws" / "iam-policy.json").read_text())
    statements = {statement["Sid"]: statement for statement in policy["Statement"]}
    operated = statements["OperateOwnedInfrastructure"]

    assert "ec2:ModifyNetworkInterfaceAttribute" in operated["Action"]
    assert operated["Condition"]["StringEquals"]["aws:ResourceTag/ManagedBy"] == "ctf-it"


def test_aws_login_and_acceptance_are_containerized() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    tools = compose["services"]["aws-tools"]
    acceptance = compose["services"]["aws-acceptance"]

    assert tools["image"] == "public.ecr.aws/aws-cli/aws-cli:2.36.24"
    assert tools["profiles"] == ["aws-acceptance"]
    assert acceptance["profiles"] == ["aws-acceptance"]
    assert acceptance["build"]["target"] == "acceptance"
    assert "aws_credentials:/root/.aws" in tools["volumes"]
    assert "aws_credentials:/root/.aws" in acceptance["volumes"]
    assert acceptance["network_mode"] == "host"
    assert "NET_ADMIN" in acceptance["cap_add"]
    assert "/dev/net/tun:/dev/net/tun" in acceptance["devices"]
    assert any(
        value.startswith("DATA_ENCRYPTION_KEY=")
        for value in acceptance["environment"]
    )
    assert "AWS_ACCEPTANCE_FORCE_OPNSENSE_BUILD=${AWS_ACCEPTANCE_FORCE_OPNSENSE_BUILD:-}" in acceptance["environment"]
    assert "aws_credentials" in compose["volumes"]


def test_opnsense_cache_operator_commands_run_in_acceptance_container() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    cache = compose["services"]["aws-opnsense-cache"]

    assert cache["profiles"] == ["aws-acceptance"]
    assert cache["build"]["target"] == "acceptance"
    assert cache["entrypoint"] == [
        "python", "-m", "scripts.aws_acceptance_opnsense_cache",
    ]
    assert cache["command"][-1] == "--inventory-only"
    assert "aws_credentials:/root/.aws" in cache["volumes"]


def test_opnsense_source_archive_is_built_and_consumed_in_containers() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    source = compose["services"]["opnsense-source-cache"]
    acceptance = compose["services"]["aws-acceptance"]

    assert source["image"].startswith("alpine/git:")
    assert "opnsense_source_cache:/cache" in source["volumes"]
    assert "opnsense_source_cache:/var/cache/opnsense:ro" in acceptance["volumes"]
    assert "OPNSENSE_CORE_ARCHIVE=/var/cache/opnsense/core-26.7.tar.gz" in acceptance["environment"]
    assert "OPNSENSE_CORE_COMMIT_FILE=/var/cache/opnsense/core-26.7.commit" in acceptance["environment"]


def test_aws_containers_expose_no_static_keys_or_host_credential_mounts() -> None:
    services = yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]

    for name in ("aws-tools", "aws-acceptance"):
        service = services[name]
        environment = "\n".join(service.get("environment", []))
        volumes = service.get("volumes", [])
        assert "AWS_ACCESS_KEY_ID" not in environment
        assert "AWS_SECRET_ACCESS_KEY" not in environment
        assert "aws_credentials:/root/.aws" in volumes
        assert all(volume.startswith(("aws_credentials:", "opnsense_source_cache:"))
                   for volume in volumes)
