import pytest

from builder.ansible import dependency_order, render_playbook
from builder.module_loader import Module, RunStep


def _module(module_id: str, *, requires=None) -> Module:
    return Module(
        id=module_id,
        name=module_id,
        description="",
        type="vulnerability",
        difficulty="easy",
        points=100,
        category="test",
        requires=requires or [],
        steps=[RunStep(script=f"{module_id}.sh")],
    )


def test_render_playbook_puts_dependencies_before_dependents():
    application = _module("php_guestbook")
    vulnerability = _module("guestbook_phpinfo", requires=["php_guestbook"])

    playbook = render_playbook([vulnerability, application])

    assert playbook.index("php_guestbook__php_guestbook.sh") < playbook.index(
        "guestbook_phpinfo__guestbook_phpinfo.sh"
    )


def test_dependency_order_rejects_missing_assignments():
    vulnerability = _module("guestbook_phpinfo", requires=["php_guestbook"])

    with pytest.raises(ValueError, match="requires unassigned module 'php_guestbook'"):
        dependency_order([vulnerability])
