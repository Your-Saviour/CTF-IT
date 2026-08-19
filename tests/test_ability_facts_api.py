import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.database import Base, get_db
from api.models import Event, EventOperation, User
from api.routes.admin import router


def _plan_with_ability():
    return {
        "nodes": [
            {"id": "trigger", "type": "manual_trigger", "label": "Manual", "x": 0, "y": 0, "config": {}},
            {"id": "a1", "type": "ability", "label": "Exploit", "x": 100, "y": 0,
             "config": {"module_id": "weak_ssh", "ability": "exploit", "target_vm_id": "vm:web"}},
            {"id": "finish", "type": "finish", "label": "Finish", "x": 200, "y": 0, "config": {}},
        ],
        "edges": [],
    }


def _fake_module(module_id="weak_ssh"):
    return SimpleNamespace(
        id=module_id, name="Weak SSH", type="vulnerability",
        caldera={"recon": {"command": "echo VULNERABLE"}, "exploit": {"command": "su svc"}},
        stage=None, references=[], tags=[], requires=[],
        prerequisites=[], conflicts=[], verification=None,
    )


@pytest.fixture
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    app = FastAPI()
    app.include_router(router)

    def override_db():
        s = sessions()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as c:
        c.sessions = sessions
        yield c


def test_ability_facts_reads_module_and_phase_from_node_config(client):
    db = client.sessions()
    event = Event(name="Exercise", quota="{}", status="open")
    db.add(event); db.commit(); db.refresh(event)
    op = EventOperation(event_id=event.id, name="Phase 1", position=0,
                        operation_plan=json.dumps(_plan_with_ability()))
    db.add(op); db.commit(); db.refresh(op)
    db.close()

    with patch("api.routes.admin.require_admin", return_value=User(is_admin=True)), \
         patch("api.routes.admin.load_all_modules", return_value=[_fake_module()]):
        resp = client.get(f"/admin/api/events/{event.id}/operations/{op.id}/ability-facts")

    assert resp.status_code == 200
    facts = resp.json()["fact_data"]
    assert len(facts) == 1
    assert facts[0]["node_id"] == "a1"
    assert facts[0]["module_id"] == "weak_ssh"
    assert facts[0]["module_name"] == "Weak SSH"
    assert facts[0]["phase"] == "exploit"
    assert facts[0]["inputs"] == ["ctf.vuln.weak_ssh"]
    assert facts[0]["outputs"] == []
