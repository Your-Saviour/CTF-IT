#!/usr/bin/env python3
"""Ownership-safe lifecycle for reusable OPNsense AWS acceptance AMIs."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone


CACHE_ROLE = "opnsense-acceptance-cache"
CACHE_OWNERSHIP = {
    "Application": "ctf-it",
    "ManagedBy": "ctf-it",
    "Environment": "acceptance",
    "ArtifactRole": CACHE_ROLE,
}


@dataclass(frozen=True)
class CacheIdentity:
    region: str
    architecture: str
    opnsense_version: str
    bootstrap_sha256: str
    golden_config_revision: str
    image_build_revision: str


@dataclass(frozen=True)
class CachedAmi:
    ami_id: str
    snapshot_ids: tuple[str, ...]
    cache_key: str
    expires_at: datetime


def cache_key(identity: CacheIdentity) -> str:
    payload = {
        "architecture": identity.architecture,
        "bootstrap_sha256": identity.bootstrap_sha256,
        "golden_config_revision": identity.golden_config_revision,
        "image_build_revision": identity.image_build_revision,
        "opnsense_version": identity.opnsense_version,
        "region": identity.region,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def cache_tags(identity: CacheIdentity, *, created_at: datetime,
               expires_at: datetime) -> dict[str, str]:
    return {
        **CACHE_OWNERSHIP,
        "CacheKey": cache_key(identity),
        "CreatedAt": created_at.astimezone(timezone.utc).isoformat(),
        "ExpiresAt": expires_at.astimezone(timezone.utc).isoformat(),
    }


def _tag_dict(tags) -> dict[str, str]:
    return {row["Key"]: row["Value"] for row in tags or []}


def _assert_cache_owned(resource_type: str, resource_id: str, tags,
                        key: str) -> dict[str, str]:
    actual = _tag_dict(tags)
    expected = {**CACHE_OWNERSHIP, "CacheKey": key}
    if any(actual.get(name) != value for name, value in expected.items()):
        raise RuntimeError(
            f"{resource_type} {resource_id} cache ownership tags do not match"
        )
    return actual


def _parse_expiry(value: str) -> datetime:
    try:
        expiry = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise RuntimeError("cached OPNsense AMI has an invalid expiry") from exc
    if expiry.tzinfo is None:
        raise RuntimeError("cached OPNsense AMI expiry must include a timezone")
    return expiry.astimezone(timezone.utc)


def discover_cache(ec2, identity: CacheIdentity, *,
                   now: datetime | None = None) -> CachedAmi | None:
    key = cache_key(identity)
    filters = [
        {"Name": f"tag:{name}", "Values": [value]}
        for name, value in {**CACHE_OWNERSHIP, "CacheKey": key}.items()
    ]
    images = ec2.describe_images(Owners=["self"], Filters=filters).get("Images", [])
    if len(images) > 1:
        raise RuntimeError("multiple cached OPNsense AMIs match one cache key")
    if not images or images[0].get("State") != "available":
        return None
    image = images[0]
    tags = _assert_cache_owned("AMI", image["ImageId"], image.get("Tags"), key)
    expiry = _parse_expiry(tags.get("ExpiresAt", ""))
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if expiry <= current:
        return None
    snapshot_ids = tuple(
        mapping["Ebs"]["SnapshotId"]
        for mapping in image.get("BlockDeviceMappings", [])
        if mapping.get("Ebs", {}).get("SnapshotId")
    )
    if not snapshot_ids:
        raise RuntimeError(f"cached OPNsense AMI {image['ImageId']} has no EBS snapshots")
    snapshots = ec2.describe_snapshots(SnapshotIds=list(snapshot_ids)).get("Snapshots", [])
    by_id = {row["SnapshotId"]: row for row in snapshots}
    for snapshot_id in snapshot_ids:
        try:
            snapshot = by_id[snapshot_id]
        except KeyError as exc:
            raise RuntimeError(f"cached OPNsense snapshot {snapshot_id} is missing") from exc
        _assert_cache_owned("snapshot", snapshot_id, snapshot.get("Tags"), key)
    return CachedAmi(image["ImageId"], snapshot_ids, key, expiry)
