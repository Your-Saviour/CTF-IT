"""Database adapter for a retained OPNsense acceptance AMI."""

import json

from api.models import OpnsenseImage, PlatformSettings, utcnow
from api.services.opnsense_images import BOOTSTRAP_SOURCE_URL, BUILD_METHOD, SUPPORTED_RELEASES
from scripts.aws_acceptance_opnsense_cache import CacheIdentity, CachedAmi


def materialize_cached_image(db, cached: CachedAmi, identity: CacheIdentity, *,
                             availability_zone: str) -> OpnsenseImage:
    now = utcnow()
    evidence = {
        "cache_hit": {
            "passed": True,
            "cache_key": cached.cache_key,
            "expires_at": cached.expires_at.isoformat(),
        },
    }
    image = OpnsenseImage(
        version=identity.opnsense_version,
        build_method=BUILD_METHOD,
        base_os=SUPPORTED_RELEASES[identity.opnsense_version],
        bootstrap_source_url=BOOTSTRAP_SOURCE_URL,
        bootstrap_sha256=identity.bootstrap_sha256,
        ami_id=cached.ami_id,
        backing_snapshot_ids_json=json.dumps(list(cached.snapshot_ids), sort_keys=True),
        region=identity.region,
        availability_zone=availability_zone,
        validation_results=json.dumps(evidence, sort_keys=True),
        status="active",
        phase="active",
        validated_at=now,
        completed_at=now,
        activated_at=now,
    )
    db.add(image)
    db.flush()
    db.add(PlatformSettings(key="active_opnsense_image_id", value=str(image.id)))
    db.commit()
    return image
