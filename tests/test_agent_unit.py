"""
Unit tests for AI agent features.
"""
import sys
sys.path.insert(0, '/app')
sys.path.insert(0, '/app/ai_agent')

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from ai_agent.memory.context import ContextManager
from ai_agent.memory.state_store import StateStore
from ai_agent.planner.attack_tree import AttackNode, AttackTree
from ai_agent.planner.tda import compute_tdi, select_mode, should_prune, assess_risk
from ai_agent.db import get_engine, init_db
from ai_agent.routes.sessions import RateLimiter


def test_rate_limiter_persists_datetime_values():
    record = MagicMock()
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = record
    context = MagicMock()
    context.__enter__.return_value = db

    limiter = RateLimiter()
    limiter.requests["agent_api"] = [1.0]

    with patch("ai_agent.routes.sessions.get_db", return_value=context):
        limiter._persist_to_db("agent_api")

    assert isinstance(record.last_request_at, datetime)
    assert isinstance(record.updated_at, datetime)


class TestTDAScore:
    """Test Task Difficulty Assessment."""

    @pytest.fixture(autouse=True)
    def setup_db(self):
        """Set up database for tests."""
        init_db()
        yield

    def test_compute_tdi_initial(self):
        """Test TDI computation for new node."""
        node = AttackNode(id="test", description="Test node", depth=0)
        tree = AttackTree()
        tdi = compute_tdi(node, tree)
        assert 0 <= tdi <= 1

    def test_compute_tdi_with_depth(self):
        """Test TDI increases with depth."""
        node = AttackNode(id="test", description="Test node", depth=5)
        tree = AttackTree()
        tdi = compute_tdi(node, tree)
        assert tdi >= 0.5

    def test_select_mode_bfs(self):
        """Test BFS mode selection for high TDI."""
        assert select_mode(0.7) == "bfs"

    def test_select_mode_dfs(self):
        """Test DFS mode selection for low TDI."""
        assert select_mode(0.2) == "dfs"

    def test_select_mode_hybrid(self):
        """Test hybrid mode selection for medium TDI."""
        assert select_mode(0.5) == "hybrid"

    def test_should_prune(self):
        """Test branch pruning logic."""
        node = AttackNode(id="test", description="Test node", depth=5, visit_count=3)
        tree = AttackTree()
        tdi = compute_tdi(node, tree)
        assert should_prune(node, tdi, threshold=0.3, min_attempts=3)

    def test_should_not_prune_low_tdi(self):
        """Test branch not pruned for low TDI."""
        node = AttackNode(id="test", description="Test node", depth=1)
        tree = AttackTree()
        tdi = compute_tdi(node, tree)
        assert not should_prune(node, tdi, threshold=0.8, min_attempts=3)

    def test_assess_risk_high(self):
        """Test high risk assessment."""
        assert assess_risk("exploit", "192.168.1.1", "Execute shell") == "high"

    def test_assess_risk_medium(self):
        """Test medium risk assessment."""
        assert assess_risk("recon", "192.168.1.1", "Scan ports") == "medium"

    def test_assess_risk_low(self):
        """Test low risk assessment."""
        assert assess_risk("info", "192.168.1.1", "Read service info") == "low"


class TestContextManager:
    """Test Context Manager."""

    @pytest.fixture(autouse=True)
    def setup_db(self):
        """Set up database for tests."""
        init_db()
        yield

    @pytest.fixture
    def state_store(self):
        """Create mock state store."""
        store = StateStore("test-session")
        return store

    @pytest.fixture
    def context_manager(self, state_store):
        """Create context manager with state store."""
        return ContextManager("test-session", state_store)

    def test_assemble_context(self, context_manager):
        """Test context assembly."""
        node = AttackNode(id="test", description="Test node", depth=0)
        tree = AttackTree()
        context = context_manager.assemble(node, tree, "hybrid", 0.5)

        assert "path" in context
        assert "mode" in context
        assert "siblings" in context
        assert "state" in context

    def test_estimate_load(self, context_manager):
        """Test context load estimation."""
        tree = AttackTree()
        load = context_manager.estimate_load(tree)
        assert 0 <= load <= 1

    def test_should_compress(self, context_manager):
        """Test compression threshold."""
        tree = AttackTree()
        load = context_manager.estimate_load(tree)
        assert isinstance(load, float)


class TestStateStore:
    """Test State Store."""

    @pytest.fixture(autouse=True)
    def setup_db(self):
        """Set up database for tests."""
        from ai_agent.db import get_engine, init_db

        # Always initialize the database to ensure tables exist
        init_db()
        yield

    @pytest.fixture
    def state_store(self):
        """Create state store."""
        return StateStore("test-session")

    def test_add_entity(self, state_store):
        """Test adding entity."""
        entity_id = state_store.add(
            "test_type",
            {"key": "value"},
            "node_1"
        )
        assert entity_id is not None

    def test_get_by_type(self, state_store):
        """Test getting entities by type."""
        state_store.add("test_type", {"key": "value1", "source_node_id": "node_1"}, "node_1")
        state_store.add("test_type", {"key": "value2", "source_node_id": "node_2"}, "node_2")

        entities = state_store.get_by_type("test_type")
        assert len(entities) == 2

    def test_get_all(self, state_store):
        """Test getting all entities."""
        state_store.add("test_type", {"key": "value1", "source_node_id": "node_1"}, "node_1")
        state_store.add("test_type", {"key": "value2", "source_node_id": "node_2"}, "node_2")

        all_entities = state_store.get_all()
        assert "node_1" in all_entities
        assert "node_2" in all_entities

    def test_query_with_filters(self, state_store):
        """Test querying with filters."""
        print(f"Testing query with filters - Session ID: {state_store.session_id}")
        state_store.add("test_type", {"key": "value1", "status": "success", "source_node_id": "node_1"}, "node_1")
        state_store.add("test_type", {"key": "value2", "status": "failed", "source_node_id": "node_2"}, "node_2")

        print(f"Adding entities complete - Session ID: {state_store.session_id}")
        results = state_store.query("test_type", {"status": "success"})
        print(f"Query results: {len(results)} entities found")
        assert len(results) == 1, f"Expected 1 entity, got {len(results)}"
        assert results[0]["key"] == "value1"

    def test_get_success_rate(self, state_store):
        """Test getting success rate."""
        state_store.add("action_result", {"target": "test", "outcome": "success"}, "node_1")
        state_store.add("action_result", {"target": "test", "outcome": "success"}, "node_2")
        state_store.add("action_result", {"target": "test", "outcome": "failed"}, "node_3")

        rate = state_store.get_success_rate("test")
        assert rate == 2/3


class TestAttackTree:
    """Test Attack Tree."""

    def test_ucb_score_initial(self):
        """Test UCB score for new node."""
        root = AttackNode(id="root", description="Root", node_type="observation")
        tree = AttackTree()
        tree.root = root

        node = AttackNode(id="child", description="Child", node_type="hypothesis")
        tree.add_child(root, node)

        score = tree._ucb_score(node)
        assert score > 0

    def test_ucb_score_with_visits(self):
        """Test UCB score with visit counts."""
        root = AttackNode(id="root", description="Root", node_type="observation")
        tree = AttackTree()
        tree.root = root

        node = AttackNode(id="child", description="Child", node_type="hypothesis")
        tree.add_child(root, node)

        # Simulate visits
        node.visit_count = 10
        node.success_count = 5
        tree.total_actions = 20

        score = tree._ucb_score(node)
        assert score > 0

    def test_backpropagate(self):
        """Test backpropagation."""
        root = AttackNode(id="root", description="Root", node_type="observation")
        tree = AttackTree()
        tree.root = root

        node = AttackNode(id="child", description="Child", node_type="hypothesis")
        tree.add_child(root, node)

        tree.backpropagate(node, "success")

        assert node.promise_score > 0.5
        assert node.success_count == 1

    def test_prune_branch(self):
        """Test branch pruning."""
        root = AttackNode(id="root", description="Root", node_type="observation")
        tree = AttackTree()
        tree.root = root

        node = AttackNode(id="child", description="Child", node_type="hypothesis")
        tree.add_child(root, node)

        child = AttackNode(id="grandchild", description="Grandchild", node_type="hypothesis")
        tree.add_child(node, child)

        tree.prune_branch(node)

        assert node.status == "pruned"
        assert child.status == "pruned"
