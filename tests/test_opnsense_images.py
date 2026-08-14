import bz2
import hashlib
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.database import Base
from api.models import OpnsenseImage, PlatformSettings, utcnow
from api.services.opnsense_images import (
    ImageWorkflowError, VultrImageClient, active_image, artifact_urls, cleanup_validated_image, decompress_bz2,
    _builder_validation_command, _wait_for_builder_validation, complete_install, generic_builder_setup_script,
    interrupt_running_jobs, parse_published_checksum, stream_download, validate_release,
)


def test_release_and_official_urls_are_strict():
    assert validate_release("26.7") == "26.7"
    with pytest.raises(ValueError):
        validate_release("26.7/../../evil")
    urls = artifact_urls("26.7")
    assert urls["artifact"] == "https://pkg.opnsense.org/releases/mirror/OPNsense-26.7-dvd-amd64.iso.bz2"
    assert urls["checksum"].endswith("OPNsense-26.7-checksums-amd64.sha256")
    assert urls["signature"].endswith("OPNsense-26.7-dvd-amd64.iso.sig")


def test_checksum_decompression_and_atomic_download(tmp_path):
    raw = b"OPNsense ISO contents" * 100
    compressed = bz2.compress(raw)
    digest = hashlib.sha256(compressed).hexdigest()
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, content=compressed))
    with httpx.Client(transport=transport) as client:
        target = tmp_path / "image.iso.bz2"
        assert stream_download(client, "https://mirror.invalid/image.iso.bz2", target) == digest
    assert not (tmp_path / "image.iso.bz2.part").exists()
    assert parse_published_checksum(f"SHA256 (image.iso.bz2) = {digest}\n", target.name) == digest
    output = tmp_path / "image.iso"
    assert decompress_bz2(target, output) == hashlib.sha256(raw).hexdigest()
    assert output.read_bytes() == raw


def test_download_rejects_redirect_and_removes_partial(tmp_path):
    transport = httpx.MockTransport(lambda _request: httpx.Response(302, headers={"location": "https://evil.invalid/x"}))
    with httpx.Client(transport=transport) as client, pytest.raises(ImageWorkflowError):
        stream_download(client, "https://mirror.invalid/image", tmp_path / "image")
    assert not (tmp_path / "image.part").exists()


def test_installed_validation_retries_live_media_transition(monkeypatch):
    attempts = iter([
        (1, "", "medium was live"),
        (0, "26.7\n", ""),
    ])
    monkeypatch.setattr("api.services.opnsense_images.POLL_SECONDS", 0)
    monkeypatch.setattr("api.services.opnsense_images._builder_ssh", lambda *_args, **_kwargs: next(attempts))

    assert _wait_for_builder_validation(
        None, "198.51.100.10", "check", version="26.7",
        label="first installed-disk validation",
    ) == "26.7\n"


def test_only_validated_active_image_is_returned_and_jobs_interrupt():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    urls = artifact_urls("26.7")
    image = OpnsenseImage(version="26.7", artifact_url=urls["artifact"], checksum_url=urls["checksum"],
                          signature_url=urls["signature"], status="downloading", phase="downloading")
    session.add(image); session.commit()
    assert interrupt_running_jobs(session) == 1
    assert image.status == "interrupted"
    image.status = image.phase = "active"; image.snapshot_id = "snap"; image.validated_at = image.created_at
    session.add(PlatformSettings(key="active_opnsense_image_id", value=str(image.id))); session.commit()
    assert active_image(session).id == image.id


@pytest.mark.parametrize("cidr", ["", "not-a-cidr", "10.0.0.1", "2001:db8::/64", "10.0.0.0/99"])
def test_invalid_control_plane_cidr_creates_no_firewall(monkeypatch, cidr):
    client = object.__new__(VultrImageClient)
    calls = []
    client.request = lambda *args, **kwargs: calls.append((args, kwargs))
    monkeypatch.setenv("CTF_CONTROL_PLANE_CIDR", cidr)
    with pytest.raises(ImageWorkflowError):
        client.create_firewall("26.7")
    assert calls == []


def test_iso_route_directory_is_traversable_by_read_only_sidecar():
    source = Path("api/services/opnsense_images.py").read_text()
    assert "route_dir.chmod(0o755)" in source


def test_builder_setup_creates_key_only_wan_config_before_install(monkeypatch):
    monkeypatch.setattr("api.services.opnsense_images.get_or_create_platform_keypair",
                        lambda _db: ("private", "ssh-ed25519 TEST"))
    script = generic_builder_setup_script(
        None, control_plane_cidr="192.0.2.8/32", build_nonce="one-time-token"
    )
    assert 'require_once("config.inc")' in script
    assert 'write_config("Configure CTF WAN-only OPNsense image builder")' in script
    assert "count($devices) !== 1" in script
    assert '$config["interfaces"] = ["wan" =>' in script
    assert '"interfaces" => "wan"' in script
    assert 'passwordauth' not in script.lower()
    assert '$user["password"]' in script
    assert '$config["OPNsense"]["Firewall"]["Filter"]["rules"]' in script
    assert '"source_net" => "192.0.2.8/32"' in script
    assert "sync_user.php -u root" in script
    assert "configctl interface reconfigure wan" in script
    assert "configctl interface reconfigure lan" not in script
    assert "configctl openssh restart" in script
    assert "configctl filter reload" in script
    assert "pfctl -d" not in script


def test_detach_iso_uses_official_endpoint_and_is_idempotent(monkeypatch):
    client = object.__new__(VultrImageClient)
    calls = []
    def request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {"iso_status": {"iso_id": None}}
    monkeypatch.setattr(client, "request", request)

    client.detach_iso(SimpleNamespace(builder_instance_id="builder-1"))

    assert calls == [
        ("GET", "/instances/builder-1/iso", {}),
        ("GET", "/instances/builder-1/iso", {}),
    ]


def test_builder_validation_checks_effective_network_ssh_and_pf_state():
    command = _builder_validation_command(
        public_ip="198.51.100.10", version="26.7", control_plane_cidr="192.0.2.8/32",
        build_nonce="one-time-token", installed=True,
    )
    assert "198.51.100.10" in command
    assert "is_install_media" in command
    assert "has_lan" in command
    assert "permitrootlogin" in command
    assert "passwordauthentication no" in command
    assert "kbdinteractiveauthentication no" in command
    assert "pfctl -sr" in command
    assert "from 192.0.2.8 to" in command
    assert "route -n get default" in command


def test_vultr_get_retries_transient_server_errors(monkeypatch):
    responses = [httpx.Response(500, text="temporary"), httpx.Response(200, json={"instance": {"id": "ok"}})]
    client = object.__new__(VultrImageClient)
    client.client = SimpleNamespace(request=lambda *_args, **_kwargs: responses.pop(0))
    monkeypatch.setattr("api.services.opnsense_images.time.sleep", lambda _seconds: None)

    assert client.request("GET", "/instances/test") == {"instance": {"id": "ok"}}
    assert responses == []


def test_cleanup_failure_preserves_ready_validated_snapshot(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    urls = artifact_urls("26.7")
    image = OpnsenseImage(
        version="26.7", artifact_url=urls["artifact"], checksum_url=urls["checksum"],
        signature_url=urls["signature"], status="ready", phase="ready", snapshot_id="snapshot-id",
        test_instance_id="test-id", builder_instance_id="builder-id", validated_at=utcnow(),
    )
    session.add(image); session.commit()

    class Client:
        def delete(self, kind, identifier):
            if identifier == "test-id":
                raise ImageWorkflowError("temporary Vultr error")

    monkeypatch.setattr("api.services.opnsense_images.cleanup_local", lambda _image: None)
    cleanup_validated_image(session, image, Client())

    assert image.status == "ready"
    assert image.snapshot_id == "snapshot-id"
    assert image.test_instance_id == "test-id"
    assert image.builder_instance_id is None
    assert "cleanup incomplete" in image.error_detail


def test_snapshot_is_blocked_until_builder_is_stopped_and_two_clones_pass(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    urls = artifact_urls("26.7")
    image = OpnsenseImage(
        version="26.7", artifact_url=urls["artifact"], checksum_url=urls["checksum"],
        signature_url=urls["signature"], status="awaiting_install", phase="awaiting_install",
        builder_instance_id="builder", vultr_iso_id="iso", builder_firewall_group_id="firewall",
        builder_config_token="one-time-token",
    )
    session.add(image); session.commit()
    events = []

    class Client:
        def close(self): pass
        def wait_instance(self, identifier):
            return {"main_ip": "198.51.100.10" if identifier == "builder" else "198.51.100.20"}
        def detach_iso(self, _image): events.append("iso-detached")
        def reboot(self, _identifier): events.append("reboot")
        def halt(self, _identifier): events.append("halt")
        def wait_stopped(self, _identifier): events.append("stopped"); return {"server_status": "stopped"}
        def create_snapshot(self, _image):
            assert events[-1] == "stopped"
            events.append("snapshot")
            return "snapshot-id"
        def wait_snapshot(self, _identifier): events.append("snapshot-complete")
        def create_test_instance(self, _image):
            events.append("clone-created")
            return f"clone-{events.count('clone-created')}"
        def delete(self, kind, identifier): events.append(f"deleted:{kind}:{identifier}")

    monkeypatch.setenv("CTF_CONTROL_PLANE_CIDR", "192.0.2.8/32")
    validation_media = []
    from api.services.opnsense_images import _builder_validation_command as real_validation_command
    def record_validation_command(**kwargs):
        validation_media.append(kwargs["installed"])
        return real_validation_command(**kwargs)
    monkeypatch.setattr("api.services.opnsense_images._builder_validation_command", record_validation_command)
    monkeypatch.setattr("api.services.opnsense_images._builder_ssh", lambda *_args, **_kwargs: (0, "26.7", ""))
    monkeypatch.setattr("api.services.opnsense_images._boot_id", lambda *_args: "boot")
    monkeypatch.setattr("api.services.opnsense_images._wait_for_new_boot", lambda *_args: None)
    fingerprints = iter(["host-key-1", "host-key-2"])
    monkeypatch.setattr("api.services.opnsense_images._ssh_host_fingerprint", lambda *_args: next(fingerprints))
    monkeypatch.setattr("api.services.opnsense_images.cleanup_local", lambda *_args, **_kwargs: None)

    complete_install(session, image.id, vultr_factory=Client)

    assert image.status == "ready"
    assert image.snapshot_id == "snapshot-id"
    assert validation_media and all(validation_media)
    assert events[:2] == ["iso-detached", "reboot"]
    assert events.index("halt") < events.index("stopped") < events.index("snapshot")
    assert events.count("clone-created") == 2
