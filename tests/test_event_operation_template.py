from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_operation_designer_uses_canvas_dominant_workspace():
    html = (ROOT / "frontend/templates/event_operation.html").read_text()
    assert '{% extends "base.html" %}' in html
    assert "planner-toolbar" in html
    assert 'id="operation-canvas"' in html
    assert 'id="operation-world"' in html
    assert 'id="operation-selection-box"' in html
    assert 'id="operation-add-node"' in html
    assert 'id="operation-undo"' in html
    assert 'id="operation-redo"' in html
    assert 'id="operation-zoom-in"' in html
    assert 'id="operation-zoom-out"' in html
    assert 'id="operation-fit"' in html
    assert 'id="operation-minimap"' in html
    assert 'id="node-picker"' in html
    assert 'id="node-picker-search"' in html
    assert 'id="node-picker-results"' in html
    assert 'id="operation-inspector-panel"' in html
    assert 'id="operation-outline-panel"' in html
    assert 'id="operation-validation-panel"' in html
    assert 'id="operation-announcer"' in html
    assert 'aria-live="polite"' in html
    assert 'class="operation-library"' not in html


def test_operation_designer_exposes_real_workflow_actions_and_dialogs():
    html = (ROOT / "frontend/templates/event_operation.html").read_text()
    for identifier in ("operation-validate", "operation-arrange", "operation-preview", "operation-save"):
        assert f'id="{identifier}"' in html
    assert f'/admin/events/{{{{ event_id }}}}/operation' in html
    assert 'id="operation-preview-dialog"' in html
    assert 'id="edge-dialog"' not in html


def test_operation_designer_exposes_run_action():
    html = (ROOT / "frontend/templates/event_operation.html").read_text()
    source = (ROOT / "frontend/static/event-operation.js").read_text()
    assert 'id="operation-run"' in html
    assert "btn btn-primary" in html
    assert "operations/${operationId}/run" in source
    assert "runs/${runId}" in source
    assert "confirm(" in source


def test_operation_designer_assets_and_route_are_wired():
    html = (ROOT / "frontend/templates/event_operation.html").read_text()
    main = (ROOT / "api/main.py").read_text()
    assert "/static/event-operation.css" in html
    assert 'type="module" src="/static/event-operation.js' in html
    assert '@app.get("/admin/events/{event_id}/operation"' in main
    assert '"event_operation.html"' in main


def test_operation_designer_cache_busts_controller_and_state_module_together():
    html = (ROOT / "frontend/templates/event_operation.html").read_text()
    source = (ROOT / "frontend/static/event-operation.js").read_text()
    assert '/static/event-operation.js?v=7' in html
    assert "from './event-operation-state.js?v=4'" in source


def test_ability_nodes_expose_shared_details_and_expandable_dialog():
    html = (ROOT / "frontend/templates/event_operation.html").read_text()
    source = (ROOT / "frontend/static/event-operation.js").read_text()
    styles = (ROOT / "frontend/static/event-operation-ability-details.css").read_text()

    assert 'id="ability-details-dialog"' in html
    assert 'id="ability-details-dialog-content"' in html
    assert 'data-ability-dialog-action="close"' in html
    assert '/static/event-operation-ability-details.css?v=2' in html
    assert '/static/event-operation.js?v=7' in html
    assert "from './event-operation-ability-details.js?v=2'" in source
    assert 'data-inspector-tab="details"' in source
    assert 'data-inspector-tab="settings"' in source
    assert 'data-ability-action="expand"' in source
    assert 'navigator.clipboard.writeText(command)' in source
    assert 'abilityExpandButton?.focus()' in source
    assert 'margin:auto' in styles


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


def test_operation_controller_wires_direct_canvas_interactions():
    source = (ROOT / "frontend/static/event-operation.js").read_text()
    for helper in (
        "connectionError", "insertConnectedNode", "moveNodes", "duplicateNodes",
        "createViewport", "zoomAt", "fitViewport", "createHistory",
    ):
        assert helper in source
    assert "onwheel" in source
    assert "clipboard" in source
    assert "node-picker-search" in source
    assert "operation-connection-preview" in source


def test_operation_picker_filters_abilities_by_zone_and_explicit_vm_target():
    html = (ROOT / "frontend/templates/event_operation.html").read_text()
    source = (ROOT / "frontend/static/event-operation.js").read_text()

    assert 'for="node-picker-zone"' in html
    assert 'id="node-picker-zone"' in html
    assert 'for="node-picker-vm"' in html
    assert 'id="node-picker-vm"' in html
    assert '/static/event-operation-picker-filter.css?v=1' in html
    assert "from './event-operation-picker-filter.js?v=1'" in source
    assert "selectedPickerZone=''" in source
    assert "selectedPickerVm=''" in source
    assert "target_vm_id:abilityTargetId(selectedPickerVm)" in source
    assert "catalogue.targets[0]?.id||''" not in source
    assert "filterAbilities" in source
    assert "abilityEmptyText" in source


def test_operation_designer_exposes_trigger_node_language():
    html = (ROOT / "frontend/templates/event_operation.html").read_text()
    source = (ROOT / "frontend/static/event-operation.js").read_text()
    assert "Search triggers, targets, abilities, objectives, and controls" in html
    assert "operationTriggerTemplates" in source
    assert "replaceTrigger" in source
    assert "Start after event begins (minutes)" in source
    assert "triggerPreviewText" in source
    assert "Launch mode" not in source
    assert "Start offset (min)" not in source
