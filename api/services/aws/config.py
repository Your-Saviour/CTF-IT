import json
import os
import hashlib
import re
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
    standard_security_group_ids: tuple[str, ...]
    ubuntu_amis: Mapping[str, str]
    freebsd_amis: Mapping[str, str]
    availability_zones: Mapping[str, str]
    instance_types: tuple[str, ...]
    key_pair_name: str
    resource_scope: str
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
        security_groups = tuple(
            value.strip() for value in _required("AWS_STANDARD_SECURITY_GROUP_IDS").split(",")
            if value.strip()
        )
        if not security_groups:
            raise AwsConfigurationError(
                "AWS_STANDARD_SECURITY_GROUP_IDS must contain at least one security group"
            )
        environment = _required("AWS_ENVIRONMENT")
        key_pair_name = os.environ.get("AWS_KEY_PAIR_NAME", "ctf-it").strip() or "ctf-it"
        resource_scope = f"ctf-it-{environment}"
        if environment == "acceptance" and os.environ.get("RUN_AWS_ACCEPTANCE") == "1":
            run_id = os.environ.get("AWS_ACCEPTANCE_RUN_ID", "").strip()
            if len(run_id) < 8:
                raise AwsConfigurationError(
                    "AWS_ACCEPTANCE_RUN_ID must contain at least eight characters"
                )
            key_pair_name = f"{key_pair_name}-{run_id}"[:255]
            resource_scope = f"{resource_scope}-{run_id}"
        return cls(
            default_region=_required("AWS_DEFAULT_REGION"),
            environment=environment,
            standard_vpc_id=_required("AWS_STANDARD_VPC_ID"),
            standard_subnet_id=_required("AWS_STANDARD_SUBNET_ID"),
            standard_security_group_ids=security_groups,
            ubuntu_amis=_mapping("AWS_UBUNTU_AMIS"),
            freebsd_amis=_mapping("AWS_FREEBSD_AMIS"),
            availability_zones=_mapping("AWS_AVAILABILITY_ZONES"),
            instance_types=instance_types,
            key_pair_name=key_pair_name,
            resource_scope=resource_scope,
            profile=os.environ.get("AWS_PROFILE") or None,
        )

    def resource_token(self, *parts: object) -> str:
        raw = "-".join((self.resource_scope, *(str(part) for part in parts)))
        value = re.sub(r"[^A-Za-z0-9_.:-]+", "-", raw).strip("-")
        if len(value) <= 64:
            return value
        digest = hashlib.sha256(value.encode()).hexdigest()[:12]
        return value[:51].rstrip("-") + "-" + digest

    def _ami(self, mapping: Mapping[str, str], family: str, region: str) -> str:
        try:
            return mapping[region]
        except KeyError as exc:
            raise AwsConfigurationError(f"No approved {family} AMI for region {region}") from exc

    def ubuntu_ami(self, region: str) -> str:
        return self._ami(self.ubuntu_amis, "Ubuntu", region)

    def freebsd_ami(self, region: str) -> str:
        return self._ami(self.freebsd_amis, "FreeBSD", region)

    def availability_zone(self, region: str) -> str:
        try:
            return self.availability_zones[region]
        except KeyError as exc:
            raise AwsConfigurationError(f"No approved Availability Zone for region {region}") from exc
