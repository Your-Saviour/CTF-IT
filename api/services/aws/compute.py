from dataclasses import dataclass
from typing import Mapping

from botocore.exceptions import ClientError

from .errors import AwsQuotaError, AwsRetryableError, AwsTerminalError
from .tags import assert_owned, aws_tag_dict, aws_tag_list
from .types import ElasticIpResult, InstanceResult


_RETRYABLE_CODES = {
    "InternalError", "InternalFailure", "RequestLimitExceeded", "ServiceUnavailable",
    "Throttling", "ThrottlingException", "Unavailable",
}
_QUOTA_CODES = {
    "AddressLimitExceeded", "InstanceLimitExceeded", "NetworkInterfaceLimitExceeded",
    "VcpuLimitExceeded", "VpcLimitExceeded",
}


@dataclass(frozen=True)
class NetworkInterfaceSpec:
    device_index: int
    subnet_id: str | None = None
    security_group_ids: tuple[str, ...] = ()
    eni_id: str | None = None
    associate_public_ip: bool = False
    delete_on_termination: bool = True
    private_ip: str | None = None

    def request(self) -> dict:
        value = {
            "DeviceIndex": self.device_index,
            "DeleteOnTermination": self.delete_on_termination,
        }
        if self.eni_id:
            value["NetworkInterfaceId"] = self.eni_id
        else:
            value["SubnetId"] = self.subnet_id
            value["Groups"] = list(self.security_group_ids)
            value["AssociatePublicIpAddress"] = self.associate_public_ip
            if self.private_ip:
                value["PrivateIpAddress"] = self.private_ip
        return value


@dataclass(frozen=True)
class InstanceSpec:
    ami_id: str
    instance_type: str
    client_token: str
    network_interfaces: tuple[NetworkInterfaceSpec, ...]
    tags: Mapping[str, str]
    key_name: str | None = None
    user_data: str = ""


class AwsComputeProvider:
    def __init__(self, ec2_client):
        self.ec2 = ec2_client

    def _call(self, operation: str, **kwargs):
        try:
            return getattr(self.ec2, operation)(**kwargs)
        except ClientError as exc:
            error = exc.response.get("Error", {})
            code = error.get("Code", "Unknown")
            message = error.get("Message", str(exc))
            if code in _RETRYABLE_CODES or code.endswith("NotFound"):
                raise AwsRetryableError(f"{operation}: {message}") from exc
            if code in _QUOTA_CODES:
                raise AwsQuotaError(f"{operation}: {message}") from exc
            raise AwsTerminalError(f"{operation}: {code}: {message}") from exc

    @staticmethod
    def _result(instance: dict) -> InstanceResult:
        interfaces = instance.get("NetworkInterfaces", [])
        primary = next(
            (eni for eni in interfaces if eni.get("Attachment", {}).get("DeviceIndex") == 0),
            interfaces[0] if interfaces else None,
        )
        return InstanceResult(
            instance_id=instance["InstanceId"],
            state=instance.get("State", {}).get("Name", "unknown"),
            primary_eni_id=primary.get("NetworkInterfaceId") if primary else None,
            public_ip=instance.get("PublicIpAddress"),
            private_ip=instance.get("PrivateIpAddress"),
            availability_zone=instance.get("Placement", {}).get("AvailabilityZone"),
        )

    def _owned_result(self, instance: dict, tags: Mapping[str, str]) -> InstanceResult:
        assert_owned(aws_tag_dict(instance.get("Tags")), tags)
        return self._result(instance)

    def _find_by_client_token(self, token: str) -> dict | None:
        response = self._call(
            "describe_instances",
            Filters=[
                {"Name": "client-token", "Values": [token]},
                {"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"]},
            ],
        )
        return next(
            (instance for reservation in response.get("Reservations", [])
             for instance in reservation.get("Instances", [])),
            None,
        )

    def launch_instance(self, spec: InstanceSpec) -> InstanceResult:
        existing = self._find_by_client_token(spec.client_token)
        if existing:
            return self._owned_result(existing, spec.tags)
        request = {
            "ImageId": spec.ami_id,
            "InstanceType": spec.instance_type,
            "ClientToken": spec.client_token,
            "MinCount": 1,
            "MaxCount": 1,
            "MetadataOptions": {"HttpTokens": "required", "HttpEndpoint": "enabled"},
            "NetworkInterfaces": [interface.request() for interface in spec.network_interfaces],
            "TagSpecifications": [
                {"ResourceType": resource, "Tags": aws_tag_list(spec.tags)}
                for resource in ("instance", "volume")
            ],
        }
        if spec.key_name:
            request["KeyName"] = spec.key_name
        if spec.user_data:
            request["UserData"] = spec.user_data
        instance = self._call("run_instances", **request)["Instances"][0]
        return self._result(instance)

    def instance(self, instance_id: str) -> InstanceResult:
        response = self._call("describe_instances", InstanceIds=[instance_id])
        try:
            instance = response["Reservations"][0]["Instances"][0]
        except (KeyError, IndexError) as exc:
            raise AwsRetryableError(f"instance {instance_id} is not visible yet") from exc
        return self._result(instance)

    def terminate_owned(self, instance_id: str, expected_tags: Mapping[str, str]) -> None:
        response = self._call("describe_instances", InstanceIds=[instance_id])
        instance = response["Reservations"][0]["Instances"][0]
        assert_owned(aws_tag_dict(instance.get("Tags")), expected_tags)
        self._call("terminate_instances", InstanceIds=[instance_id])

    def set_source_dest_check(self, instance_id: str, *, enabled: bool) -> None:
        self._call(
            "modify_instance_attribute",
            InstanceId=instance_id,
            SourceDestCheck={"Value": enabled},
        )

    def allocate_eip(self, tags: Mapping[str, str]) -> ElasticIpResult:
        response = self._call(
            "allocate_address",
            Domain="vpc",
            TagSpecifications=[{"ResourceType": "elastic-ip", "Tags": aws_tag_list(tags)}],
        )
        return ElasticIpResult(response["AllocationId"], response["PublicIp"])

    def associate_eip(self, allocation_id: str, eni_id: str) -> str:
        response = self._call(
            "associate_address",
            AllocationId=allocation_id,
            NetworkInterfaceId=eni_id,
            AllowReassociation=False,
        )
        return response["AssociationId"]

    def release_owned_eip(self, allocation_id: str, expected_tags: Mapping[str, str]) -> None:
        response = self._call("describe_addresses", AllocationIds=[allocation_id])
        address = response["Addresses"][0]
        assert_owned(aws_tag_dict(address.get("Tags")), expected_tags)
        self._call("release_address", AllocationId=allocation_id)

    def ensure_key_pair(self, name: str, public_key: str, tags: Mapping[str, str]) -> str:
        try:
            response = self._call("describe_key_pairs", KeyNames=[name])
            key = response["KeyPairs"][0]
            assert_owned(aws_tag_dict(key.get("Tags")), tags)
            return key["KeyPairId"]
        except AwsRetryableError:
            response = self._call(
                "import_key_pair",
                KeyName=name,
                PublicKeyMaterial=public_key.encode(),
                TagSpecifications=[{"ResourceType": "key-pair", "Tags": aws_tag_list(tags)}],
            )
            return response["KeyPairId"]

    def wait_running(self, instance_id: str, *, delay: int = 5, max_attempts: int = 120) -> None:
        self.ec2.get_waiter("instance_running").wait(
            InstanceIds=[instance_id], WaiterConfig={"Delay": delay, "MaxAttempts": max_attempts}
        )

    def wait_stopped(self, instance_id: str, *, delay: int = 5, max_attempts: int = 120) -> None:
        self.ec2.get_waiter("instance_stopped").wait(
            InstanceIds=[instance_id], WaiterConfig={"Delay": delay, "MaxAttempts": max_attempts}
        )

    def wait_terminated(self, instance_id: str, *, delay: int = 5, max_attempts: int = 120) -> None:
        self.ec2.get_waiter("instance_terminated").wait(
            InstanceIds=[instance_id], WaiterConfig={"Delay": delay, "MaxAttempts": max_attempts}
        )
