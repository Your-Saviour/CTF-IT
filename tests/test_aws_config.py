import importlib

import pytest

from api.services.aws import (
    AwsConfig,
    AwsConfigurationError,
    AwsOwnershipError,
    AwsSessionFactory,
    assert_owned,
    ownership_tags,
)


def _set_required_env(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-southeast-2")
    monkeypatch.setenv("AWS_ENVIRONMENT", "test")
    monkeypatch.setenv("AWS_STANDARD_VPC_ID", "vpc-123")
    monkeypatch.setenv("AWS_STANDARD_SUBNET_ID", "subnet-123")
    monkeypatch.setenv("AWS_UBUNTU_AMIS", '{"ap-southeast-2":"ami-ubuntu"}')
    monkeypatch.setenv("AWS_FREEBSD_AMIS", '{"ap-southeast-2":"ami-freebsd"}')
    monkeypatch.setenv("AWS_AVAILABILITY_ZONES", '{"ap-southeast-2":"ap-southeast-2a"}')
    monkeypatch.setenv("AWS_INSTANCE_TYPES", "t3.small,t3.medium")
    monkeypatch.setenv("AWS_STANDARD_SECURITY_GROUP_IDS", "sg-one, sg-two")


def test_config_loads_network_approved_amis_and_instance_types(monkeypatch):
    _set_required_env(monkeypatch)
    monkeypatch.setenv("AWS_KEY_PAIR_NAME", "ctf-it-platform")

    config = AwsConfig.from_env()

    assert config.default_region == "ap-southeast-2"
    assert config.standard_vpc_id == "vpc-123"
    assert config.standard_subnet_id == "subnet-123"
    assert config.standard_security_group_ids == ("sg-one", "sg-two")
    assert config.ubuntu_ami("ap-southeast-2") == "ami-ubuntu"
    assert config.freebsd_ami("ap-southeast-2") == "ami-freebsd"
    assert config.instance_types == ("t3.small", "t3.medium")
    assert config.availability_zone("ap-southeast-2") == "ap-southeast-2a"
    assert config.key_pair_name == "ctf-it-platform"


def test_acceptance_config_suffixes_shared_key_pair_name(monkeypatch):
    _set_required_env(monkeypatch)
    monkeypatch.setenv("AWS_ENVIRONMENT", "acceptance")
    monkeypatch.setenv("AWS_KEY_PAIR_NAME", "ctf-it-platform")
    monkeypatch.setenv("RUN_AWS_ACCEPTANCE", "1")
    monkeypatch.setenv("AWS_ACCEPTANCE_RUN_ID", "run-123456")
    config = AwsConfig.from_env()
    assert config.key_pair_name == "ctf-it-platform-run-123456"
    assert config.resource_token("vm", 7) == "ctf-it-acceptance-run-123456-vm-7"


def test_config_rejects_missing_approved_ami_mapping(monkeypatch):
    _set_required_env(monkeypatch)
    monkeypatch.delenv("AWS_FREEBSD_AMIS")

    with pytest.raises(AwsConfigurationError, match="AWS_FREEBSD_AMIS"):
        AwsConfig.from_env()


def test_config_rejects_unknown_region_ami(monkeypatch):
    _set_required_env(monkeypatch)
    config = AwsConfig.from_env()

    with pytest.raises(AwsConfigurationError, match="eu-west-1"):
        config.ubuntu_ami("eu-west-1")


def test_ownership_tags_include_only_supplied_resource_ids():
    assert ownership_tags("test", event_id=4, vm_id=7) == {
        "Application": "ctf-it",
        "ManagedBy": "ctf-it",
        "Environment": "test",
        "EventId": "4",
        "VmId": "7",
    }


def test_acceptance_opt_in_adds_unique_run_tag(monkeypatch):
    monkeypatch.setenv("RUN_AWS_ACCEPTANCE", "1")
    monkeypatch.setenv("AWS_ACCEPTANCE_RUN_ID", "run-123456")
    assert ownership_tags("acceptance", event_id=4)["AcceptanceRunId"] == "run-123456"


def test_acceptance_run_tag_requires_both_safety_gates(monkeypatch):
    monkeypatch.setenv("RUN_AWS_ACCEPTANCE", "1")
    monkeypatch.setenv("AWS_ACCEPTANCE_RUN_ID", "run-123456")
    assert "AcceptanceRunId" not in ownership_tags("production")
    monkeypatch.delenv("RUN_AWS_ACCEPTANCE")
    assert "AcceptanceRunId" not in ownership_tags("acceptance")


def test_assert_owned_rejects_mismatched_vm():
    with pytest.raises(AwsOwnershipError, match="VmId"):
        assert_owned(
            {"Application": "ctf-it", "ManagedBy": "ctf-it", "VmId": "8"},
            {"Application": "ctf-it", "ManagedBy": "ctf-it", "VmId": "7"},
        )


def test_session_factory_uses_profile_and_region_without_explicit_secrets(monkeypatch):
    _set_required_env(monkeypatch)
    monkeypatch.setenv("AWS_PROFILE", "development")
    captured = {}

    class Session:
        def __init__(self, **kwargs):
            captured["session"] = kwargs

        def client(self, service, **kwargs):
            captured["client"] = (service, kwargs)
            return object()

    monkeypatch.setattr("api.services.aws.session.boto3.Session", Session)
    factory = AwsSessionFactory(AwsConfig.from_env())
    factory.client("ec2", region="us-east-1")

    assert captured["session"] == {
        "profile_name": "development",
        "region_name": "ap-southeast-2",
    }
    assert captured["client"] == ("ec2", {"region_name": "us-east-1"})


def test_runtime_includes_crt_for_aws_login_profiles():
    assert importlib.import_module("awscrt")
