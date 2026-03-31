import uuid


def generate_image_tag() -> str:
    return f"ctf-{uuid.uuid4()}"


def push_image(image_tag: str) -> str:
    """Stub: no-op. In production, push to ghcr.io."""
    return image_tag
