"""Database adapter for a retained OPNsense acceptance AMI."""

import json
import os
from pathlib import Path

from api.models import OpnsenseImage, PlatformSettings, utcnow
from api.services.opnsense_images import BOOTSTRAP_SOURCE_URL, BUILD_METHOD, SUPPORTED_RELEASES
from api.services.ssh_keys import get_or_create_platform_keypair
from scripts.aws_acceptance_opnsense_cache import CacheIdentity, CachedAmi


def load_acceptance_platform_key(db, path: Path) -> tuple[str, str]:
    """Keep the key baked into a cached acceptance AMI across ephemeral databases."""
    path = Path(path)
    if path.is_file():
        stored = json.loads(path.read_text())
        private_key = stored["private_key"]
        public_key = stored["public_key"]
        if not private_key.startswith("-----BEGIN OPENSSH PRIVATE KEY-----"):
            raise RuntimeError("cached acceptance platform private key is invalid")
        if not public_key.startswith("ssh-ed25519 "):
            raise RuntimeError("cached acceptance platform public key is invalid")
        db.add(PlatformSettings(key="ssh_private_key", value=private_key))
        db.add(PlatformSettings(key="ssh_public_key", value=public_key))
        db.commit()
        return private_key, public_key

    private_key, public_key = get_or_create_platform_keypair(db)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        json.dump({"private_key": private_key, "public_key": public_key}, handle)
    return private_key, public_key


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
