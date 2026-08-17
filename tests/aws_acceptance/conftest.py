import pytest
import json
import os
from types import SimpleNamespace
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .context import require_acceptance_context


@pytest.fixture(scope="session")
def aws_context():
    context = require_acceptance_context()
    before = context.inventory()
    if any(before.values()):
        raise RuntimeError(f"acceptance run ID already owns resources: {before}")
    try:
        yield context
    finally:
        context.cleanup()
        remaining = context.inventory()
        if any(remaining.values()):
            raise RuntimeError(f"AWS acceptance cleanup incomplete: {remaining}")


@pytest.fixture(scope="session")
def aws_opnsense_image(aws_context):
    from api.database import Base
    from api.models import PlatformSettings, utcnow
    from api.services.aws import ownership_tags
    from api.services.opnsense_images import download_bootstrap, new_image, run_image_build
    from scripts.aws_acceptance_opnsense_cache import (
        GOLDEN_CONFIG_REVISION, IMAGE_BUILD_REVISION, CacheIdentity,
        discover_cache, promote_cache,
    )
    from .cache_fixture import materialize_cached_image

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    ec2 = aws_context.ec2()
    version = "26.7"
    bootstrap, bootstrap_digest = download_bootstrap()
    base_ami = aws_context.config.freebsd_ami(aws_context.region)
    base_image = ec2.describe_images(ImageIds=[base_ami])["Images"][0]
    identity = CacheIdentity(
        region=aws_context.region,
        architecture=base_image["Architecture"],
        opnsense_version=version,
        bootstrap_sha256=bootstrap_digest,
        golden_config_revision=GOLDEN_CONFIG_REVISION,
        image_build_revision=IMAGE_BUILD_REVISION,
    )
    force_build = os.environ.get("AWS_ACCEPTANCE_FORCE_OPNSENSE_BUILD") == "1"
    cached = None if force_build else discover_cache(ec2, identity)
    promoted = False
    if cached:
        image = materialize_cached_image(
            db, cached, identity,
            availability_zone=aws_context.config.availability_zone(aws_context.region),
        )
    else:
        image = new_image(db, version)
        run_image_build(
            db, image.id,
            bootstrap_downloader=lambda _url: (bootstrap, bootstrap_digest),
        )
        db.refresh(image)
        if image.status != "ready":
            raise RuntimeError(
                f"OPNsense acceptance AMI failed in {image.phase}: {image.error_detail}"
            )
        evidence = json.loads(image.validation_results or "{}")
        if not all(evidence.get(name, {}).get("passed") for name in ("public_clone", "private_clone")):
            raise RuntimeError("refusing to cache an OPNsense AMI without both clone validations")
        cached = promote_cache(
            ec2, image.ami_id,
            tuple(json.loads(image.backing_snapshot_ids_json or "[]")),
            identity,
            expected_run_tags=ownership_tags("acceptance", site_id=image.id),
        )
        promoted = True
        image.status = image.phase = "active"
        image.activated_at = utcnow()
        db.add(PlatformSettings(key="active_opnsense_image_id", value=str(image.id)))
        db.commit()
    try:
        yield SimpleNamespace(
            image=image, db=db, engine=engine, cached_ami=cached,
            cache_hit=not promoted, forced_build=force_build,
        )
    finally:
        db.close()
        engine.dispose()
