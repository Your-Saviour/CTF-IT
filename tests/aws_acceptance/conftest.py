import pytest

from .context import require_acceptance_context


@pytest.fixture(scope="session")
def aws_context():
    return require_acceptance_context()
