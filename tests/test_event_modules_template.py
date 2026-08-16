from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_planned_vm_navigation_stacks_rows_vertically():
    css = (ROOT / "frontend/static/event-modules.css").read_text()
    assert "#vm-list{display:flex;flex-direction:column" in css
