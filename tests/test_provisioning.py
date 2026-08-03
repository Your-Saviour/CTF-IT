import asyncio
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine, StaticPool
from sqlalchemy.orm import sessionmaker

from api.database import Base
from api.models import Event, Team, User, VM, VMModule
from api.routes.vm import _maybe_cleanup_team_vpc, provision_vm
from api.services.semaphore import SemaphoreClient


def _database():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)(), engine


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


def test_vpc_cleanup_waits_for_last_attached_vm():
    db, engine = _database()
    vm, team = _seed_vm(db)
    team.vpc_id = "vpc-123"
    vm.vpc_ip = "10.1.1.10"
    db.commit()
    try:
        with patch("api.routes.vm.VULTR_API_KEY", "test-key"), patch(
            "httpx.delete"
        ) as delete:
            _maybe_cleanup_team_vpc(db, team.id)
            delete.assert_not_called()

            db.delete(vm)
            db.commit()
            response = MagicMock(status_code=204)
            delete.return_value = response
            _maybe_cleanup_team_vpc(db, team.id)
            delete.assert_called_once()
            db.refresh(team)
            assert team.vpc_id is None
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
