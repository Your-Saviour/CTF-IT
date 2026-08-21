"""Tests for Phase 2b: planner selection + human-in-loop operation controls."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from api.services.caldera import (
    LINK_STATUS_DISCARD,
    LINK_STATUS_EXECUTE,
    LINK_STATUS_PAUSE,
    CalderaClient,
)


@pytest.fixture
def client():
    c = CalderaClient.__new__(CalderaClient)
    c._client = MagicMock()
    return c


def _resp(json_data):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value=json_data)
    return resp


class TestCreateOperationControls:
    def test_default_autonomous_payload(self, client):
        client._client.post = AsyncMock(return_value=_resp({}))
        asyncio.run(client.create_operation(
            name="x", adversary_id="a", planner_id="p", group="g"
        ))
        payload = client._client.post.call_args.kwargs["json"]
        assert payload["autonomous"] == 1
        assert "state" not in payload
        assert payload["obfuscator"] == "plain-text"
        assert payload["jitter"] == "2/8"
        assert payload["visibility"] == 50

    def test_manual_operation_payload(self, client):
        client._client.post = AsyncMock(return_value=_resp({}))
        asyncio.run(client.create_operation(
            name="x", adversary_id="a", planner_id="p", group="g",
            autonomous=False, state="running",
        ))
        payload = client._client.post.call_args.kwargs["json"]
        assert payload["autonomous"] == 0
        assert payload["state"] == "running"


class TestUpdateOperation:
    def test_update_state(self, client):
        client._client.patch = AsyncMock(
            return_value=_resp({"id": "op1", "state": "paused"})
        )
        asyncio.run(client.update_operation("op1", state="paused"))
        assert client._client.patch.call_args.args[0] == "/api/v2/operations/op1"
        assert client._client.patch.call_args.kwargs["json"] == {"state": "paused"}

    def test_update_autonomous_coerced_to_int(self, client):
        client._client.patch = AsyncMock(return_value=_resp({}))
        asyncio.run(client.update_operation("op1", autonomous=False))
        assert client._client.patch.call_args.kwargs["json"] == {"autonomous": 0}

    def test_update_multiple_fields(self, client):
        client._client.patch = AsyncMock(return_value=_resp({}))
        asyncio.run(client.update_operation(
            "op1", state="running", obfuscator="base64", visibility=30
        ))
        assert client._client.patch.call_args.kwargs["json"] == {
            "state": "running", "obfuscator": "base64", "visibility": 30,
        }


class TestUpdateOperationLink:
    def test_link_status_patch(self, client):
        client._client.patch = AsyncMock(return_value=_resp({}))
        asyncio.run(client.update_operation_link("op1", "lnk1", status=LINK_STATUS_EXECUTE))
        assert client._client.patch.call_args.args[0] == (
            "/api/v2/operations/op1/links/lnk1"
        )
        assert client._client.patch.call_args.kwargs["json"] == {"status": -3}


class TestPlanners:
    def test_list_planners(self, client):
        client._client.get = AsyncMock(
            return_value=_resp([{"planner_id": "p1", "name": "atomic"}])
        )
        planners = asyncio.run(client.list_planners())
        assert planners == [{"planner_id": "p1", "name": "atomic"}]


class TestObfuscators:
    def test_list_obfuscators(self, client):
        client._client.get = AsyncMock(
            return_value=_resp([{"name": "base64", "description": "b64"}])
        )
        obfs = asyncio.run(client.list_obfuscators())
        assert obfs == [{"name": "base64", "description": "b64"}]


class TestDebrief:
    def test_get_operation_report(self, client):
        client._client.post = AsyncMock(return_value=_resp({"name": "op", "steps": []}))
        report = asyncio.run(client.get_operation_report("op1", include_output=True))
        assert report == {"name": "op", "steps": []}
        args, kwargs = client._client.post.call_args
        assert args[0] == "/api/v2/operations/op1/report"
        assert kwargs["json"] == {"enable_agent_output": True}

    def test_get_operation_event_logs(self, client):
        client._client.post = AsyncMock(return_value=_resp({"logs": []}))
        logs = asyncio.run(client.get_operation_event_logs("op1"))
        assert logs == {"logs": []}
        assert client._client.post.call_args.args[0] == (
            "/api/v2/operations/op1/event-logs"
        )


class TestLinkStatusConstants:
    def test_constants(self):
        assert LINK_STATUS_PAUSE == -1
        assert LINK_STATUS_EXECUTE == -3
        assert LINK_STATUS_DISCARD == -2
