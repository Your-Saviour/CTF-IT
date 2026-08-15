from pathlib import Path

from builder.infrastructure_planner import default_infrastructure, validate_infrastructure_layout
from builder.infrastructure_validation import validate_infrastructure


TEMPLATE = Path(__file__).resolve().parents[1] / "frontend" / "templates" / "event_plan.html"
ROOT = TEMPLATE.parents[2]
EVENT_EDITOR = ROOT / "frontend" / "templates" / "admin_resource.html"
CANVAS = ROOT / "frontend" / "static" / "event-planner-canvas.js"
STATE = ROOT / "frontend" / "static" / "event-planner-state.js"
CONTROLLER = ROOT / "frontend" / "static" / "event-planner.js"
CSS = ROOT / "frontend" / "static" / "event-planner.css"
BASES = {"ubuntu_24_server", "opnsense"}


def test_infrastructure_accepts_free_form_planner_address_annotations():
    infrastructure = default_infrastructure()
    site = infrastructure["sites"][0]
    site["firewall_zone_address_range"] = "x.x.{{team_id}}.0/24"
    site["firewall"]["address"] = "x.x.{{team_id}}.1"
    zone = site["zones"][0]
    zone["address_range"] = "x.x.{{team_id}}.0/24"
    zone["endpoints"][0]["address"] = "x.x.{{team_id}}.10"

    assert validate_infrastructure(infrastructure, BASES) == []


def test_infrastructure_rejects_non_string_planner_address_annotations():
    infrastructure = default_infrastructure()
    site = infrastructure["sites"][0]
    site["firewall_zone_address_range"] = ["10.0.0.0/24"]
    site["firewall"]["address"] = {"host": "10.0.0.1"}
    zone = site["zones"][0]
    zone["address_range"] = {"cidr": "10.0.0.0/24"}
    zone["endpoints"][0]["address"] = 10

    errors = validate_infrastructure(infrastructure, BASES)

    assert "sites[0].firewall_zone_address_range must be a string" in errors
    assert "sites[0].firewall.address must be a string" in errors
    assert "sites[0].zones[0].address_range must be a string" in errors
    assert "sites[0].zones[0].endpoints[0].address must be a string" in errors


def test_layout_theme_validation_accepts_known_nodes_and_strict_hex_colours():
    layout = {
        "version": 1,
        "nodes": {},
        "themes": {
            "zone:head_office/corporate": {"color": "#2563eb"},
            "vm:head_office/corporate/workstation_1": {"color": "#A855F7"},
        },
    }

    assert validate_infrastructure_layout(layout, default_infrastructure()) == []


def test_layout_theme_validation_rejects_unknown_malformed_and_extra_values():
    layout = {
        "version": 1,
        "nodes": {},
        "themes": {
            "zone:head_office/missing": {"color": "#2563eb"},
            "zone:head_office/corporate": {"color": "blue"},
            "vm:head_office/corporate/workstation_1": {"color": "#a855f7", "fill": "red"},
            "firewall-zone:head_office": "#dc2626",
        },
    }

    errors = validate_infrastructure_layout(layout, default_infrastructure())

    assert any("themes.zone:head_office/missing references an unknown node id" in error for error in errors)
    assert any("themes.zone:head_office/corporate.color must be a six-digit hex colour" in error for error in errors)
    assert any("themes.vm:head_office/corporate/workstation_1.fill is not supported" in error for error in errors)
    assert any("themes.firewall-zone:head_office must be an object" in error for error in errors)


def test_planner_colour_controls_feed_inherited_themes_to_the_canvas():
    controller = CONTROLLER.read_text()
    canvas = CANVAS.read_text()
    css = CSS.read_text()

    assert "renderColourControl" in controller
    assert "setNodeThemeColor" in controller
    assert "effectiveNodeColor" in controller
    assert "data-theme-swatch" in controller
    assert "data-theme-reset" in controller
    assert "colorInherited" in controller
    assert "--node-theme-color" in canvas
    assert "--node-theme-color" in css
    assert ".theme-swatch" in css


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
    assert "themes: remapEntries(state.layout?.themes)" in source
    assert "export function pruneLayout" in source
    assert "sites[${si}].zones[${zi}].endpoints[${vi}]" in source
    assert "A zone supports at most 245 VMs" in source
    assert "Listen port must be from 1 to 65535" in source


def test_planner_recovers_catalogues_and_guards_read_only_mutations():
    source = CONTROLLER.read_text()
    compact = "".join(source.split())

    assert "Promise.allSettled" in source
    assert "planner-retry-catalogues" in source
    assert "readOnly:READ_ONLY" in source
    assert "if(!READ_ONLY)canvas?.resetLayout()" in source
    assert "document.querySelector('.planner-add-actions').hidden=READ_ONLY" in source
    assert "return pruneLayout(state)" in source
    assert "if(name==='listen_port')value=Number(value)" in source
    assert "saveBlocked=catalogueFailures.includes('base types')" in source
    assert "$('#planner-save').disabled=READ_ONLY||saveBlocked||errors.length>0||!dirty" in compact


def test_planner_renders_system_firewall_zone_as_workload_route_parent():
    source = CONTROLLER.read_text()
    compact = "".join(source.split())

    assert "normalizeClientLayout" in source
    assert "node.visualParent||node.parent" in source
    assert "node.type==='firewall-zone'" in source
    assert "System managed" in source
    assert "Automatically allocated" in source
    assert "['site','firewall-zone','firewall','zone','vm']" in source
    assert "['zone','vm']" in source
    assert "systemManaged:node.type==='firewall-zone'" in compact
    assert "team:node.type==='zone'?node.value.team:null" in compact
    assert "childCount" in source
    assert "onArrangeZone" in source
    assert "state.layout=nextLayout" in compact


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


def test_planner_icon_picker_has_preview_search_and_keyboard_contracts():
    controller = CONTROLLER.read_text()
    picker = (ROOT / "frontend" / "static" / "event-planner-icon-picker.js").read_text()

    assert "renderIconPicker" in controller
    assert "bindIconPickers" in controller
    assert "machineAutomaticIcon" in controller
    assert 'role="listbox"' in picker
    assert "icon-picker-search" in picker
    assert "ArrowDown" in picker
    assert "ArrowUp" in picker
    assert "event.key === 'Enter'" in picker
    assert "Escape" in picker


def test_machine_nodes_render_as_standalone_icons_with_labels_below():
    canvas = CANVAS.read_text()

    assert "node-hit-target" in canvas
    assert "node-state-ring" in canvas
    assert "machine-label" in canvas
    assert "topologyNodePresentation" in canvas
    assert "machine-badge" not in canvas


def test_planner_renders_framed_zone_container_layers_and_states():
    canvas = CANVAS.read_text()
    css = CSS.read_text()

    assert "zone-container-header" in canvas
    assert "zone-arrange" in canvas
    assert "topology-containers" in canvas
    assert "topology-machines" in canvas
    assert ".zone-container.team-red" in css
    assert ".zone-container.system-managed" in css
    assert ".zone-container.selected" in css
    assert ".zone-container.invalid" in css


def test_zone_drag_translates_children_and_persists_once_on_release():
    canvas = CANVAS.read_text()
    compact = "".join(canvas.split())

    assert "zone-container-header" in canvas
    assert "translateZoneLayout" in canvas
    assert "dragStartLayout" in canvas
    assert "updateMachineTransforms" in canvas
    assert "event.stopPropagation()" in canvas
    assert ".on('end',function(event,d)" in compact
