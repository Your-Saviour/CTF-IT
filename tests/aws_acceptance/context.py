import os
from dataclasses import dataclass

import pytest


@dataclass(frozen=True)
class AcceptanceContext:
    run_id: str
    expected_account_id: str
    account_id: str
    region: str

    @property
    def tags(self):
        return {"Application": "ctf-it", "ManagedBy": "ctf-it",
                "Environment": "acceptance", "AcceptanceRunId": self.run_id}


def require_acceptance_context(session_factory=None) -> AcceptanceContext:
    if os.environ.get("RUN_AWS_ACCEPTANCE") != "1":
        pytest.skip("AWS acceptance requires RUN_AWS_ACCEPTANCE=1")
    expected = os.environ.get("AWS_ACCEPTANCE_ACCOUNT_ID", "").strip()
    run_id = os.environ.get("AWS_ACCEPTANCE_RUN_ID", "").strip()
    region = os.environ.get("AWS_DEFAULT_REGION", "").strip()
    if not expected or not expected.isdigit() or not run_id or len(run_id) < 8 or not region:
        raise ValueError("approved account ID, unique run ID, and AWS region are required")
    if session_factory is None:
        from api.services.aws import AwsConfig, AwsSessionFactory
        session_factory = AwsSessionFactory(AwsConfig.from_env())
    identity = session_factory.caller_identity()
    if identity.account_id != expected:
        raise RuntimeError(f"refusing acceptance in AWS account {identity.account_id}; expected {expected}")
    return AcceptanceContext(run_id, expected, identity.account_id, region)
