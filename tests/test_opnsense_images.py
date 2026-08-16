import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.database import Base
from api.models import OpnsenseImage, PlatformSettings, utcnow
from api.services.opnsense_images import (
    BOOTSTRAP_SOURCE_URL, ImageWorkflowError, active_image, elapsed_seconds,
    _ssh, interrupt_running_jobs, new_image, release_matches, run_image_build,
    validate_bootstrap_url, validate_control_plane_cidr, validate_release,
)


def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def image_row(db, **overrides):
    values = dict(version="26.7", build_method="freebsd-bootstrap", base_os="FreeBSD 15 x64",
                  bootstrap_source_url=BOOTSTRAP_SOURCE_URL,
                  status="creating_builder", phase="creating_builder")
    values.update(overrides)
    row = OpnsenseImage(**values); db.add(row); db.commit(); return row


def test_release_and_bootstrap_inputs_are_strict():
    assert validate_release("26.7") == "26.7"
    assert release_matches("26.7.2_2", "26.7")
    assert not release_matches("27.7", "26.7")
    with pytest.raises(ValueError): validate_release("26.7/../../")
    with pytest.raises(ImageWorkflowError): validate_bootstrap_url("https://example.com/bootstrap.sh")


@pytest.mark.parametrize("value", ["", "not-a-cidr", "10.0.0.1", "2001:db8::/64"])
def test_control_plane_cidr_must_be_ipv4(value):
    with pytest.raises(ImageWorkflowError): validate_control_plane_cidr(value)


@pytest.mark.parametrize("created_at", [
    datetime(2026, 8, 15, 3, 0, 0),
    datetime(2026, 8, 15, 3, 0, 0, tzinfo=timezone.utc),
])
def test_build_duration_accepts_naive_or_aware_utc(created_at):
    assert elapsed_seconds(type("Image", (), {"created_at": created_at})()) >= 0


def test_running_jobs_interrupt_and_only_validated_active_ami_is_selected():
    db = session(); image = image_row(db, status="bootstrapping", phase="bootstrapping")
    assert interrupt_running_jobs(db) == 1 and image.status == "interrupted"
    image.status = image.phase = "active"; image.ami_id = "ami-opnsense"; image.validated_at = utcnow()
    db.add(PlatformSettings(key="active_opnsense_image_id", value=str(image.id))); db.commit()
    assert active_image(db).id == image.id


def test_aws_image_workflow_persists_ami_snapshots_and_validation_evidence(monkeypatch):
    db = session(); monkeypatch.setenv("CTF_CONTROL_PLANE_CIDR", "203.0.113.0/24")
    class Provider:
        cleaned = False
        def preflight(self, base_os):
            assert base_os == "FreeBSD 15 x64"
            return {"region": "ap-southeast-2", "availability_zone": "ap-southeast-2a"}
        def build(self, db, image, bootstrap_downloader):
            return {
                "builder_instance_id": "i-builder", "builder_vpc_id": "vpc-builder",
                "builder_subnet_id": "subnet-public", "validation_subnet_id": "subnet-private",
                "ami_id": "ami-opnsense", "snapshot_ids": ["snap-root"],
                "validation_results": {"public_clone": {"passed": True},
                                       "private_clone": {"passed": True}},
            }
        def cleanup_temporary(self, image, result):
            assert result["ami_id"] == "ami-opnsense"
            self.cleaned = True
    provider = Provider()
    image = new_image(db, "26.7", provider_factory=lambda: provider)
    run_image_build(db, image.id, provider_factory=lambda: provider)
    db.refresh(image)
    assert image.status == image.phase == "ready" and image.ami_id == "ami-opnsense"
    assert json.loads(image.backing_snapshot_ids_json) == ["snap-root"]
    assert json.loads(image.validation_results)["private_clone"]["passed"] is True
    assert provider.cleaned is True


def test_activation_source_rejects_unvalidated_ami(monkeypatch):
    db = session(); monkeypatch.setenv("CTF_CONTROL_PLANE_CIDR", "203.0.113.0/24")
    class Provider:
        def preflight(self, _): return {"region": "ap-southeast-2", "availability_zone": "ap-southeast-2a"}
        def build(self, *_):
            return {"ami_id": "ami-bad", "snapshot_ids": [],
                    "validation_results": {"public_clone": {"passed": True},
                                           "private_clone": {"passed": False}}}
    image = new_image(db, "26.7", provider_factory=lambda: Provider())
    run_image_build(db, image.id, provider_factory=lambda: Provider())
    assert image.status == "failed" and active_image(db) is None


def test_freebsd_cloud_user_fallback_uses_available_privilege_escalation(monkeypatch):
    from paramiko import AuthenticationException
    from api.services import opnsense_images

    attempts = []
    commands = []

    class Stream:
        def __init__(self, content=b""):
            self.content = content
            self.channel = self
        def recv_exit_status(self): return 0
        def read(self): return self.content

    class Client:
        def set_missing_host_key_policy(self, _policy): pass
        def connect(self, _host, *, username, **_kwargs):
            attempts.append(username)
            if username == "root":
                raise AuthenticationException("root disabled")
        def exec_command(self, command, **_kwargs):
            commands.append(command)
            return Stream(), Stream(b"amd64\n"), Stream()
        def close(self): pass

    times = iter((0, 0, 2))
    monkeypatch.setattr(opnsense_images, "POLL_TIMEOUT", 1)
    monkeypatch.setattr(opnsense_images.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(opnsense_images.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(opnsense_images.paramiko, "SSHClient", Client)
    monkeypatch.setattr(opnsense_images.paramiko.Ed25519Key, "from_private_key", lambda _value: object())
    monkeypatch.setattr(opnsense_images, "get_or_create_platform_keypair", lambda _db: ("private", "public"))

    assert _ssh(object(), "198.51.100.10", "uname -m") == (0, "amd64\n", "")
    assert attempts == ["root", "freebsd"]
    assert len(commands) == 1
    assert "/usr/local/bin/doas" in commands[0]
    assert "/usr/local/bin/sudo" in commands[0]
    assert "sudo -n" in commands[0]
    assert "uname -m" in commands[0]
