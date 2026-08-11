"""
Integration tests for AI agent with mocked Caldera.
"""
import sys
sys.path.insert(0, '/app')
sys.path.insert(0, '/app/ai_agent')

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from ai_agent.tools.caldera import CalderaTool, _sanitize_name, _sanitize_id, validate_target


class TestCalderaSanitization:
    """Test Caldera input sanitization."""

    def test_sanitize_name_valid(self):
        """Test sanitizing valid name."""
        result = _sanitize_name("Test Operation")
        assert result == "Test Operation"

    def test_sanitize_name_invalid(self):
        """Test sanitizing invalid name."""
        result = _sanitize_name("Test@Operation#1")
        assert result == "unnamed"

    def test_sanitize_id_valid(self):
        """Test sanitizing valid ID."""
        result = _sanitize_id("op-123")
        assert result == "op-123"

    def test_sanitize_id_invalid(self):
        """Test sanitizing invalid ID."""
        result = _sanitize_id("op@123#")
        assert result is None

    def test_validate_target_valid_ip(self):
        """Test validating valid IP address."""
        assert validate_target("192.168.1.1")

    def test_validate_target_valid_domain(self):
        """Test validating valid domain."""
        assert validate_target("example.com")

    def test_validate_target_valid_uuid(self):
        """Test validating valid UUID."""
        assert validate_target("12345678-1234-1234-1234-123456789012")

    def test_validate_target_invalid(self):
        """Test validating invalid target."""
        assert not validate_target("not-a-valid-target")


class TestCalderaTool:
    """Test Caldera tool with mocked API."""

    @pytest.fixture
    def mock_caldera(self):
        """Create mock Caldera tool."""
        caldera = CalderaTool("test-session")
        caldera.client = AsyncMock()

        def get_side_effect(url, **kwargs):
            if "/planners" in url:
                return MagicMock(json=MagicMock(return_value=[
                    {"name": "atomic", "id": "planner-1"}
                ]))
            if "/adversaries" in url:
                return MagicMock(json=MagicMock(return_value=[
                    {"name": "CTF Full Exploit Chain", "adversary_id": "adv-1"}
                ]))
            if "/operations/" in url and "/abilities" not in url:
                return MagicMock(json=MagicMock(return_value={
                    "state": "completed",
                    "abilities": {"ability-456": "completed"}
                }))
            return MagicMock(json=MagicMock(return_value=[]))

        caldera.client.get = AsyncMock(side_effect=get_side_effect)
        caldera.client.post = AsyncMock(return_value=MagicMock(
            json=MagicMock(return_value={"id": "op-123"}),
            raise_for_status=MagicMock()
        ))
        caldera.client.patch = AsyncMock(return_value=MagicMock(
            raise_for_status=MagicMock()
        ))
        return caldera

    @pytest.mark.asyncio
    async def test_execute_operation(self, mock_caldera):
        """Test executing Caldera operation."""
        result = await mock_caldera.execute({
            "action_type": "caldera_operation",
            "adversary_name": "CTF Full Exploit Chain",
            "group": "test-group",
            "name": "Test Operation"
        })

        assert "op-123" in result
        mock_caldera.client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_ability(self, mock_caldera):
        """Test executing ability."""
        result = await mock_caldera.execute({
            "action_type": "caldera_ability",
            "operation_id": "op-123",
            "ability_id": "ability-456"
        })

        assert "ability-456" in result
        mock_caldera.client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_invalid_action_type(self, mock_caldera):
        """Test executing invalid action type."""
        result = await mock_caldera.execute({
            "action_type": "invalid_type"
        })

        assert "Unsupported Caldera action type" in result

    @pytest.mark.asyncio
    async def test_get_adversaries(self, mock_caldera):
        """Test getting adversaries."""
        adversaries = await mock_caldera._get_adversaries()

        assert len(adversaries) > 0
        assert adversaries[0]["name"] == "CTF Full Exploit Chain"

    @pytest.mark.asyncio
    async def test_get_planners(self, mock_caldera):
        """Test getting planners."""
        planners = await mock_caldera._get_planners()

        assert len(planners) > 0
        assert planners[0]["name"] == "atomic"

    @pytest.mark.asyncio
    async def test_list_abilities(self, mock_caldera):
        """Test listing abilities."""
        abilities = await mock_caldera.list_abilities()

        assert isinstance(abilities, list)

    @pytest.mark.asyncio
    async def test_resume_operation(self, mock_caldera):
        """Test resuming operation."""
        result = await mock_caldera.resume_operation("op-123")

        assert "op-123" in result
        mock_caldera.client.patch.assert_called_once()


class TestCalderaBatchOperations:
    """Test batch Caldera operations."""

    @pytest.fixture
    def mock_caldera(self):
        """Create mock Caldera tool."""
        caldera = CalderaTool("test-session")
        caldera.client = AsyncMock()

        def get_side_effect(url, **kwargs):
            if "/planners" in url:
                return MagicMock(json=MagicMock(return_value=[
                    {"name": "atomic", "id": "planner-1"}
                ]))
            if "/adversaries" in url:
                return MagicMock(json=MagicMock(return_value=[
                    {"name": "CTF Full Exploit Chain", "adversary_id": "adv-1"}
                ]))
            return MagicMock(json=MagicMock(return_value=[]))

        caldera.client.get = AsyncMock(side_effect=get_side_effect)
        caldera.client.post = AsyncMock(return_value=MagicMock(
            json=MagicMock(return_value={"id": "op-123"}),
            raise_for_status=MagicMock()
        ))
        return caldera

    @pytest.mark.asyncio
    async def test_batch_execute_abilities(self, mock_caldera):
        """Test executing multiple abilities in batch."""
        abilities = [
            {"id": "ability-1", "adversary_name": "CTF Full Exploit Chain"},
            {"id": "ability-2", "adversary_name": "CTF Full Exploit Chain"},
        ]

        results = await mock_caldera.batch_execute_abilities(abilities)

        assert len(results) == 3
        assert "op-123" in results[-1]  # Last result is operation confirmation

    @pytest.mark.asyncio
    async def test_batch_execute_with_invalid_adversary(self, mock_caldera):
        """Test batch execution with invalid adversary."""
        abilities = [
            {"id": "ability-1", "adversary_name": "Invalid Adversary"},
        ]

        results = await mock_caldera.batch_execute_abilities(abilities)

        assert "Invalid Adversary" in results[0]


class TestCalderaOperationMonitoring:
    """Test Caldera operation monitoring."""

    @pytest.fixture
    def mock_caldera(self):
        """Create mock Caldera tool."""
        caldera = CalderaTool("test-session")
        caldera.client = AsyncMock()

        def get_side_effect(url, **kwargs):
            if "/operations/" in url and "/abilities" not in url:
                return MagicMock(json=MagicMock(return_value={
                    "state": "running",
                    "agents": [{"id": "agent-1"}]
                }))
            return MagicMock(json=MagicMock(return_value=[]))

        caldera.client.get = AsyncMock(side_effect=get_side_effect)
        caldera.client.post = AsyncMock(return_value=MagicMock(
            json=MagicMock(return_value={"id": "op-123"}),
            raise_for_status=MagicMock()
        ))
        return caldera

    @pytest.mark.asyncio
    async def test_get_operation_status(self, mock_caldera):
        """Test getting operation status."""
        mock_caldera.client.get = AsyncMock(return_value=MagicMock(
            json=MagicMock(return_value={"state": "running", "agents": [{"id": "agent-1"}]}),
            raise_for_status=MagicMock()
        ))

        status = await mock_caldera._get_operation_status("op-123")

        assert status["state"] == "running"

    @pytest.mark.asyncio
    async def test_monitor_operation_running(self, mock_caldera):
        """Test monitoring running operation."""
        call_count = [0]

        def get_side_effect(url, **kwargs):
            call_count[0] += 1
            if call_count[0] >= 3:
                return MagicMock(json=MagicMock(return_value={"state": "completed"}), raise_for_status=MagicMock())
            return MagicMock(json=MagicMock(return_value={"state": "running", "agents": [{"id": "agent-1"}]}), raise_for_status=MagicMock())

        mock_caldera.client.get = AsyncMock(side_effect=get_side_effect)
        status = await mock_caldera.monitor_operation("op-123", timeout=5)
        assert status["state"] == "completed"

    @pytest.mark.asyncio
    async def test_monitor_operation_completed(self, mock_caldera):
        """Test monitoring completed operation."""
        mock_caldera.client.get = AsyncMock(return_value=MagicMock(
            json=MagicMock(return_value={"state": "completed"}),
            raise_for_status=MagicMock()
        ))

        status = await mock_caldera.monitor_operation("op-123", timeout=5)

        assert status["state"] == "completed"

    @pytest.mark.asyncio
    async def test_monitor_operation_timeout(self, mock_caldera):
        """Test monitoring operation with timeout."""
        mock_caldera.client.get = AsyncMock(return_value=MagicMock(
            json=MagicMock(return_value={"state": "running", "agents": [{"id": "agent-1"}]}),
            raise_for_status=MagicMock()
        ))

        status = await mock_caldera.monitor_operation("op-123", timeout=1)

        assert "error" in status
