from datetime import datetime, timedelta, timezone

import pytest

from scripts.aws_acceptance_opnsense_cache import (
    CacheIdentity, cache_key, discover_cache,
)


NOW = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)


def identity(**changes):
    values = {
        "region": "ap-southeast-2",
        "architecture": "x86_64",
        "opnsense_version": "26.7",
        "bootstrap_sha256": "a" * 64,
        "golden_config_revision": "ena-v1",
        "image_build_revision": "pkgbase-v1",
    }
    values.update(changes)
    return CacheIdentity(**values)


def tags(key, expires_at):
    return [
        {"Key": "Application", "Value": "ctf-it"},
        {"Key": "ManagedBy", "Value": "ctf-it"},
        {"Key": "Environment", "Value": "acceptance"},
        {"Key": "ArtifactRole", "Value": "opnsense-acceptance-cache"},
        {"Key": "CacheKey", "Value": key},
        {"Key": "ExpiresAt", "Value": expires_at.isoformat()},
    ]


class Ec2:
    def __init__(self, images=(), snapshots=()):
        self.images = list(images)
        self.snapshots = {row["SnapshotId"]: row for row in snapshots}

    def describe_images(self, **_kwargs):
        return {"Images": self.images}

    def describe_snapshots(self, SnapshotIds):
        return {"Snapshots": [self.snapshots[value] for value in SnapshotIds]}


def image_row(key, *, expires_at=None, state="available", snapshot_id="snap-1"):
    expiry = expires_at or NOW + timedelta(days=7)
    return {
        "ImageId": "ami-1",
        "State": state,
        "Architecture": "x86_64",
        "Tags": tags(key, expiry),
        "BlockDeviceMappings": [{"DeviceName": "/dev/sda1", "Ebs": {
            "SnapshotId": snapshot_id, "DeleteOnTermination": True,
            "VolumeSize": 10, "VolumeType": "gp3",
        }}],
    }


def snapshot_row(key, *, snapshot_id="snap-1", expires_at=None):
    return {
        "SnapshotId": snapshot_id,
        "State": "completed",
        "VolumeSize": 10,
        "Tags": tags(key, expires_at or NOW + timedelta(days=7)),
    }


def test_cache_key_changes_for_every_image_input():
    base = identity()
    original = cache_key(base)

    assert len(original) == 64
    assert len({
        original,
        cache_key(identity(region="us-east-1")),
        cache_key(identity(architecture="arm64")),
        cache_key(identity(opnsense_version="27.1")),
        cache_key(identity(bootstrap_sha256="b" * 64)),
        cache_key(identity(golden_config_revision="ena-v2")),
        cache_key(identity(image_build_revision="pkgbase-v2")),
    }) == 7


def test_discovery_returns_one_available_owned_unexpired_ami():
    item = identity()
    key = cache_key(item)

    result = discover_cache(
        Ec2([image_row(key)], [snapshot_row(key)]), item, now=NOW,
    )

    assert result.ami_id == "ami-1"
    assert result.snapshot_ids == ("snap-1",)
    assert result.cache_key == key
    assert result.expires_at == NOW + timedelta(days=7)


@pytest.mark.parametrize("images", [[], [image_row(cache_key(identity()), state="pending")]])
def test_discovery_treats_missing_or_unavailable_cache_as_a_miss(images):
    assert discover_cache(Ec2(images), identity(), now=NOW) is None


def test_discovery_treats_expired_cache_as_a_miss():
    item = identity()
    key = cache_key(item)
    assert discover_cache(
        Ec2([image_row(key, expires_at=NOW)], [snapshot_row(key, expires_at=NOW)]),
        item, now=NOW,
    ) is None


def test_discovery_refuses_ambiguous_cache_matches():
    item = identity()
    key = cache_key(item)
    with pytest.raises(RuntimeError, match="multiple cached OPNsense AMIs"):
        discover_cache(Ec2([image_row(key), image_row(key)]), item, now=NOW)


def test_discovery_refuses_snapshot_without_matching_cache_ownership():
    item = identity()
    key = cache_key(item)
    snapshot = snapshot_row(key)
    snapshot["Tags"] = [row for row in snapshot["Tags"] if row["Key"] != "ManagedBy"]

    with pytest.raises(RuntimeError, match="snapshot snap-1.*ownership"):
        discover_cache(Ec2([image_row(key)], [snapshot]), item, now=NOW)
