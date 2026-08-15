import pytest

from scripts.aws_acceptance_cleanup import CleanupContext, cleanup_owned
from .context import require_acceptance_context


def test_acceptance_requires_explicit_opt_in(monkeypatch):
    monkeypatch.delenv("RUN_AWS_ACCEPTANCE", raising=False)
    with pytest.raises(pytest.skip.Exception):
        require_acceptance_context()


def test_cleanup_filter_requires_run_id_and_expected_account():
    with pytest.raises(ValueError): CleanupContext(run_id="", expected_account_id="123456789012")
    with pytest.raises(ValueError): CleanupContext(run_id="run-123456", expected_account_id="")


class CleanupEc2:
    def __init__(self):
        self.calls = []

    def _record(self, name, **kwargs):
        self.calls.append((name, kwargs))

    def describe_instances(self, **kwargs):
        return {"Reservations": [{"Instances": [{
            "InstanceId": "i-owned", "Tags": OWNED_TAGS,
        }]}]}

    def terminate_instances(self, **kwargs): self._record("terminate_instances", **kwargs)

    def get_waiter(self, name):
        parent = self
        class Waiter:
            def wait(self, **kwargs): parent._record(f"wait:{name}", **kwargs)
        return Waiter()

    def describe_addresses(self, **kwargs):
        return {"Addresses": [{"AllocationId": "eipalloc-owned",
                                "AssociationId": "eipassoc-owned", "Tags": OWNED_TAGS}]}

    def disassociate_address(self, **kwargs): self._record("disassociate_address", **kwargs)
    def release_address(self, **kwargs): self._record("release_address", **kwargs)

    def describe_images(self, **kwargs):
        return {"Images": [{"ImageId": "ami-owned", "Tags": OWNED_TAGS,
                            "BlockDeviceMappings": [{"Ebs": {"SnapshotId": "snap-owned"}}]}]}

    def deregister_image(self, **kwargs): self._record("deregister_image", **kwargs)

    def describe_snapshots(self, **kwargs):
        return {"Snapshots": [{"SnapshotId": "snap-owned", "Tags": OWNED_TAGS}]}

    def delete_snapshot(self, **kwargs): self._record("delete_snapshot", **kwargs)

    def describe_network_interfaces(self, **kwargs): return {"NetworkInterfaces": []}
    def describe_security_groups(self, **kwargs): return {"SecurityGroups": []}
    def describe_route_tables(self, **kwargs): return {"RouteTables": []}
    def describe_subnets(self, **kwargs): return {"Subnets": []}
    def describe_internet_gateways(self, **kwargs): return {"InternetGateways": []}
    def describe_key_pairs(self, **kwargs): return {"KeyPairs": []}
    def describe_vpcs(self, **kwargs): return {"Vpcs": []}


OWNED_TAGS = [
    {"Key": "ManagedBy", "Value": "ctf-it"},
    {"Key": "Environment", "Value": "acceptance"},
    {"Key": "AcceptanceRunId", "Value": "run-123456"},
]


def test_cleanup_removes_owned_compute_artifacts_in_dependency_order():
    ec2 = CleanupEc2()
    result = cleanup_owned(ec2, CleanupContext("run-123456", "123456789012"))
    names = [name for name, _ in ec2.calls]
    assert names[:4] == ["terminate_instances", "wait:instance_terminated",
                         "disassociate_address", "release_address"]
    assert names.index("deregister_image") < names.index("delete_snapshot")
    assert result["instances"] == ["i-owned"]
    assert result["images"] == ["ami-owned"]


def test_cleanup_refuses_resource_whose_returned_tags_do_not_match_run():
    ec2 = CleanupEc2()
    ec2.describe_addresses = lambda **_: {"Addresses": [{
        "AllocationId": "eipalloc-other", "Tags": [
            {"Key": "ManagedBy", "Value": "ctf-it"},
            {"Key": "Environment", "Value": "acceptance"},
            {"Key": "AcceptanceRunId", "Value": "run-other99"},
        ],
    }]}
    with pytest.raises(RuntimeError, match="refusing cleanup"):
        cleanup_owned(ec2, CleanupContext("run-123456", "123456789012"))
