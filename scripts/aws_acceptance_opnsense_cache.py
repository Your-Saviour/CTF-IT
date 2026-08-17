#!/usr/bin/env python3
"""Ownership-safe lifecycle for reusable OPNsense AWS acceptance AMIs."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


CACHE_ROLE = "opnsense-acceptance-cache"
GOLDEN_CONFIG_REVISION = "aws-ena-golden-v1"
IMAGE_BUILD_REVISION = "pkgbase-sanitized-clone-v2"
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
    core_commit: str
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
        "core_commit": identity.core_commit,
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


def _assert_run_owned(resource_type: str, resource_id: str, tags,
                      expected: dict[str, str]) -> None:
    actual = _tag_dict(tags)
    if any(actual.get(name) != value for name, value in expected.items()):
        raise RuntimeError(f"{resource_type} {resource_id} run ownership tags do not match")


def promote_cache(ec2, ami_id: str, snapshot_ids: tuple[str, ...],
                  identity: CacheIdentity, *, expected_run_tags: dict[str, str],
                  now: datetime | None = None, retention_days: int = 7) -> CachedAmi:
    if retention_days < 1:
        raise ValueError("cache retention must be at least one day")
    image_rows = ec2.describe_images(ImageIds=[ami_id]).get("Images", [])
    if len(image_rows) != 1:
        raise RuntimeError(f"AMI {ami_id} is missing or ambiguous during cache promotion")
    image = image_rows[0]
    _assert_run_owned("AMI", ami_id, image.get("Tags"), expected_run_tags)
    mapped_snapshots = tuple(
        mapping["Ebs"]["SnapshotId"]
        for mapping in image.get("BlockDeviceMappings", [])
        if mapping.get("Ebs", {}).get("SnapshotId")
    )
    if mapped_snapshots != tuple(snapshot_ids):
        raise RuntimeError(f"AMI {ami_id} snapshot set does not match the validated build")
    rows = ec2.describe_snapshots(SnapshotIds=list(snapshot_ids)).get("Snapshots", [])
    by_id = {row["SnapshotId"]: row for row in rows}
    for snapshot_id in snapshot_ids:
        if snapshot_id not in by_id:
            raise RuntimeError(f"snapshot {snapshot_id} is missing during cache promotion")
        _assert_run_owned(
            "snapshot", snapshot_id, by_id[snapshot_id].get("Tags"), expected_run_tags,
        )

    created = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    expires = created + timedelta(days=retention_days)
    promoted_tags = cache_tags(identity, created_at=created, expires_at=expires)
    resources = [ami_id, *snapshot_ids]
    ec2.create_tags(
        Resources=resources,
        Tags=[{"Key": name, "Value": value} for name, value in promoted_tags.items()],
    )
    run_only = [
        {"Key": name} for name in expected_run_tags
        if name not in {"Application", "ManagedBy", "Environment"}
    ]
    if run_only:
        ec2.delete_tags(Resources=resources, Tags=run_only)
    return CachedAmi(ami_id, tuple(snapshot_ids), cache_key(identity), expires)


def validate_account(expected_account_id: str, actual_account_id: str) -> None:
    if not expected_account_id or not expected_account_id.isdigit():
        raise ValueError("the expected AWS account ID is required")
    if actual_account_id != expected_account_id:
        raise RuntimeError(
            f"refusing AWS account {actual_account_id}; expected {expected_account_id}"
        )


def _cache_images(ec2) -> list[dict]:
    filters = [
        {"Name": f"tag:{name}", "Values": [value]}
        for name, value in CACHE_OWNERSHIP.items()
    ]
    return ec2.describe_images(Owners=["self"], Filters=filters).get("Images", [])


def cache_inventory(ec2) -> dict[str, list[str]]:
    images = _cache_images(ec2)
    return {
        "images": [row["ImageId"] for row in images],
        "snapshots": sorted({
            mapping["Ebs"]["SnapshotId"]
            for row in images
            for mapping in row.get("BlockDeviceMappings", [])
            if mapping.get("Ebs", {}).get("SnapshotId")
        }),
    }


def cleanup_cache(ec2, *, all_artifacts: bool = False,
                  now: datetime | None = None) -> dict[str, list[str]]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    candidates = _cache_images(ec2)
    selected: list[dict] = []
    selected_ids: set[str] = set()
    snapshot_keys: dict[str, str] = {}
    for image in candidates:
        tags = _tag_dict(image.get("Tags"))
        key = tags.get("CacheKey", "")
        _assert_cache_owned("AMI", image["ImageId"], image.get("Tags"), key)
        expiry = _parse_expiry(tags.get("ExpiresAt", ""))
        if not all_artifacts and expiry > current:
            continue
        selected.append(image)
        selected_ids.add(image["ImageId"])
        for mapping in image.get("BlockDeviceMappings", []):
            snapshot_id = mapping.get("Ebs", {}).get("SnapshotId")
            if snapshot_id:
                previous = snapshot_keys.setdefault(snapshot_id, key)
                if previous != key:
                    raise RuntimeError(
                        f"snapshot {snapshot_id} belongs to multiple cache identities"
                    )

    snapshot_ids = sorted(snapshot_keys)
    if snapshot_ids:
        rows = ec2.describe_snapshots(SnapshotIds=snapshot_ids).get("Snapshots", [])
        by_id = {row["SnapshotId"]: row for row in rows}
        for snapshot_id, key in snapshot_keys.items():
            if snapshot_id not in by_id:
                raise RuntimeError(f"cached OPNsense snapshot {snapshot_id} is missing")
            _assert_cache_owned(
                "snapshot", snapshot_id, by_id[snapshot_id].get("Tags"), key,
            )

    all_images = ec2.describe_images(Owners=["self"]).get("Images", [])
    for image in all_images:
        if image["ImageId"] in selected_ids:
            continue
        for mapping in image.get("BlockDeviceMappings", []):
            snapshot_id = mapping.get("Ebs", {}).get("SnapshotId")
            if snapshot_id in snapshot_keys:
                raise RuntimeError(
                    f"snapshot {snapshot_id} is referenced by remaining AMI {image['ImageId']}"
                )

    removed = {"images": [], "snapshots": []}
    for image in selected:
        ec2.deregister_image(ImageId=image["ImageId"])
        removed["images"].append(image["ImageId"])
    for snapshot_id in snapshot_ids:
        ec2.delete_snapshot(SnapshotId=snapshot_id)
        removed["snapshots"].append(snapshot_id)
    return removed


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect or clean owned OPNsense cache AMIs")
    parser.add_argument("--expected-account-id", required=True)
    parser.add_argument("--all", action="store_true", dest="all_artifacts")
    parser.add_argument("--inventory-only", action="store_true")
    args = parser.parse_args()

    import boto3

    session = boto3.Session()
    actual_account_id = session.client("sts").get_caller_identity()["Account"]
    validate_account(args.expected_account_id, actual_account_id)
    ec2 = session.client("ec2")
    if not args.inventory_only:
        print(json.dumps({"removed": cleanup_cache(
            ec2, all_artifacts=args.all_artifacts,
        )}, sort_keys=True))
    print(json.dumps({"remaining": cache_inventory(ec2)}, sort_keys=True))


if __name__ == "__main__":
    main()
