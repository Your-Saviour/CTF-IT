from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AttackNode:
    """Node in the EGATS attack tree."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    parent_id: str | None = None
    node_type: str = "hypothesis"
    status: str = "pending"
    description: str = ""
    promise_score: float = 0.5
    tdi: float = 0.5
    visit_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    depth: int = 0
    evidence: list[str] = field(default_factory=list)
    children: list["AttackNode"] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AttackTree:
    """EGATS attack tree with UCB selection and backpropagation."""

    root: AttackNode | None = None
    total_actions: int = 0
    budget_remaining: int = 100

    def select_next_node(self, node: AttackNode | None = None) -> AttackNode:
        candidates = self._get_pending_nodes(node or self.root)
        if not candidates:
            return self.root or AttackNode(description="No attack tree available")

        best = max(candidates, key=lambda n: self._ucb_score(n))
        best.visit_count += 1
        self.total_actions += 1
        return best

    def _ucb_score(self, node: AttackNode) -> float:
        if node.visit_count == 0:
            return node.promise_score + 2.0

        exploration_constant = math.sqrt(2)
        difficulty_penalty = 0.5

        exploitation = node.promise_score
        exploration = exploration_constant * math.sqrt(
            math.log(max(self.total_actions, 1)) / node.visit_count
        )
        penalty = difficulty_penalty * node.tdi

        return exploitation + exploration - penalty

    def _get_pending_nodes(self, node: AttackNode | None) -> list[AttackNode]:
        if node is None:
            return []

        pending = []
        if node.status in ("pending", "active"):
            pending.append(node)
        for child in node.children:
            pending.extend(self._get_pending_nodes(child))
        return pending

    def backpropagate(self, node: AttackNode, outcome: str, alpha: float = 0.7):
        rewards = {"success": 1.0, "partial": 0.5, "failure": 0.1}
        reward = rewards.get(outcome, 0.1)

        current = node
        while current is not None:
            current.promise_score = alpha * reward + (1 - alpha) * current.promise_score
            if outcome == "success":
                current.success_count += 1
            else:
                current.failure_count += 1
            current = self._find_parent(current)

    def prune_branch(self, node: AttackNode):
        node.status = "pruned"
        for child in node.children:
            child.status = "pruned"

    def add_child(self, parent: AttackNode, child: AttackNode) -> AttackNode:
        child.parent_id = parent.id
        child.depth = parent.depth + 1
        parent.children.append(child)
        return child

    def _find_parent(self, node: AttackNode) -> AttackNode | None:
        if not node.parent_id or not self.root:
            return None
        return self._find_node_by_id(self.root, node.parent_id)

    def _find_node_by_id(self, node: AttackNode, target_id: str) -> AttackNode | None:
        if node.id == target_id:
            return node
        for child in node.children:
            result = self._find_node_by_id(child, target_id)
            if result:
                return result
        return None

    def to_dict(self, node: AttackNode | None = None) -> dict:
        n = node or self.root
        if n is None:
            return {}
        return {
            "id": n.id,
            "type": n.node_type,
            "status": n.status,
            "description": n.description,
            "promise_score": round(n.promise_score, 3),
            "tdi": round(n.tdi, 3),
            "visits": n.visit_count,
            "depth": n.depth,
            "evidence": n.evidence,
            "children": [self.to_dict(c) for c in n.children],
        }
