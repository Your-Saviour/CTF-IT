from collections.abc import Mapping
import os

from .errors import AwsOwnershipError


_ID_TAGS = {
    "event_id": "EventId",
    "team_id": "TeamId",
    "site_id": "SiteId",
    "vm_id": "VmId",
}


def ownership_tags(environment: str, **ids: int | None) -> dict[str, str]:
    tags = {
        "Application": "ctf-it",
        "ManagedBy": "ctf-it",
        "Environment": environment,
    }
    if environment == "acceptance" and os.environ.get("RUN_AWS_ACCEPTANCE") == "1":
        run_id = os.environ.get("AWS_ACCEPTANCE_RUN_ID", "").strip()
        if len(run_id) < 8:
            raise ValueError("AWS_ACCEPTANCE_RUN_ID must contain at least eight characters")
        tags["AcceptanceRunId"] = run_id
    for key, value in ids.items():
        if key not in _ID_TAGS:
            raise ValueError(f"Unsupported ownership identifier: {key}")
        if value is not None:
            tags[_ID_TAGS[key]] = str(value)
    return tags


def assert_owned(actual_tags: Mapping[str, str], expected_tags: Mapping[str, str]) -> None:
    for key, expected in expected_tags.items():
        actual = actual_tags.get(key)
        if actual != expected:
            raise AwsOwnershipError(
                f"AWS resource ownership mismatch for {key}: expected {expected!r}, got {actual!r}"
            )


def aws_tag_list(tags: Mapping[str, str]) -> list[dict[str, str]]:
    return [{"Key": key, "Value": value} for key, value in sorted(tags.items())]


def aws_tag_dict(tags: list[dict[str, str]] | None) -> dict[str, str]:
    return {tag["Key"]: tag["Value"] for tag in tags or []}
