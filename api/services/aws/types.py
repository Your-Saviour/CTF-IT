from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True)
class AwsIdentity:
    account_id: str
    arn: str
    user_id: str


@dataclass(frozen=True)
class NetworkInterfaceResult:
    eni_id: str
    subnet_id: str
    private_ip: str | None = None
    mac_address: str | None = None


@dataclass(frozen=True)
class InstanceResult:
    instance_id: str
    state: str
    primary_eni_id: str | None = None
    public_ip: str | None = None
    private_ip: str | None = None
    availability_zone: str | None = None
    wan_eni_id: str | None = None
    lan_eni_id: str | None = None


@dataclass(frozen=True)
class SiteNetworkResult:
    vpc_id: str
    availability_zone: str
    subnet_ids: Mapping[str, str]
    route_table_ids: Mapping[str, str]
    internet_gateway_id: str


@dataclass(frozen=True)
class ImageResult:
    ami_id: str
    snapshot_ids: tuple[str, ...]
    state: str


@dataclass(frozen=True)
class CleanupResult:
    removed: tuple[str, ...] = field(default_factory=tuple)
    remaining: tuple[str, ...] = field(default_factory=tuple)
