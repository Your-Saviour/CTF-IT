from __future__ import annotations

from dataclasses import dataclass

from ai_agent.planner.attack_tree import AttackNode, AttackTree


@dataclass
class TDIScore:
    """Task Difficulty Index with 4 weighted dimensions."""

    horizon: float = 0.5
    evidence_confidence: float = 0.5
    context_load: float = 0.5
    historical_success: float = 0.5
    value: float = 0.5

    WEIGHTS = {
        "horizon": 0.43,
        "evidence": 0.3,
        "context": 0.17,
        "success": 0.1,
    }

    def compute(self) -> float:
        self.value = (
            self.WEIGHTS["horizon"] * self.horizon
            + self.WEIGHTS["evidence"] * (1 - self.evidence_confidence)
            + self.WEIGHTS["context"] * self.context_load
            + self.WEIGHTS["success"] * (1 - self.historical_success)
        )
        return round(self.value, 2)

    def compute(self) -> float:
        self.value = (
            self.WEIGHTS["horizon"] * self.horizon
            + self.WEIGHTS["evidence"] * (1 - self.evidence_confidence)
            + self.WEIGHTS["context"] * self.context_load
            + self.WEIGHTS["success"] * (1 - self.historical_success)
        )
        return self.value


def compute_tdi(node: AttackNode, tree: AttackTree, context_load: float = 0.0, max_depth: int = 10) -> float:
    tdi = TDIScore()

    tdi.horizon = min(node.depth / max(max_depth, 1), 1.0)

    total_visits = node.success_count + node.failure_count
    if total_visits > 0:
        tdi.historical_success = (node.success_count + 1) / (total_visits + 2)
    else:
        tdi.historical_success = 0.5

    if node.evidence:
        tdi.evidence_confidence = min(len(node.evidence) / 3, 1.0)
    else:
        tdi.evidence_confidence = 0.2

    tdi.context_load = context_load

    return tdi.compute()


def select_mode(tdi_value: float) -> str:
    if tdi_value > 0.6:
        return "bfs"
    elif tdi_value < 0.3:
        return "dfs"
    else:
        return "hybrid"


def should_prune(node: AttackNode, tdi_value: float, threshold: float = 0.8, min_attempts: int = 3) -> bool:
    return tdi_value > threshold and node.visit_count >= min_attempts


def assess_risk(action_type: str, target: str | None, description: str) -> str:
    high_risk_keywords = ["exploit", "rce", "shell", "privilege", "escalat", "dump", "exfiltrat"]
    medium_risk_keywords = ["scan", "probe", "enumerate", "test", "check"]

    desc_lower = description.lower()

    if any(kw in desc_lower for kw in high_risk_keywords) or action_type in ("exploit", "caldera_ability"):
        return "high"
    if any(kw in desc_lower for kw in medium_risk_keywords) or action_type in ("recon", "caldera_operation"):
        return "medium"
    return "low"
