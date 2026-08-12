from pathlib import Path


TEMPLATE = Path(__file__).resolve().parents[1] / "frontend" / "templates" / "event_plan.html"


def test_inline_plan_actions_are_exported_to_window():
    source = TEMPLATE.read_text()

    assert 'onclick="startEventFromPlan()"' in source
    assert "window.startEventFromPlan = async function()" in source
