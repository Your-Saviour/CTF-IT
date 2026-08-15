import pytest

from api.services.aws import AwsOwnershipError, AwsRetryableError
from api.services.aws.compute import AwsComputeProvider, InstanceSpec, NetworkInterfaceSpec


class FakeEc2:
    def __init__(self):
        self.instances = []
        self.calls = []
        self.error = None

    def describe_instances(self, **kwargs):
        self.calls.append(("describe_instances", kwargs))
        if self.error:
            raise self.error
        return {"Reservations": [{"Instances": self.instances}]} if self.instances else {"Reservations": []}

    def run_instances(self, **kwargs):
        self.calls.append(("run_instances", kwargs))
        return {"Instances": [{
            "InstanceId": "i-123",
            "State": {"Name": "pending"},
            "Placement": {"AvailabilityZone": "ap-southeast-2a"},
            "PrivateIpAddress": "10.0.1.8",
            "NetworkInterfaces": [{"NetworkInterfaceId": "eni-primary", "Attachment": {"DeviceIndex": 0}}],
            "Tags": [{"Key": "ManagedBy", "Value": "ctf-it"}],
        }]}

    def terminate_instances(self, **kwargs):
        self.calls.append(("terminate_instances", kwargs))
        return {}

    def modify_instance_attribute(self, **kwargs):
        self.calls.append(("modify_instance_attribute", kwargs))
        return {}

    def allocate_address(self, **kwargs):
        self.calls.append(("allocate_address", kwargs))
        return {"AllocationId": "eipalloc-123", "PublicIp": "198.51.100.20"}

    def associate_address(self, **kwargs):
        self.calls.append(("associate_address", kwargs))
        return {"AssociationId": "eipassoc-123"}

    def describe_addresses(self, **kwargs):
        self.calls.append(("describe_addresses", kwargs))
        return {"Addresses": [{
            "AllocationId": "eipalloc-123", "PublicIp": "198.51.100.20",
            "AssociationId": "eipassoc-123",
            "Tags": [{"Key": "ManagedBy", "Value": "ctf-it"}, {"Key": "VmId", "Value": "7"}],
        }]}

    def release_address(self, **kwargs):
        self.calls.append(("release_address", kwargs))
        return {}

    def disassociate_address(self, **kwargs):
        self.calls.append(("disassociate_address", kwargs)); return {}


def spec():
    return InstanceSpec(
        ami_id="ami-ubuntu",
        instance_type="t3.small",
        client_token="ctf-it-vm-7",
        network_interfaces=(NetworkInterfaceSpec(0, "subnet-123", ("sg-123",)),),
        tags={"ManagedBy": "ctf-it", "VmId": "7"},
        key_name="ctf-it",
    )


def test_launch_uses_client_token_tags_imdsv2_and_explicit_eni():
    ec2 = FakeEc2()

    result = AwsComputeProvider(ec2).launch_instance(spec())

    assert result.instance_id == "i-123"
    assert result.primary_eni_id == "eni-primary"
    request = next(payload for name, payload in ec2.calls if name == "run_instances")
    assert request["ClientToken"] == "ctf-it-vm-7"
    assert request["MetadataOptions"] == {"HttpTokens": "required", "HttpEndpoint": "enabled"}
    assert request["NetworkInterfaces"] == [{
        "DeviceIndex": 0, "SubnetId": "subnet-123", "Groups": ["sg-123"],
        "AssociatePublicIpAddress": False, "DeleteOnTermination": True,
    }]
    assert {item["ResourceType"] for item in request["TagSpecifications"]} == {"instance", "volume"}


def test_launch_reconciles_owned_instance_by_client_token():
    ec2 = FakeEc2()
    ec2.instances = [{
        "InstanceId": "i-existing", "State": {"Name": "running"},
        "Tags": [{"Key": "ManagedBy", "Value": "ctf-it"}, {"Key": "VmId", "Value": "7"}],
        "NetworkInterfaces": [],
    }]

    result = AwsComputeProvider(ec2).launch_instance(spec())

    assert result.instance_id == "i-existing"
    assert not any(name == "run_instances" for name, _ in ec2.calls)


def test_terminate_refuses_foreign_instance():
    ec2 = FakeEc2()
    ec2.instances = [{
        "InstanceId": "i-foreign", "State": {"Name": "running"},
        "Tags": [{"Key": "ManagedBy", "Value": "someone-else"}],
        "NetworkInterfaces": [],
    }]

    with pytest.raises(AwsOwnershipError):
        AwsComputeProvider(ec2).terminate_owned(
            "i-foreign", {"ManagedBy": "ctf-it", "VmId": "7"}
        )

    assert not any(name == "terminate_instances" for name, _ in ec2.calls)


def test_source_destination_check_is_explicitly_disabled():
    ec2 = FakeEc2()
    AwsComputeProvider(ec2).set_source_dest_check("i-fw", enabled=False)
    assert ec2.calls == [("modify_instance_attribute", {
        "InstanceId": "i-fw", "SourceDestCheck": {"Value": False},
    })]


def test_allocate_associate_and_release_owned_eip():
    ec2 = FakeEc2()
    provider = AwsComputeProvider(ec2)
    allocation = provider.allocate_eip({"ManagedBy": "ctf-it", "VmId": "7"})
    association_id = provider.associate_eip(allocation.allocation_id, "eni-primary")
    provider.release_owned_eip(allocation.allocation_id, {"ManagedBy": "ctf-it", "VmId": "7"})

    assert allocation.public_ip == "198.51.100.20"
    assert association_id == "eipassoc-123"
    assert ec2.calls[-2:] == [
        ("disassociate_address", {"AssociationId": "eipassoc-123"}),
        ("release_address", {"AllocationId": "eipalloc-123"}),
    ]


def test_throttling_is_translated_to_retryable_error():
    from botocore.exceptions import ClientError

    ec2 = FakeEc2()
    ec2.error = ClientError(
        {"Error": {"Code": "RequestLimitExceeded", "Message": "slow down"}},
        "DescribeInstances",
    )

    with pytest.raises(AwsRetryableError, match="slow down"):
        AwsComputeProvider(ec2).instance("i-123")
