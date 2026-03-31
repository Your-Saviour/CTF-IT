import os
import uuid

import docker

# The Docker daemon runs on the host (socket-mounted), so it resolves
# localhost:5000 as the host's published registry port.
REGISTRY_PUSH_HOST = os.environ.get("REGISTRY_PUSH_HOST", "localhost:5050")


def generate_image_tag() -> str:
    return f"ctf-{uuid.uuid4()}"


def push_image(image_tag: str) -> str:
    """Tag the image for the local registry and push it."""
    client = docker.from_env()
    registry_tag = f"{REGISTRY_PUSH_HOST}/{image_tag}"

    image = client.images.get(image_tag)
    image.tag(registry_tag)

    for line in client.images.push(registry_tag, stream=True, decode=True):
        if "error" in line:
            raise RuntimeError(f"Registry push failed: {line['error']}")

    # Clean up local images — the registry has the authoritative copy
    try:
        client.images.remove(registry_tag, noprune=True)
        client.images.remove(image_tag, noprune=True)
    except docker.errors.APIError:
        pass

    return image_tag
