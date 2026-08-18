from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.database import Base, get_db
from api.main import app
from api.models import Event, EventOperation, User


ROOT = Path(__file__).resolve().parents[1]


def test_overview_and_item_designer_routes_are_registered():
    paths = {route.path for route in app.routes if hasattr(route, "path")}
    assert "/admin/events/{event_id}/operation" in paths
    assert "/admin/events/{event_id}/operations/{operation_id}" in paths
    assert "/admin/events/{event_id}/operations/{operation_id}/runs/{run_id}" in paths


def test_operations_overview_exposes_management_controls():
    html = (ROOT / "frontend/templates/event_operations.html").read_text()
    assert 'id="operations-list"' in html
    assert 'id="operation-create"' in html
    assert 'id="operation-editor-dialog"' in html
    assert 'id="operation-delete-dialog"' in html
    assert 'data-read-only="{{ read_only|tojson }}"' in html
    assert "/static/event-operations.js" in html


def test_designer_is_scoped_to_one_operation_and_returns_to_overview():
    html = (ROOT / "frontend/templates/event_operation.html").read_text()
    source = (ROOT / "frontend/static/event-operation.js").read_text()
    assert 'data-operation-id="{{ operation_id }}"' in html
    assert 'href="/admin/events/{{ event_id }}/operation"' in html
    assert "operations/${operationId}/plan" in source


def test_run_detail_template_contract():
    html = (ROOT / "frontend/templates/operation_run.html").read_text()
    assert 'data-run-id="{{ run_id }}"' in html
    assert "data-event-id" in html
    assert "operation-run-app" in html
    assert "/admin/api/operation-runs/${runId}" in html
    assert "/admin/api/operation-runs/${runId}/steps/${stepId}/${action}" in html
    assert "/admin/api/operation-runs/${runId}/cancel" in html
    assert "setInterval(loadRun,4000)" in html
    assert 'id="run-cancel"' in html
    assert "esc(" in html
    assert "run-output" in html
    assert "run-status-badge" in html


def test_run_detail_page_auth_redirects_and_renders():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Sessions = sessionmaker(bind=engine, expire_on_commit=False)

    def override_db():
        session = Sessions()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    try:
        db = Sessions()
        event = Event(name="Exercise", quota="{}", status="open")
        db.add(event)
        db.commit()
        db.refresh(event)
        operation = EventOperation(event_id=event.id, name="Phase 1", position=0, operation_plan="{}")
        db.add(operation)
        db.commit()
        db.refresh(operation)
        admin = User(username="run-admin", password_hash="x", is_admin=True, event_id=event.id)
        db.add(admin)
        db.commit()
        db.refresh(admin)
        db.close()

        with TestClient(app, follow_redirects=False) as client:
            unauth = client.get(f"/admin/events/{event.id}/operations/{operation.id}/runs/7")
            assert unauth.status_code == 303
            assert unauth.headers["location"] == "/"

        with patch("api.main.get_current_user", return_value=admin):
            with TestClient(app) as client:
                resp = client.get(f"/admin/events/{event.id}/operations/{operation.id}/runs/7")
                assert resp.status_code == 200
                assert "Operation run" in resp.text
                assert 'data-run-id="7"' in resp.text
                assert f"/admin/events/{event.id}/operations/{operation.id}" in resp.text
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(engine)
