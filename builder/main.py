import hashlib
import hmac
import io
import json
import os
import shutil
import tarfile

import docker

from builder.module_loader import load_all_modules
from builder.registry import generate_image_tag, push_image
from builder.renderer import prepare_build_context
from builder.selector import select_modules


def generate_flag(secret_key: str, user_id: str) -> str:
    return hmac.HMAC(
        secret_key.encode(), user_id.encode(), hashlib.sha256
    ).hexdigest()


def _extract_build_state(client, image_tag: str, platform: str = None) -> dict:
    """Extract /opt/ctf/state.json from a built image to store server-side."""
    create_kwargs = {"image": image_tag}
    if platform:
        create_kwargs["platform"] = platform
    container = client.containers.create(**create_kwargs)
    try:
        bits, _ = container.get_archive("/opt/ctf/state.json")
        raw = b"".join(bits)
        tar = tarfile.open(fileobj=io.BytesIO(raw))
        member = tar.getmembers()[0]
        f = tar.extractfile(member)
        return json.loads(f.read())
    finally:
        container.remove()


def build_image_for_user(user_id: str, quota: dict) -> dict:
    secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")

    library = load_all_modules()
    selected = select_modules(quota, library)
    flag = generate_flag(secret_key, user_id)
    image_tag = generate_image_tag()

    context_dir = prepare_build_context(user_id, selected, flag, image_tag)

    try:
        client = docker.from_env()
        platform = os.environ.get("DOCKER_PLATFORM")
        build_kwargs = dict(
            path=str(context_dir),
            tag=image_tag,
            buildargs={"FLAG": flag},
            rm=True,
        )
        if platform:
            build_kwargs["platform"] = platform
        client.images.build(**build_kwargs)

        # Extract build-time state before pushing
        build_state = _extract_build_state(client, image_tag, platform)

        push_image(image_tag)
    finally:
        shutil.rmtree(context_dir, ignore_errors=True)

    return {
        "image_tag": image_tag,
        "modules": selected,
        "flag": flag,
        "build_state": json.dumps(build_state.get("snapshots", {})),
    }
