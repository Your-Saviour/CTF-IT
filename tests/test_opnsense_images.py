import hashlib
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.database import Base
from api.models import OpnsenseImage, PlatformSettings, utcnow
from api.services.opnsense_images import (
    BOOTSTRAP_SOURCE_URL, ImageWorkflowError, VultrImageClient, active_image,
    builder_validation_command, cleanup_validated_image, download_bootstrap,
    interrupt_running_jobs, new_image, render_golden_config, run_image_build,
    validate_bootstrap_url, validate_release, _posix_command,
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
    row = OpnsenseImage(**values)
    db.add(row); db.commit()
    return row


def test_supported_release_mapping_is_exact():
    assert validate_release("26.7") == "26.7"
    for value in ("25.7", "26.1", "26.7/../../", ""):
        with pytest.raises(ValueError): validate_release(value)


@pytest.mark.parametrize("value", ["", "not-a-cidr", "10.0.0.1", "2001:db8::/64", "10.0.0.0/99"])
def test_control_plane_cidr_is_rejected_before_client_creation(monkeypatch, value):
    db = session(); created = []
    monkeypatch.setenv("CTF_CONTROL_PLANE_CIDR", value)
    with pytest.raises(ImageWorkflowError):
        new_image(db, "26.7", vultr_factory=lambda: created.append(True))
    assert created == [] and db.query(OpnsenseImage).count() == 0


def test_bootstrap_download_is_official_and_records_digest():
    content = b"#!/bin/sh\necho opnsense\n"
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=content))
    with httpx.Client(transport=transport) as client:
        result, digest = download_bootstrap(client=client)
    assert result == content and digest == hashlib.sha256(content).hexdigest()
    for url in ("http://raw.githubusercontent.com/opnsense/update/master/src/bootstrap/opnsense-bootstrap.sh.in",
                "https://example.com/bootstrap.sh", BOOTSTRAP_SOURCE_URL + "?ref=x"):
        with pytest.raises(ImageWorkflowError): validate_bootstrap_url(url)


def test_new_image_preflights_vultr_before_persisting(monkeypatch):
    db = session(); monkeypatch.setenv("CTF_CONTROL_PLANE_CIDR", "192.0.2.8/32"); events = []
    class Client:
        def preflight(self, name): events.append(("preflight", name)); return 1869
        def close(self): events.append(("close",))
    image = new_image(db, "26.7", vultr_factory=Client)
    assert events == [("preflight", "FreeBSD 15 x64"), ("close",)]
    assert image.build_method == "freebsd-bootstrap"
    assert image.artifact_url is None and image.vultr_iso_id is None and image.builder_vpc_id is None


def test_golden_config_is_wan_only_key_only_and_has_provenance(monkeypatch):
    db = session(); image = image_row(db)
    monkeypatch.setattr("api.services.opnsense_images.get_or_create_platform_keypair",
                        lambda _db: ("private", "ssh-ed25519 TEST"))
    config = render_golden_config(db, image, "192.0.2.8/32")
    assert "<hostname>opnsense-golden</hostname>" in config
    assert config.count("<wan>") == 1 and "<lan>" not in config
    assert "<ipaddr>dhcp</ipaddr>" in config and "<ipaddrv6>none</ipaddrv6>" in config
    assert "<interfaces>wan</interfaces>" in config and "passwordauth" not in config.lower()
    assert "192.0.2.8/32" in config and "ctf_builder_provenance" in config
    for forbidden in ("wireguard", "unbound", "site", "event"): assert forbidden not in config.lower()


def test_builder_request_uses_freebsd_without_iso_or_vpc():
    client = object.__new__(VultrImageClient); captured = {}
    client.request = lambda method, path, **kwargs: captured.update(method=method, path=path, body=kwargs["json"]) or {"instance": {"id": "builder"}}
    image = type("Image", (), {"id": 4, "version": "26.7", "builder_firewall_group_id": "fw"})()
    assert client.create_builder(image, os_id=1869, ssh_key_id="key") == "builder"
    assert captured["body"]["os_id"] == 1869 and captured["body"]["plan"] == "vc2-2c-4gb"
    assert "iso_id" not in captured["body"] and "attach_vpc" not in captured["body"]


def test_validation_command_checks_release_disk_network_ssh_and_pf():
    command = builder_validation_command(public_ip="198.51.100.9", version="26.7",
                                         cidr="192.0.2.8/32", provenance="marker")
    for expected in ("opnsense-version", "test -w /conf/config.xml", "vtnet", "198.51.100.9",
                     "route -n get default", "passwordauthentication no",
                     "kbdinteractiveauthentication no", "pfctl -sr", "192.0.2.8"):
        assert expected in command


def test_remote_commands_explicitly_select_posix_shell_for_opnsense_root():
    wrapped = _posix_command("set -eu; value=$(echo ok); test \"$value\" = ok")
    assert wrapped.startswith("/bin/sh -c ")
    assert "set -eu" in wrapped and "$(echo ok)" in wrapped


def test_running_jobs_interrupt_and_only_validated_active_image_is_selected():
    db = session(); image = image_row(db, status="bootstrapping", phase="bootstrapping")
    assert interrupt_running_jobs(db) == 1 and image.status == "interrupted"
    image.status = image.phase = "active"; image.snapshot_id = "snap"; image.validated_at = utcnow()
    db.add(PlatformSettings(key="active_opnsense_image_id", value=str(image.id))); db.commit()
    assert active_image(db).id == image.id


class WorkflowClient:
    def __init__(self): self.events = []
    def close(self): self.events.append("close")
    def preflight(self, _base): self.events.append("preflight"); return 1869
    def ensure_ssh_key(self, _key): return "ssh-key"
    def create_firewall(self, _version, _cidr): self.events.append("firewall"); return "firewall"
    def create_builder(self, _image, **_kwargs): self.events.append("builder"); return "builder"
    def instance(self, identifier):
        self.events.append(f"instance:{identifier}")
        return {"power_status": "stopped", "main_ip": "198.51.100.10"}
    def wait_instance(self, identifier):
        self.events.append(f"wait:{identifier}")
        return {"main_ip": {"builder": "198.51.100.10", "clone-1": "198.51.100.11", "clone-2": "198.51.100.12"}[identifier]}
    def wait_stopped(self, identifier): self.events.append(f"stopped:{identifier}"); return {"power_status": "stopped"}
    def start(self, identifier): self.events.append(f"start:{identifier}")
    def create_snapshot(self, _image):
        assert self.events.count("stopped:builder") == 2 or "instance:builder" in self.events
        self.events.append("snapshot"); return "snapshot"
    def wait_snapshot(self, _identifier): self.events.append("snapshot-complete")
    def create_clone(self, _image, number): self.events.append(f"clone:{number}"); return f"clone-{number}"
    def create_validation_vpc(self, _image): self.events.append("vpc"); return "vpc"
    def attach_vpc(self, instance, vpc):
        self.events.append(f"attach:{instance}:{vpc}")
        return {"mac_address": "00:11:22:33:44:55", "ip_address": "172.31.254.2"}
    def delete(self, kind, identifier): self.events.append(f"delete:{kind}:{identifier}")


def patch_success(monkeypatch, *, state="freebsd"):
    monkeypatch.setenv("CTF_CONTROL_PLANE_CIDR", "192.0.2.8/32")
    monkeypatch.setattr("api.services.opnsense_images.get_or_create_platform_keypair", lambda _db: ("private", "ssh-ed25519 TEST"))
    monkeypatch.setattr("api.services.opnsense_images._guest_state", lambda *_args: state)
    monkeypatch.setattr("api.services.opnsense_images._verify_freebsd_base", lambda *_args: None)
    monkeypatch.setattr("api.services.opnsense_images._upload_atomic", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("api.services.opnsense_images._wait_for_opnsense", lambda *_args: None)
    monkeypatch.setattr("api.services.opnsense_images._validate_builder", lambda *_args: None)
    boots = iter(["boot-1", "boot-2"])
    monkeypatch.setattr("api.services.opnsense_images._boot_id", lambda *_args: next(boots))
    monkeypatch.setattr("api.services.opnsense_images._fingerprint", lambda *_args: "builder-key")
    monkeypatch.setattr("api.services.opnsense_images._halt", lambda *_args: None)
    monkeypatch.setattr("api.services.opnsense_images._ssh", lambda *_args, **_kwargs: (0, "", ""))
    monkeypatch.setattr("api.services.opnsense_images._validate_clone_one", lambda *_args: "clone-key-1")
    monkeypatch.setattr("api.services.opnsense_images._validate_clone_two", lambda *_args: "clone-key-2")
    monkeypatch.setattr("api.services.opnsense_images._configure_validation_peer", lambda *_args: "172.31.254.2")


def test_full_automated_lifecycle_blocks_snapshot_until_two_boots_and_cleans_up(monkeypatch):
    db = session(); image = image_row(db); client = WorkflowClient(); patch_success(monkeypatch)
    run_image_build(db, image.id, vultr_factory=lambda: client,
                    bootstrap_downloader=lambda _url: (b"script", hashlib.sha256(b"script").hexdigest()))
    assert image.status == image.phase == "ready" and image.snapshot_id == "snapshot"
    assert image.bootstrap_sha256 == hashlib.sha256(b"script").hexdigest()
    assert client.events.index("snapshot") > client.events.index("stopped:builder")
    assert "clone:1" in client.events and "clone:2" in client.events and "attach:clone-2:vpc" in client.events
    assert image.builder_instance_id is None and image.test_instance_id is None


def test_resume_during_conversion_never_downloads_or_launches_second_bootstrap(monkeypatch):
    db = session(); image = image_row(db, status="interrupted", phase="bootstrapping",
                                    builder_instance_id="builder", builder_firewall_group_id="firewall",
                                    bootstrap_sha256="existing")
    client = WorkflowClient(); patch_success(monkeypatch, state="converting"); downloads = []
    run_image_build(db, image.id, vultr_factory=lambda: client,
                    bootstrap_downloader=lambda _url: downloads.append(True))
    assert downloads == [] and image.status == "ready"


def test_resume_from_stopped_snapshot_phase_skips_builder_and_conversion(monkeypatch):
    db = session()
    results = '{"builder_boot_2":{"passed":true,"ssh_host_key":"builder-key"}}'
    image = image_row(db, status="interrupted", phase="snapshotting",
                      builder_instance_id="builder", builder_firewall_group_id="firewall",
                      validation_results=results, bootstrap_sha256="existing")
    client = WorkflowClient(); patch_success(monkeypatch); downloads = []
    run_image_build(db, image.id, vultr_factory=lambda: client,
                    bootstrap_downloader=lambda _url: downloads.append(True))
    assert downloads == [] and "wait:builder" not in client.events
    assert image.status == "ready" and image.snapshot_id == "snapshot", image.error_detail


def test_validation_failure_never_calls_snapshot(monkeypatch):
    db = session(); image = image_row(db); client = WorkflowClient(); patch_success(monkeypatch)
    monkeypatch.setattr("api.services.opnsense_images._validate_builder",
                        lambda *_args: (_ for _ in ()).throw(ImageWorkflowError("disk gate failed")))
    run_image_build(db, image.id, vultr_factory=lambda: client,
                    bootstrap_downloader=lambda _url: (b"script", "a" * 64))
    assert image.status == "failed" and "disk gate failed" in image.error_detail
    assert "snapshot" not in client.events and image.snapshot_id is None


def test_cleanup_failure_cannot_invalidate_ready_snapshot():
    db = session(); image = image_row(db, status="ready", phase="ready", snapshot_id="snap",
                                    builder_instance_id="builder", validated_at=utcnow())
    class Client:
        def delete(self, _kind, _identifier): raise ImageWorkflowError("temporary cleanup failure")
    cleanup_validated_image(db, image, Client())
    assert image.status == "ready" and image.snapshot_id == "snap"
    assert "cleanup incomplete" in image.error_detail


def test_new_workflow_has_no_manual_or_iso_surfaces():
    service = Path("api/services/opnsense_images.py").read_text()
    routes = Path("api/routes/admin.py").read_text()
    main = Path("api/main.py").read_text()
    assert "import_iso" not in service and "detach_iso" not in service and '"iso_id"' not in service
    assert "installer-complete" not in routes and "opnsense-builder-config" not in main
