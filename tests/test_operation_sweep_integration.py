# tests/test_operation_sweep_integration.py
import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("CALDERA_INTERNAL_URL") or os.environ.get("CTF_SKIP_CALDERA_TESTS"),
    reason="requires a running Caldera",
)


def test_single_ability_driver_round_trip():
    import asyncio
    from api.services.caldera import CalderaClient, get_caldera_api_key
    from api.services.operation_driver import OperationDriver

    async def run():
        async with CalderaClient(get_caldera_api_key()) as caldera:
            driver = OperationDriver(caldera)
            source_id = await driver.ensure_run_source(999999)
            result = await driver.execute("does-not-exist", "does-not-exist", "paw",
                                          "event-0", source_id, 30)
            return result
    result = asyncio.run(run())
    assert result.finished is False or result.status != 0
