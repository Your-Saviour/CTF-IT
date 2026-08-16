from pathlib import Path

from api.main import app


ROOT = Path(__file__).resolve().parents[1]


def test_overview_and_item_designer_routes_are_registered():
    paths = {route.path for route in app.routes if hasattr(route, "path")}
    assert "/admin/events/{event_id}/operation" in paths
    assert "/admin/events/{event_id}/operations/{operation_id}" in paths


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
