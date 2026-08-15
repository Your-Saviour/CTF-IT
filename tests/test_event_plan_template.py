from pathlib import Path


TEMPLATE = Path(__file__).resolve().parents[1] / "frontend" / "templates" / "event_plan.html"
ROOT = TEMPLATE.parents[2]
EVENT_EDITOR = ROOT / "frontend" / "templates" / "admin_resource.html"


def test_inline_plan_actions_are_exported_to_window():
    source = TEMPLATE.read_text()

    assert 'onclick="startEventFromPlan()"' in source
    assert "window.startEventFromPlan = async function()" in source


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
