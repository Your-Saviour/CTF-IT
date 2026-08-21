import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine, StaticPool
from sqlalchemy.orm import sessionmaker

from builder.ansible import _stage_files
from builder.module_loader import CopyStep, Module
from api.database import Base
from api.models import Event, PlatformSettings, Team, User, VM, VMModule
from api.routes.vm import _wait_for_tcp_port, provision_vm
from api.services.caldera import CalderaClient
from api.services.semaphore import SemaphoreClient


def _database():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)(), engine


def test_wait_for_tcp_port_returns_when_connection_succeeds():
    with patch("api.routes.vm.socket.create_connection") as connect:
        _wait_for_tcp_port("192.0.2.20", 22, timeout_seconds=1)
    connect.assert_called_once_with(("192.0.2.20", 22), timeout=5)


def test_opnsense_bootstrap_allows_installation_and_waits_for_reboot():
    playbook = Path("playbooks/bootstrap-opnsense.yml").read_text()
    assert "nohup /tmp/opnsense-bootstrap.sh" in playbook
    assert "/var/log/opnsense-bootstrap.log" in playbook
    assert "test -x /usr/local/sbin/configctl" in playbook
    assert "gather_facts: false" in playbook
    assert "seconds: 900" in playbook
    assert "ansible.builtin.raw: \"configctl service reload all\"" in playbook
    assert "ansible.builtin.setup" not in playbook


def test_stage_files_creates_parents_for_nested_copy_source(tmp_path):
    source_dir = tmp_path / "module"
    nested_source = source_dir / "src" / "package.json"
    nested_source.parent.mkdir(parents=True)
    nested_source.write_text('{"name":"portal"}')
    module = Module(
        id="portal",
        name="Portal",
        description="test module",
        type="application_external",
        difficulty="easy",
        points=1,
        category="test",
        steps=[CopyStep(src="src/package.json", dest="/opt/portal/package.json")],
        source_dir=source_dir,
    )

    _stage_files([module], tmp_path / "export")

    assert (tmp_path / "export" / "files" / "portal__src" / "package.json").read_text() == '{"name":"portal"}'


def test_base_playbook_waits_for_ubuntu_package_manager_lock():
    template = Path("templates/base_playbook.yml.j2").read_text()
    assert "lock_timeout: 600" in template


def test_vm_routes_expose_provider_neutral_aws_lifecycle_contract():
    source = Path("api/routes/vm.py").read_text()
    for route in (
        '"/aws/instance-types"', '"/aws/amis"', '"/vms/create-cloud"',
        '"/vms/{vm_id}/retry-cloud"', '"/vms/{vm_id}/destroy-cloud"',
    ):
        assert route in source
    assert '"cloud_instance_id": vm.cloud_instance_id' in source


def test_guestbook_vhost_uses_consistent_apache_options_syntax():
    vhost = Path("modules/application_external/php_guestbook/guestbook.conf").read_text()
    assert "Options -Indexes +FollowSymLinks" in vhost


def test_nextjs_portal_configures_the_import_alias_base_directory():
    tsconfig = Path("modules/application_external/nextjs_portal/src/tsconfig.json").read_text()
    assert '"baseUrl": "."' in tsconfig
    assert "@/" not in "\n".join(
        path.read_text()
        for path in Path("modules/application_external/nextjs_portal/src/app").rglob("*.ts*")
    )
    auth_route = Path(
        "modules/application_external/nextjs_portal/src/app/api/auth/[...nextauth]/route.ts"
    ).read_text()
    assert "../../../../lib/auth" in auth_route


def test_automatic_event_provisioning_creates_goal_rows_and_preserves_stages():
    vm_route = Path("api/routes/vm.py").read_text()
    assert "stage=mod.stage" in vm_route
    assert "if mod.type == \"goal\":" in vm_route
    assert "red_points=mod.red_points" in vm_route


def test_caldera_operation_detail_merges_metadata_from_collection_response():
    class Response:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    class Client:
        async def get(self, url):
            if url.startswith("/api/v2/operations/op-1"):
                return Response({"chain": [{"id": "link-1"}]})
            return Response([{"id": "op-1", "name": "Test", "state": "running"}])

    async def check():
        caldera = CalderaClient(api_key="test")
        original_client = caldera._client
        caldera._client = Client()
        await original_client.aclose()
        return await caldera.get_operation("op-1", include_chain=True)

    operation = asyncio.run(check())
    assert operation == {
        "id": "op-1",
        "name": "Test",
        "state": "running",
        "chain": [{"id": "link-1"}],
    }


def test_caldera_operations_auto_close_when_the_campaign_finishes():
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"id": "op-1", "state": "running"}

    class Client:
        def __init__(self):
            self.payload = None

        async def post(self, _url, json):
            self.payload = json
            return Response()

    async def check():
        caldera = CalderaClient(api_key="test")
        original_client = caldera._client
        fake_client = Client()
        caldera._client = fake_client
        await original_client.aclose()
        await caldera.create_operation("Campaign", "adversary", "planner", "event-1")
        return fake_client.payload

    payload = asyncio.run(check())
    assert payload["auto_close"] is True


def _seed_vm(db, status="registered", with_module=True):
    event = Event(name="Provisioning", quota="{}", status="open")
    db.add(event)
    db.flush()
    team = Team(name="Blue", event_id=event.id)
    db.add(team)
    db.flush()
    vm = VM(
        hostname="target-1",
        ip_address="192.0.2.20",
        status=status,
        team_id=team.id,
        event_id=event.id,
    )
    db.add(vm)
    db.flush()
    if with_module:
        db.add(VMModule(
            vm_id=vm.id,
            module_id="suid_find",
            module_type="vulnerability",
            difficulty="easy",
            points=100,
        ))
    db.commit()
    return vm, team


def test_provision_endpoint_records_initial_state_and_dispatches():
    db, engine = _database()
    vm, _ = _seed_vm(db)
    admin = User(username="admin", password_hash="x", is_admin=True)

    def close_coroutine(coroutine):
        coroutine.close()
        return MagicMock()

    try:
        with patch("api.routes.vm.require_admin", return_value=admin), patch(
            "api.routes.vm.asyncio.create_task", side_effect=close_coroutine
        ) as create_task:
            response = asyncio.run(provision_vm(vm.id, MagicMock(), db))
        assert response == {"status": "provisioning", "vm_id": vm.id}
        db.refresh(vm)
        assert vm.status == "provisioning"
        assert vm.provision_step == "generating_playbook"
        assert vm.provision_error is None
        create_task.assert_called_once()
    finally:
        db.close()
        Base.metadata.drop_all(engine)


def test_provision_endpoint_rejects_duplicate_run():
    db, engine = _database()
    vm, _ = _seed_vm(db, status="provisioning")
    try:
        with patch("api.routes.vm.require_admin", return_value=MagicMock()):
            response = asyncio.run(provision_vm(vm.id, MagicMock(), db))
        assert response.status_code == 409
    finally:
        db.close()
        Base.metadata.drop_all(engine)


def test_semaphore_inventory_uses_host_key_enrollment():
    client = SemaphoreClient.__new__(SemaphoreClient)
    response = MagicMock(status_code=201)
    response.json.return_value = {"id": 42}
    client._client = MagicMock()
    client._client.post.return_value = response

    inventory_id = client.create_inventory(1, "target", "192.0.2.20", "root", 22, 9)

    assert inventory_id == 42
    payload = client._client.post.call_args.kwargs["json"]
    assert "StrictHostKeyChecking=accept-new" in payload["inventory"]
    assert "StrictHostKeyChecking=no" not in payload["inventory"]
