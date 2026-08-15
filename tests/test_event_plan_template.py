from pathlib import Path


TEMPLATE = Path(__file__).resolve().parents[1] / "frontend" / "templates" / "event_plan.html"
ROOT = TEMPLATE.parents[2]


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


def test_cloud_ui_uses_aws_contracts_and_no_vultr_actions():
    admin = (ROOT / "frontend" / "templates" / "admin.html").read_text()
    detail = (ROOT / "frontend" / "templates" / "vm_detail.html").read_text()
    topology = (ROOT / "frontend" / "templates" / "topology.html").read_text()
    assert "/admin/api/aws/instance-types" in admin
    assert "/admin/api/aws/amis" in admin
    assert "Create on AWS" in admin
    assert "Vultr" not in admin + detail + topology
    assert "/destroy-cloud" in detail + topology
    assert "Destroy EC2 instance" in detail
