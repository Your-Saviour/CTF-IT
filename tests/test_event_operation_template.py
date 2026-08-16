from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_operation_designer_uses_dedicated_planner_shell_and_three_columns():
    html = (ROOT / "frontend/templates/event_operation.html").read_text()
    assert '{% extends "base.html" %}' in html
    assert "planner-toolbar" in html
    assert "operation-library" in html
    assert 'id="operation-canvas"' in html
    assert "operation-inspector" in html
    assert 'id="operation-outline"' in html
    assert 'id="operation-validation"' in html
    assert 'aria-live="polite"' in html


def test_operation_designer_exposes_real_workflow_actions_and_dialogs():
    html = (ROOT / "frontend/templates/event_operation.html").read_text()
    for identifier in ("operation-validate", "operation-arrange", "operation-preview", "operation-save"):
        assert f'id="{identifier}"' in html
    assert f'/admin/events/{{{{ event_id }}}}/modules' in html
    assert 'id="operation-preview-dialog"' in html
    assert 'id="edge-dialog"' in html


def test_operation_designer_assets_and_route_are_wired():
    html = (ROOT / "frontend/templates/event_operation.html").read_text()
    main = (ROOT / "api/main.py").read_text()
    assert "/static/event-operation.css" in html
    assert 'type="module" src="/static/event-operation.js' in html
    assert '@app.get("/admin/events/{event_id}/operation"' in main
    assert '"event_operation.html"' in main


def test_module_assignment_links_forward_to_operation_design():
    html = (ROOT / "frontend/templates/event_modules.html").read_text()
    assert f'/admin/events/{{{{ event_id }}}}/operation' in html
    assert "Design operation" in html


def test_operation_nodes_use_pointer_capture_for_dragging():
    source = (ROOT / "frontend/static/event-operation.js").read_text()
    assert "onpointerdown" in source
    assert "setPointerCapture" in source
    assert "onpointermove" in source
    assert "releasePointerCapture" in source
    assert "moveNode" in source
