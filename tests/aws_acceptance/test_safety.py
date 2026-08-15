import pytest

from scripts.aws_acceptance_cleanup import CleanupContext
from .context import require_acceptance_context


def test_acceptance_requires_explicit_opt_in(monkeypatch):
    monkeypatch.delenv("RUN_AWS_ACCEPTANCE", raising=False)
    with pytest.raises(pytest.skip.Exception):
        require_acceptance_context()


def test_cleanup_filter_requires_run_id_and_expected_account():
    with pytest.raises(ValueError): CleanupContext(run_id="", expected_account_id="123456789012")
    with pytest.raises(ValueError): CleanupContext(run_id="run-123456", expected_account_id="")
