# tests/test_operation_driver.py
from api.services.operation_driver import AbilityResult, OperationDriver


class FakeCaldera:
    def __init__(self):
        self.operations = []
        self.op_counter = 0
        self.op_state = "finished"
        self.deleted = []

    async def ensure_source(self, source_id, name="ctf"):
        return None

    async def seed_facts(self, facts, source_id=None, name="ctf"):
        self.seeded = facts

    async def create_operation(self, name, adversary_id, planner_id, group,
                               source_id=None, auto_close=True, autonomous=True,
                               state=None, obfuscator="plain-text", jitter="2/8",
                               visibility=50, allowed_agents=None):
        self.op_counter += 1
        op = {"id": f"op-{self.op_counter}", "allowed_agents": allowed_agents}
        self.operations.append(op)
        return op

    async def get_operation(self, op_id, include_chain=False):
        return {"id": op_id, "state": self.op_state, "chain": [
            {"status": 0, "output": "VULNERABLE user=svc", "finish": "2026-08-18T00:00:00Z"},
        ]}

    async def get_agent_by_ip(self, ip):
        return {"paw": "abc123"}

    async def get_planner_by_name(self, name):
        return {"id": f"{name}-planner-id"}

    async def delete_operation(self, op_id):
        self.deleted.append(op_id)


def _run(coro):
    import asyncio
    return asyncio.run(coro)


def test_driver_returns_result_and_targets_single_agent():
    fake = FakeCaldera()
    driver = OperationDriver(fake)
    result = _run(driver.execute("some-ability", "some-adversary", "abc123",
                                 "event-1", "ctf-run-1", 120))
    assert isinstance(result, AbilityResult)
    assert result.status == 0
    assert result.finished is True
    assert "VULNERABLE" in result.output
    assert fake.operations[0]["allowed_agents"] == ["abc123"]


def test_driver_timeout_deletes_orphaned_operation():
    fake = FakeCaldera()
    fake.op_state = "running"
    driver = OperationDriver(fake)
    result = _run(driver.execute("some-ability", "some-adversary", "abc123",
                                 "event-1", "ctf-run-1", 0))
    assert result.status == -1
    assert result.finished is False
    assert fake.deleted == ["op-1"]
