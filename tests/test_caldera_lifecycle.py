"""Tests for event stop/delete Caldera operation lifecycle cleanup."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.routes.admin import _cleanup_caldera_operations_for_event


class _FakeCaldera:
    def __init__(self, operations):
        self.operations = operations
        self.deleted = []
        self.stopped = []
        self.update_operation = AsyncMock(side_effect=self._stop)
        self.delete_operation = AsyncMock(side_effect=self._delete)
        self.list_operations = AsyncMock(return_value=self.operations)

    async def _stop(self, op_id, **fields):
        self.stopped.append((op_id, fields))

    async def _delete(self, op_id):
        self.deleted.append(op_id)


class _FakeCalderaCtx:
    def __init__(self, fake):
        self.fake = fake

    async def __aenter__(self):
        return self.fake

    async def __aexit__(self, *exc):
        return False


def test_cleanup_stops_running_then_deletes():
    """Running/paused operations are finished before deletion."""
    fake = _FakeCaldera([
        {"id": "op1", "group": "event-7", "state": "running"},
        {"id": "op2", "group": "event-7", "state": "paused"},
        {"id": "op3", "group": "event-8", "state": "running"},
        {"id": "op4", "group": "event-7", "state": "finished"},
    ])
    with patch("api.services.caldera.CalderaClient", return_value=_FakeCalderaCtx(fake)):
        result = asyncio.run(_cleanup_caldera_operations_for_event(7))

    assert result["deleted"] == 3
    assert result["stopped"] == 2
    # op1 (running) and op2 (paused) stopped; op4 (finished) only deleted
    assert ("op1", {"state": "finished"}) in fake.stopped
    assert ("op2", {"state": "finished"}) in fake.stopped
    assert sorted(fake.deleted) == ["op1", "op2", "op4"]


def test_cleanup_no_operations_in_group():
    fake = _FakeCaldera([{"id": "op1", "group": "event-9", "state": "running"}])
    with patch("api.services.caldera.CalderaClient", return_value=_FakeCalderaCtx(fake)):
        result = asyncio.run(_cleanup_caldera_operations_for_event(7))
    assert result["deleted"] == 0
    assert result["stopped"] == 0


def test_cleanup_continues_when_stop_fails():
    """A stop failure should not prevent deletion."""
    fake = _FakeCaldera([
        {"id": "op1", "group": "event-7", "state": "running"},
        {"id": "op2", "group": "event-7", "state": "running"},
    ])

    async def _stop(op_id, **fields):
        if op_id == "op1":
            raise RuntimeError("stop failed")
        fake.stopped.append((op_id, fields))

    fake.update_operation = AsyncMock(side_effect=_stop)

    with patch("api.services.caldera.CalderaClient", return_value=_FakeCalderaCtx(fake)):
        result = asyncio.run(_cleanup_caldera_operations_for_event(7))

    assert result["deleted"] == 2
    assert len(result["errors"]) == 1
    assert "op1" in result["errors"][0]


def test_cleanup_caldera_unavailable():
    def _raise():
        raise RuntimeError("down")

    with patch(
        "api.services.caldera.CalderaClient",
        return_value=_FakeCalderaCtx(MagicMock(list_operations=AsyncMock(side_effect=_raise))),
    ):
        result = asyncio.run(_cleanup_caldera_operations_for_event(7))
    assert result["deleted"] == 0
    assert result["errors"] == ["Caldera unavailable: down"]
