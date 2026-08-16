from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_planned_vm_navigation_stacks_rows_vertically():
    css = (ROOT / "frontend/static/event-modules.css").read_text()
    assert "#vm-list{display:flex;flex-direction:column" in css


def test_workspace_has_rich_catalogue_and_two_inspector_tabs():
    html = (ROOT / "frontend/templates/event_modules.html").read_text()
    for marker in ['id="catalogue-filters"', 'id="assignment-tab"', 'id="details-tab"',
                   'id="inspector-content"', 'id="module-errors" aria-live="polite"']:
        assert marker in html


def test_admin_copy_does_not_use_pin_terminology():
    for path in [ROOT / "frontend/templates/event_modules.html", ROOT / "frontend/static/event-modules.js"]:
        text = path.read_text()
        assert "Pinned" not in text
        assert ">Pin<" not in text
