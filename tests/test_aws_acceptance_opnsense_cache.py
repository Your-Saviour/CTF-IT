import json
from datetime import datetime, timedelta, timezone

import pytest

from scripts.aws_acceptance_opnsense_cache import (
    CacheIdentity, CachedAmi, cache_key, discover_cache, promote_cache,
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

    def create_tags(self, Resources, Tags):
        additions = {row["Key"]: row["Value"] for row in Tags}
        for resource_id in Resources:
            if resource_id.startswith("ami-"):
                row = next(item for item in self.images if item["ImageId"] == resource_id)
            else:
                row = self.snapshots[resource_id]
            current = {item["Key"]: item["Value"] for item in row.get("Tags", [])}
            current.update(additions)
            row["Tags"] = [{"Key": key, "Value": value} for key, value in current.items()]

    def delete_tags(self, Resources, Tags):
        removed = {row["Key"] for row in Tags}
        for resource_id in Resources:
            if resource_id.startswith("ami-"):
                row = next(item for item in self.images if item["ImageId"] == resource_id)
            else:
                row = self.snapshots[resource_id]
            row["Tags"] = [item for item in row.get("Tags", []) if item["Key"] not in removed]


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


def test_promotion_replaces_run_identity_only_after_owned_image_and_snapshots_validate():
    item = identity()
    run_tags = {
        "Application": "ctf-it", "ManagedBy": "ctf-it", "Environment": "acceptance",
        "AcceptanceRunId": "aws260817cache01", "SiteId": "7",
    }
    source_tags = [{"Key": key, "Value": value} for key, value in run_tags.items()]
    image = image_row("unused")
    image["Tags"] = list(source_tags)
    snapshot = snapshot_row("unused")
    snapshot["Tags"] = list(source_tags)
    ec2 = Ec2([image], [snapshot])

    cached = promote_cache(
        ec2, "ami-1", ("snap-1",), item,
        expected_run_tags=run_tags, now=NOW, retention_days=7,
    )

    assert cached.expires_at == NOW + timedelta(days=7)
    for row in (image, snapshot):
        actual = {tag["Key"]: tag["Value"] for tag in row["Tags"]}
        assert actual["ArtifactRole"] == "opnsense-acceptance-cache"
        assert actual["CacheKey"] == cache_key(item)
        assert "AcceptanceRunId" not in actual
        assert "SiteId" not in actual


def test_promotion_does_not_retag_anything_when_snapshot_run_ownership_is_wrong():
    item = identity()
    run_tags = {
        "Application": "ctf-it", "ManagedBy": "ctf-it", "Environment": "acceptance",
        "AcceptanceRunId": "aws260817cache01", "SiteId": "7",
    }
    source_tags = [{"Key": key, "Value": value} for key, value in run_tags.items()]
    image = image_row("unused")
    image["Tags"] = list(source_tags)
    snapshot = snapshot_row("unused")
    snapshot["Tags"] = [tag for tag in source_tags if tag["Key"] != "ManagedBy"]
    ec2 = Ec2([image], [snapshot])

    with pytest.raises(RuntimeError, match="snapshot snap-1.*run ownership"):
        promote_cache(
            ec2, "ami-1", ("snap-1",), item,
            expected_run_tags=run_tags, now=NOW,
        )

    assert {tag["Key"] for tag in image["Tags"]} == set(run_tags)


def test_cached_artifact_materializes_the_active_database_image_contract():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from api.database import Base
    from api.models import PlatformSettings
    from tests.aws_acceptance.cache_fixture import materialize_cached_image

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    item = identity()
    cached = CachedAmi(
        "ami-cached", ("snap-a", "snap-b"), cache_key(item), NOW + timedelta(days=7),
    )

    image = materialize_cached_image(
        db, cached, item, availability_zone="ap-southeast-2a",
    )

    assert image.status == image.phase == "active"
    assert image.ami_id == "ami-cached"
    assert image.bootstrap_sha256 == "a" * 64
    assert image.region == "ap-southeast-2"
    assert image.availability_zone == "ap-southeast-2a"
    assert image.backing_snapshot_ids_json == '["snap-a", "snap-b"]'
    assert json.loads(image.validation_results)["cache_hit"]["passed"] is True
    assert db.query(PlatformSettings).filter_by(key="active_opnsense_image_id").one().value == str(image.id)
