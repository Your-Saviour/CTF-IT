import pytest
import json
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
    from api.services.aws import AwsImageProvider, ownership_tags
    from api.services.opnsense_images import new_image, run_image_build

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    image = new_image(db, "26.7")
    run_image_build(db, image.id)
    db.refresh(image)
    if image.status != "ready":
        raise RuntimeError(f"OPNsense acceptance AMI failed in {image.phase}: {image.error_detail}")
    image.status = image.phase = "active"
    image.activated_at = utcnow()
    db.add(PlatformSettings(key="active_opnsense_image_id", value=str(image.id)))
    db.commit()
    try:
        yield SimpleNamespace(image=image, db=db, engine=engine)
    finally:
        if image.ami_id:
            AwsImageProvider(aws_context.ec2()).retire_owned(
                image.ami_id, json.loads(image.backing_snapshot_ids_json or "[]"),
                ownership_tags("acceptance"),
            )
            image.ami_id = None
            image.backing_snapshot_ids_json = None
            image.status = image.phase = "retired"
            db.commit()
        db.close()
        engine.dispose()
