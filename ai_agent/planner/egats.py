from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from ai_agent.config import get_config
from ai_agent.db import get_db
from ai_agent.db.models import AgentLog
from ai_agent.llm import get_llm
from ai_agent.memory.context import ContextManager
from ai_agent.memory.state_store import StateStore
from ai_agent.planner.attack_tree import AttackNode, AttackTree
from ai_agent.planner.tda import assess_risk, compute_tdi, select_mode, should_prune
from ai_agent.tools.caldera import CalderaTool

logger = logging.getLogger(__name__)


class EGATSPlanner:
    """Evidence-Guided Attack Tree Search planner (PentestGPT v2 inspired)."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.tree = AttackTree()
        self.state_store = StateStore(session_id)
        self.context_manager = ContextManager(session_id)
        self.caldera_tool = CalderaTool(session_id)
        self.llm = get_llm()
        self.config = get_config()

    def load_attack_tree_from_ctf(self, ctf_tree_json: str) -> AttackTree:
        try:
            ctf_data = json.loads(ctf_tree_json)
        except json.JSONDecodeError:
            logger.error("Failed to parse CTF attack tree JSON")
            return self.tree

        root = AttackNode(
            id="root",
            description="CTF Red Team Operation",
            node_type="observation",
            status="active",
            promise_score=1.0,
            tdi=0.0,
        )
        self.tree.root = root
        self.tree.budget_remaining = self.config.MAX_STEPS

        nodes = ctf_data.get("nodes", {})
        edges = ctf_data.get("edges", [])

        node_map: dict[str, AttackNode] = {}
        for nid, ndata in nodes.items():
            node = AttackNode(
                id=nid,
                description=f"{ndata.get('module_name', nid)} ({ndata.get('tactic', '')})",
                node_type="hypothesis",
                metadata=ndata,
                depth=ndata.get("phase", 0),
            )
            node_map[nid] = node

        # Build adjacency: edges are [source, target, ...] where source is prerequisite
        # We want: prerequisites are parents, dependents are children
        children_of: dict[str, list[str]] = {nid: [] for nid in node_map}
        has_parent: set[str] = set()

        for edge in edges:
            if len(edge) >= 2:
                src, dst = edge[0], edge[1]
                if src in node_map and dst in node_map:
                    children_of[src].append(dst)
                    has_parent.add(dst)

        # Build tree: nodes without parents are root children
        for nid, node in node_map.items():
            if nid not in has_parent:
                root.children.append(node)
            else:
                # Find parent (the prerequisite node)
                for parent_id, child_ids in children_of.items():
                    if nid in child_ids:
                        node.parent_id = parent_id
                        node_map[parent_id].children.append(node)
                        break

        self._log("Loaded attack tree from CTF platform", {"nodes": len(nodes), "edges": len(edges)})
        return self.tree

    async def plan_next_action(self) -> dict[str, Any] | None:
        if not self.tree.root:
            return None

        if self.tree.budget_remaining <= 0:
            self._log("Budget exhausted", level="warning")
            return None

        current_node = self.tree.select_next_node()
        current_node.status = "active"

        context_load = self.context_manager.estimate_load(self.tree)
        tdi_value = compute_tdi(current_node, self.tree, context_load)
        mode = select_mode(tdi_value)

        if should_prune(current_node, tdi_value):
            self.tree.prune_branch(current_node)
            self._log(f"Pruned branch {current_node.id} (TDI={tdi_value:.2f})")
            return await self.plan_next_action()

        # Prioritize targets if multi-target scenario
        target = current_node.metadata.get("target_ip") or current_node.metadata.get("target")
        if target:
            targets = await self._get_all_targets()
            if len(targets) > 1:
                prioritized = await self.prioritize_targets(targets)
                if target not in prioritized:
                    self._log(f"Target {target} not in top priorities, skipping", level="warning")
                    return await self.plan_next_action()
                else:
                    self._log(f"Target {target} prioritized ({prioritized.index(target) + 1}/{len(targets)})")

        context = self.context_manager.assemble(current_node, self.tree, mode, tdi_value)

        action = await self._query_llm(current_node, mode, tdi_value, context)
        if not action:
            return None

        action["risk_level"] = assess_risk(
            action.get("action_type", "recon"),
            action.get("target"),
            action.get("description", ""),
        )
        # Store target node ID for result recording
        action["target_node_id"] = current_node.id

        self._log(f"Proposed action: {action.get('description', '')}", {"mode": mode, "tdi": tdi_value})
        return action

    async def prioritize_targets(self, targets: list[str]) -> list[str]:
        """Score targets by open ports, services, and historical success rate."""
        scores = {}
        for target in targets:
            try:
                ports = await self._scan_ports(target)
                services = await self._scan_services(target)
                success_rate = self.state_store.get_success_rate(target)

                # Score: 40% ports, 30% services, 30% historical success
                scores[target] = (
                    len(ports) * 0.4 +
                    len(services) * 0.3 +
                    success_rate * 0.3
                )

                self._log(f"Target {target} scored: {scores[target]:.2f} (ports={len(ports)}, services={len(services)}, success={success_rate:.2f})")
            except Exception as e:
                self._log(f"Error scoring target {target}: {e}", level="warning")
                scores[target] = 0.0

        # Sort by score and return
        return sorted(targets, key=lambda t: scores[t], reverse=True)

    async def _get_all_targets(self) -> list[str]:
        """Get all target IPs from the attack tree."""
        targets = set()
        if not self.tree.root:
            return []

        def collect_targets(node: AttackNode):
            if node.metadata:
                target = node.metadata.get("target_ip") or node.metadata.get("target")
                if target:
                    targets.add(target)
            for child in node.children:
                collect_targets(child)

        collect_targets(self.tree.root)
        return list(targets)

    async def _scan_ports(self, target: str) -> list[str]:
        """Scan target for open ports using Caldera."""
        try:
            # Use Caldera to scan ports
            result = await self.caldera_tool.execute({
                "action_type": "caldera_operation",
                "adversary_name": "port-scan",
                "target": target,
            })
            return result.get("open_ports", [])
        except Exception as e:
            self._log(f"Port scan failed for {target}: {e}", level="warning")
            return []

    async def _scan_services(self, target: str) -> list[str]:
        """Scan target for services using Caldera."""
        try:
            # Use Caldera to scan services
            result = await self.caldera_tool.execute({
                "action_type": "caldera_operation",
                "adversary_name": "service-scan",
                "target": target,
            })
            return result.get("services", [])
        except Exception as e:
            self._log(f"Service scan failed for {target}: {e}", level="warning")
            return []

    async def prioritize_targets(self, targets: list[str]) -> list[str]:
        """Score targets by open ports, services, and historical success."""
        scores = {}
        for target in targets:
            ports = await self._scan_ports(target)
            scores[target] = len(ports) * 0.6 + self.state_store.get_success_rate(target) * 0.4
        return sorted(targets, key=lambda t: scores[t], reverse=True)

    async def _scan_ports(self, target: str) -> list[str]:
        """Scan target for open ports using Caldera."""
        try:
            # Use Caldera to scan ports
            result = await self.caldera_tool.execute({
                "action_type": "caldera_operation",
                "adversary_name": "port-scan",
                "target": target,
            })
            return result.get("open_ports", [])
        except Exception as e:
            self._log(f"Port scan failed for {target}: {e}", level="warning")
            return []

    def _progress_ratio(self) -> float:
        """Calculate session progress ratio for adaptive budget."""
        if not self.tree.root:
            return 0.0

        total_nodes = self._count_all(self.tree.root)
        completed = self._count_status(self.tree.root, "completed")
        failed = self._count_status(self.tree.root, "failed")

        if total_nodes == 0:
            return 0.0

        return (completed + failed) / total_nodes

    def _count_all(self, node: AttackNode | None) -> int:
        if node is None:
            return 0
        return 1 + sum(self._count_all(c) for c in node.children)

    def _count_status(self, node: AttackNode | None, status: str) -> int:
        if node is None:
            return 0
        count = 1 if node.status == status else 0
        for child in node.children:
            count += self._count_status(child, status)
        return count

    async def _query_llm(self, node: AttackNode, mode: str, tdi: float, context: str) -> dict[str, Any] | None:
        system_prompt = self._build_system_prompt(mode, tdi)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"{context}\n\nPropose the next action as JSON."},
        ]

        try:
            result = await self.llm.chat_structured(messages, schema={})
            if result.get("error"):
                self._log(f"LLM error: {result['error']}", level="error")
                return None
            return result
        except Exception as e:
            self._log(f"LLM query failed: {e}", level="error")
            return None

    def _build_system_prompt(self, mode: str, tdi: float) -> str:
        mode_guidance = {
            "bfs": "BREADTH-FIRST: Enumerate attack surface. Scan multiple targets. Gather intelligence before exploiting.",
            "dfs": "DEPTH-FIRST: Focus on the most promising vector. Exploit fully. Chain vulnerabilities.",
            "hybrid": "HYBRID: Balance exploration and exploitation based on evidence.",
        }[mode]

        return f"""You are an AI red team agent conducting authorized penetration testing.

CURRENT MODE: {mode_guidance}
TASK DIFFICULTY INDEX: {tdi:.2f}

Your goal is to compromise target systems and achieve objectives by:
1. Reconnaissance — discover services, vulnerabilities, attack surface
2. Exploitation — leverage vulnerabilities to gain access
3. Post-exploitation — escalate privileges, move laterally, achieve goals

Use Caldera operations for structured attacks. Be methodical and evidence-driven.

Respond with JSON:
{{
  "action_type": "caldera_operation" | "caldera_ability" | "recon" | "exploit",
  "description": "clear description of what you will do",
  "reasoning": "why this is the next best step",
  "target": "target IP or identifier",
  "tool": "tool name if applicable",
  "tool_args": {{}}
}}"""

    async def execute_action(self, action: dict[str, Any]) -> str:
        action_type = action.get("action_type", "")
        max_retries = 2

        for attempt in range(max_retries):
            try:
                if action_type in ("caldera_operation", "caldera_ability"):
                    result = await self.caldera_tool.execute(action)
                elif action_type in ("recon", "exploit"):
                    # Map to caldera_operation for now
                    action["action_type"] = "caldera_operation"
                    result = await self.caldera_tool.execute(action)
                else:
                    result = f"Action type {action_type} not yet implemented"

                if "error" not in result.lower() and "not found" not in result.lower():
                    return result

                # Log failure and adjust strategy
                self._log(f"Attempt {attempt + 1} failed: {result[:200]}", level="warning")

                # Add delay before retry
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)

            except Exception as e:
                self._log(f"Attempt {attempt + 1} exception: {e}", level="error")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)

        return f"Failed after {max_retries} attempts: {result}"

    def record_result(self, node_id: str, outcome: str, evidence: str | None = None):
        node = self._find_node(self.tree.root, node_id)
        if node:
            node.status = "completed" if outcome == "success" else "failed"
            if evidence:
                node.evidence.append(evidence[:500])
            self.tree.backpropagate(node, outcome)
            self.tree.budget_remaining -= 1
        else:
            logger.warning(f"record_result: node {node_id} not found in tree")

    def _find_node(self, root: AttackNode | None, target_id: str) -> AttackNode | None:
        if root is None or root.id == target_id:
            return root
        for child in root.children:
            result = self._find_node(child, target_id)
            if result:
                return result
        return None

    def _log(self, message: str, metadata: dict | None = None, level: str = "info"):
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "session_id": self.session_id,
            "level": level,
            "component": "egats",
            "message": message,
            "metadata": metadata or {},
        }

        # Write to structured log file
        log_dir = "logs"
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, f"agent_{self.session_id}.log")
        with open(log_file, "a") as f:
            f.write(json.dumps(log_entry) + "\n")

        # Also write to database
        with get_db() as db:
            log = AgentLog(
                session_id=self.session_id,
                level=level,
                component="egats",
                message=message,
                metadata_json=json.dumps(metadata or {}),
            )
            db.add(log)

    def get_tree_state(self) -> dict:
        return self.tree.to_dict()
