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


def test_workspace_uses_planner_shell_structure():
    html = (ROOT / "frontend/templates/event_modules.html").read_text()
    for marker in ['{% block body_class %}planner-page{% endblock %}',
                   'class="planner-toolbar"', 'class="planner-identity"',
                   'class="planner-actions"', 'class="planner-account"',
                   'class="planner-event-status"', 'class="planner-validation"']:
        assert marker in html


def test_workspace_uses_planner_geometry_and_theme_tokens():
    css = (ROOT / "frontend/static/event-modules.css").read_text()
    for rule in ['padding:12px', 'gap:12px',
                 'grid-template-columns:240px minmax(520px,1fr) 360px',
                 'background:var(--bg-surface)', 'border:1px solid var(--border)',
                 'border-radius:9px', '@media(max-width:1100px)']:
        assert rule in css


def test_assignment_provenance_uses_semantic_colour_tokens():
    css = (ROOT / "frontend/static/event-modules-colours.css").read_text()
    for rule in ['.module-card.provenance-manual{--state-color:var(--cyan)',
                 '.module-card.provenance-random{--state-color:var(--green)',
                 '.module-card.provenance-dependency{--state-color:var(--amber)',
                 '.module-card.provenance-absent{--state-color:var(--text-muted)',
                 '.module-card.invalid-state{--state-color:var(--red)',
                 '.provenance-marker{background:var(--state-color)']:
        assert rule in css


def test_controller_renders_provenance_classes_beyond_badges():
    script = (ROOT / "frontend/static/event-modules.js").read_text()
    assert 'provenance-${provenance}' in script
    assert 'class="usage-row provenance-${item.provenance}"' in script


def test_assignment_colours_use_flat_readable_surfaces_and_borders():
    css = (ROOT / "frontend/static/event-modules-colours.css").read_text()
    for rule in [
        "border-left:6px solid var(--state-color)",
        "border-color:var(--state-color)",
        "--state-surface:color-mix(in srgb,var(--state-color) 12%,var(--bg-elevated))",
        "background:var(--state-surface)",
        "background:var(--state-color)",
        "color:var(--bg-deep)",
        "box-shadow:inset 0 0 0 1px var(--state-color)",
    ]:
        assert rule in css
    assert "linear-gradient" not in css
    assert "filter:brightness" not in css


def test_high_contrast_stylesheet_uses_current_cache_version():
    html = (ROOT / "frontend/templates/event_modules.html").read_text()
    assert 'event-modules-colours.css?v=6' in html
    assert 'event-modules.js?v=4' in html


def test_coloured_cards_keep_module_information_readable():
    css = (ROOT / "frontend/static/event-modules-colours.css").read_text()
    for rule in [
        "--card-text-primary:#eef4ff",
        "--card-text-secondary:#bdc9da",
        ".module-card p{color:var(--card-text-primary)}",
        ".module-card code{color:var(--card-text-secondary)}",
        ".module-card.incompatible{opacity:1}",
    ]:
        assert rule in css


def test_catalogue_exposes_direct_dependant_focus_controls():
    html = (ROOT / "frontend/templates/event_modules.html").read_text()
    for marker in ['id="dependency-focus"', 'id="dependency-focus-copy"', 'id="clear-dependency-focus"']:
        assert marker in html


def test_controller_renders_only_valid_direct_dependants_as_applicable():
    script = (ROOT / "frontend/static/event-modules.js").read_text()
    for marker in ["directDependants", "relationshipFocus", "direct-dependant", "relationship-group", "Applies directly"]:
        assert marker in script
    assert "direct&&!bad&&!conflicting" in script


def test_relationship_parent_persists_across_normal_card_selection():
    script = (ROOT / "frontend/static/event-modules.js").read_text()
    for marker in [
        "relationship-group",
        "if(!relationshipFocus)relationshipFocus=selectedModule",
        'id="use-as-parent"',
        "relationshipFocus=selectedModule;render()",
    ]:
        assert marker in script


def test_direct_dependant_style_preserves_invalid_red_precedence():
    css = (ROOT / "frontend/static/event-modules-colours.css").read_text()
    for marker in ["--relation:#b388ff", ".module-card.direct-dependant", ".relationship-group", ".module-card.invalid-state"]:
        assert marker in css
