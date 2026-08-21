import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.database import Base
from api.models import Event, EventOperation, Scenario
from builder.module_loader import Module
from builder.operation_plan import empty_operation_plan
from builder.scenario import (
    capture_scenario_from_event,
    instantiate_scenario,
    scenario_fingerprint,
    validate_scenario_catalogue,
)


@pytest.fixture
def db_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    yield sessions
    sessions().close()


def _module(module_id, bases=("ubuntu_24_server",)):
    return Module(id=module_id, name=module_id, description="", type="vulnerability",
                  difficulty="easy", points=0, category="test", supported_bases=list(bases))


INFRA = {
    "vpn_gateway": {"base_type": "ubuntu_24_server", "default_plan": "vc2-1c-1gb",
                    "region": "ewr", "listen_port": 51820},
    "sites": [{
        "key": "head_office", "name": "Head Office", "region": "ewr", "firewall_team": "blue",
        "firewall": {"base_type": "opnsense", "default_plan": "vc2-2c-4gb"},
        "zones": [
            {"key": "corporate", "name": "Corporate", "team": "blue",
             "endpoints": [{"key": "workstation_1", "name": "Workstation 1",
                            "base_type": "ubuntu_24_server", "default_plan": "vc2-1c-1gb"}]},
        ],
    }],
}

MODULE_PLAN = {"version": 1, "assignments": {
    "vm:head_office/corporate/workstation_1": {
        "mode": "manual_only",
        "pinned_module_ids": ["weak_ssh_credentials"],
        "resolved_module_ids": ["weak_ssh_credentials"],
    }
}}


def _scenario(db, module_plan=None, operations=None):
    scenario = Scenario(
        name="Base", version=1, quota="{}",
        infrastructure=json.dumps(INFRA),
        module_plan=json.dumps(module_plan if module_plan is not None else MODULE_PLAN),
        operations_json=json.dumps(operations if operations is not None else []),
        timeline=json.dumps({"version": 1, "phases": [], "injects": []}),
    )
    db.add(scenario); db.commit(); db.refresh(scenario)
    return scenario


def test_fingerprint_changes_with_content():
    a = scenario_fingerprint("{}", INFRA, None, MODULE_PLAN, [], {"version": 1, "phases": [], "injects": []})
    b = scenario_fingerprint("{}", INFRA, None, MODULE_PLAN, [{"name": "Op"}], {"version": 1, "phases": [], "injects": []})
    assert a != b
    assert a.startswith("sha256:")


def test_instantiate_creates_event_and_operations(db_session):
    db = db_session()
    ops = [{"name": "Recon", "description": None, "position": 0,
            "operation_plan": empty_operation_plan()}]
    scenario = _scenario(db, operations=ops)
    event_id, report = instantiate_scenario(db, scenario, name="Instantiated")
    event = db.get(Event, event_id)
    assert event.name == "Instantiated"
    assert event.status == "draft"
    assert event.scenario_id == scenario.id
    assert event.scenario_version == scenario.version
    rows = db.query(EventOperation).filter(EventOperation.event_id == event_id).all()
    assert [r.name for r in rows] == ["Recon"]
    assert json.loads(event.module_plan) == MODULE_PLAN
    assert report == []


def test_instantiate_reports_unknown_module(db_session):
    db = db_session()
    plan = {"version": 1, "assignments": {
        "vm:head_office/corporate/workstation_1": {
            "mode": "manual_only", "pinned_module_ids": ["gone"],
            "resolved_module_ids": ["gone"],
        }
    }}
    scenario = _scenario(db, module_plan=plan)
    _, report = instantiate_scenario(db, scenario)
    assert any(i["code"] == "unknown_module" for i in report)


def test_validate_scenario_catalogue_flags_incompatible_base():
    issues = validate_scenario_catalogue(MODULE_PLAN, INFRA, {
        "weak_ssh_credentials": _module("weak_ssh_credentials", bases=("windows",))
    })
    assert any(i["code"] == "incompatible_base" for i in issues)
