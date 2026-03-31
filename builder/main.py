import hashlib
import hmac
import os
import shutil

import docker

from builder.module_loader import load_all_modules
from builder.registry import generate_image_tag, push_image
from builder.renderer import prepare_build_context
from builder.selector import select_modules


def generate_flag(secret_key: str, user_id: str) -> str:
    return hmac.HMAC(
        secret_key.encode(), user_id.encode(), hashlib.sha256
    ).hexdigest()


def build_image_for_user(user_id: str, quota: dict) -> dict:
    secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")

    library = load_all_modules()
    selected = select_modules(quota, library)
    flag = generate_flag(secret_key, user_id)
    image_tag = generate_image_tag()

    context_dir = prepare_build_context(user_id, selected, flag, image_tag)

    try:
        client = docker.from_env()
        client.images.build(
            path=str(context_dir),
            tag=image_tag,
            buildargs={"FLAG": flag},
            rm=True,
        )
        push_image(image_tag)
    finally:
        shutil.rmtree(context_dir, ignore_errors=True)

    return {
        "image_tag": image_tag,
        "modules": selected,
        "flag": flag,
    }
