import json
import os
from dataclasses import dataclass
from typing import Mapping

from .errors import AwsConfigurationError


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise AwsConfigurationError(f"{name} is required")
    return value


def _mapping(name: str) -> dict[str, str]:
    raw = _required(name)
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AwsConfigurationError(f"{name} must be a JSON object") from exc
    if not isinstance(value, dict) or not value or not all(
        isinstance(key, str) and isinstance(item, str) and item.strip()
        for key, item in value.items()
    ):
        raise AwsConfigurationError(f"{name} must map regions to non-empty AMI IDs")
    return {key.strip(): item.strip() for key, item in value.items()}


@dataclass(frozen=True)
class AwsConfig:
    default_region: str
    environment: str
    standard_vpc_id: str
    standard_subnet_id: str
    ubuntu_amis: Mapping[str, str]
    freebsd_amis: Mapping[str, str]
    instance_types: tuple[str, ...]
    profile: str | None = None

    @classmethod
    def from_env(cls) -> "AwsConfig":
        instance_types = tuple(
            value.strip()
            for value in _required("AWS_INSTANCE_TYPES").split(",")
            if value.strip()
        )
        if not instance_types:
            raise AwsConfigurationError("AWS_INSTANCE_TYPES must contain at least one type")
        return cls(
            default_region=_required("AWS_DEFAULT_REGION"),
            environment=_required("AWS_ENVIRONMENT"),
            standard_vpc_id=_required("AWS_STANDARD_VPC_ID"),
            standard_subnet_id=_required("AWS_STANDARD_SUBNET_ID"),
            ubuntu_amis=_mapping("AWS_UBUNTU_AMIS"),
            freebsd_amis=_mapping("AWS_FREEBSD_AMIS"),
            instance_types=instance_types,
            profile=os.environ.get("AWS_PROFILE") or None,
        )

    def _ami(self, mapping: Mapping[str, str], family: str, region: str) -> str:
        try:
            return mapping[region]
        except KeyError as exc:
            raise AwsConfigurationError(f"No approved {family} AMI for region {region}") from exc

    def ubuntu_ami(self, region: str) -> str:
        return self._ami(self.ubuntu_amis, "Ubuntu", region)

    def freebsd_ami(self, region: str) -> str:
        return self._ami(self.freebsd_amis, "FreeBSD", region)
