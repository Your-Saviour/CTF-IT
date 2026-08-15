from pathlib import Path


TEMPLATE = Path(__file__).resolve().parents[1] / "frontend" / "templates" / "event_plan.html"
ROOT = TEMPLATE.parents[2]
EVENT_EDITOR = ROOT / "frontend" / "templates" / "admin_resource.html"
CANVAS = ROOT / "frontend" / "static" / "event-planner-canvas.js"
STATE = ROOT / "frontend" / "static" / "event-planner-state.js"
CONTROLLER = ROOT / "frontend" / "static" / "event-planner.js"
CSS = ROOT / "frontend" / "static" / "event-planner.css"


def test_plan_page_loads_full_page_planner_assets():
    source = TEMPLATE.read_text()

    assert 'id="planner-outline"' in source
    assert 'id="planner-canvas"' in source
    assert 'id="planner-inspector"' in source
    assert 'id="planner-validation"' in source
    assert 'src="/static/event-planner.js' in source
    assert 'href="/static/event-planner.css' in source
    assert "onclick=" not in source


def test_plan_page_owns_a_dedicated_shell():
    source = TEMPLATE.read_text()

    assert '{% extends "base.html" %}' in source
    assert "admin_base.html" not in source
    assert 'class="planner-account"' in source
    assert 'action="/auth/logout"' in source
    assert "{{ user.username }}" in source


def test_plan_page_css_fills_the_viewport():
    source = CSS.read_text()
    compact = "".join(source.split())

    assert ".planner-page" in source
    assert "height:100dvh" in compact
    assert ".planner-root" in source
    assert "min-height:0" in compact
    assert ".planner-account" in source


def test_plan_outline_resets_base_navigation_and_grid_stacks_before_clipping():
    compact = "".join(CSS.read_text().split())

    assert "#planner-outline{display:block;padding:0;border:0;background:transparent;position:static;}" in compact
    assert "@media(max-width:1100px)" in compact
    assert ".planner-workspace{grid-template-columns:1fr;}" in compact


def test_admin_drawers_restore_focus_and_red_team_tables_are_responsive():
    script = (ROOT / "frontend" / "static" / "admin.js").read_text()
    caldera = (ROOT / "frontend" / "templates" / "caldera_dashboard.html").read_text()
    agent = (ROOT / "frontend" / "templates" / "ai_agent.html").read_text()
    assert "drawer._returnFocus.focus()" in script
    assert "requestAnimationFrame" in script
    assert "form input:not([disabled])" in script
    assert caldera.count('class="table-wrap"') >= 4
    assert 'class="card-body table-wrap"' in agent


def test_event_drawer_delegates_network_authoring_to_full_page_plan():
    source = EVENT_EDITOR.read_text()

    assert 'id="infrastructure-json"' not in source
    assert 'href="/admin/events/${x.id}/plan"' in source


def test_canvas_module_supports_durable_accessible_layout():
    source = CANVAS.read_text()
    compact = "".join(source.split())

    assert "createPlannerCanvas" in source
    assert "onLayoutChange" in source
    assert "resetLayout" in source
    assert "focusNode" in source
    assert "d3.zoom" in source
    assert "aria-label" in source
    assert ".call(zoom.transform,transform)" in compact
    assert "if(!callbacks.readOnly)" in compact


def test_canvas_drag_updates_node_and_attached_links_before_release():
    source = CANVAS.read_text()
    compact = "".join(source.split())

    assert "sourceEvent.currentTarget" not in source
    assert ".on('drag',function(event,d)" in compact
    assert "d3.select(this).attr('transform'" in compact
    assert "updateLinks()" in source
    drag_handler = compact[compact.index(".on('drag'"):compact.index(".on('end'")]
    assert "updateLinks()" in drag_handler


def test_canvas_persists_missing_hierarchical_positions_and_reuses_them_for_reset():
    source = CANVAS.read_text()
    compact = "".join(source.split())

    assert "calculateHierarchicalLayout(graph,currentLayout)" in compact
    assert "completed.added&&!callbacks.readOnly" in compact
    assert "calculateHierarchicalLayout(graph,{version:1,nodes:{}})" in compact
    assert "functiondefaults(" not in compact


def test_planner_state_remaps_structural_layout_ids_and_mirrors_server_paths():
    source = STATE.read_text()

    assert "renameStructuralKey" in source
    assert "state.layout = {version: 1, nodes: remapped}" in source
    assert "export function pruneLayout" in source
    assert "sites[${si}].zones[${zi}].endpoints[${vi}]" in source
    assert "A zone supports at most 245 VMs" in source
    assert "Listen port must be from 1 to 65535" in source


def test_planner_recovers_catalogues_and_guards_read_only_mutations():
    source = CONTROLLER.read_text()

    assert "Promise.allSettled" in source
    assert "planner-retry-catalogues" in source
    assert "readOnly:READ_ONLY" in source
    assert "if(!READ_ONLY)canvas?.resetLayout()" in source
    assert "document.querySelector('.planner-add-actions').hidden=READ_ONLY" in source
    assert "return pruneLayout(state)" in source
    assert "if(name==='listen_port')value=Number(value)" in source


def test_planner_renders_system_firewall_zone_as_workload_route_parent():
    source = CONTROLLER.read_text()

    assert "normalizeClientLayout" in source
    assert "node.visualParent||node.parent" in source
    assert "node.type==='firewall-zone'" in source
    assert "System managed" in source
    assert "Automatically allocated" in source
    assert "['site','firewall-zone','firewall','zone','vm']" in source
    assert "['zone','vm']" in source


def test_planner_projects_configurable_base_type_icons_into_machine_nodes():
    controller = CONTROLLER.read_text()
    canvas = CANVAS.read_text()

    assert "PLANNER_ICON_GROUPS" in controller
    assert "<optgroup" in controller
    assert "machineIconPair" in controller
    assert "setMachineIconOverride" in controller
    assert "Primary icon" in controller
    assert "Secondary icon" in controller
    assert "node-primary-icon" in canvas
    assert "node-secondary-icon" in canvas
    assert "viewBox" in canvas


def test_machine_nodes_render_as_standalone_icons_with_labels_below():
    canvas = CANVAS.read_text()

    assert "node-hit-target" in canvas
    assert "node-state-ring" in canvas
    assert "machine-label" in canvas
    assert "topologyNodePresentation" in canvas
    assert "machine-badge" not in canvas
