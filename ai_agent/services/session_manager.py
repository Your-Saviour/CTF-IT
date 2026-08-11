from __future__ import annotations

import asyncio
import json
import time
import uuid
import logging
from datetime import datetime, timezone
from typing import Any

import httpx
from prometheus_client import Counter, Histogram

from ai_agent.config import get_config
from ai_agent.db import get_db
from ai_agent.db.models import AgentAction, AgentLog, AgentSession
from ai_agent.planner.egats import EGATSPlanner

logger = logging.getLogger(__name__)

# Prometheus metrics
ACTION_COUNTER = Counter(
    "agent_actions_total",
    "Total actions executed",
    ["type", "status", "risk_level"]
)
SESSION_DURATION = Histogram(
    "agent_session_duration_seconds",
    "Session duration",
    ["status"]
)
ACTION_LATENCY = Histogram(
    "agent_action_latency_seconds",
    "Action execution time"
)
SESSION_COUNT = Counter(
    "agent_sessions_total",
    "Total sessions created",
    ["status"]
)
OPERATION_SUCCESS = Counter(
    "agent_operations_success_total",
    "Successful Caldera operations"
)
OPERATION_FAILURE = Counter(
    "agent_operations_failure_total",
    "Failed Caldera operations"
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SessionManager:
    """Manages AI agent sessions."""

    def __init__(self):
        self.config = get_config()
        self.planners: dict[str, EGATSPlanner] = {}

    async def create_session(
        self,
        event_id: int,
        vm_id: int | None = None,
        target_ip: str | None = None,
        approval_required: bool | None = None,
    ) -> dict[str, Any]:
        session_id = str(uuid.uuid4())

        # Only require CTF_API_KEY when fetching attack tree for a VM
        attack_tree_json = None
        if vm_id:
            ctf_api_key = self.config.CTF_API_KEY
            if not ctf_api_key:
                raise ValueError("CTF_API_KEY not configured (required for VM targeting)")

            async with httpx.AsyncClient(timeout=30.0) as client:
                headers = {"X-API-Key": ctf_api_key}
                try:
                    url = f"{self.config.CTF_API_URL}/admin/caldera/attack-tree/{vm_id}"
                    logger.info(f"Fetching attack tree from {url}")
                    resp = await client.get(url, headers=headers)
                    logger.info(f"Attack tree response: {resp.status_code}, body length: {len(resp.text)}")
                    if resp.status_code == 200:
                        attack_tree_json = json.dumps(resp.json())
                        logger.info(f"Attack tree stored, length: {len(attack_tree_json)}")
                    else:
                        logger.warning(f"Failed to fetch attack tree: {resp.status_code} {resp.text[:200]}")
                except Exception as e:
                    logger.warning(f"Failed to fetch attack tree for VM {vm_id}: {e}")

        with get_db() as db:
            session = AgentSession(
                id=session_id,
                ctf_event_id=event_id,
                ctf_vm_id=vm_id,
                target_ip=target_ip,
                status="pending",
                attack_tree_json=attack_tree_json,
                max_steps=self.config.MAX_STEPS,
                approval_required=approval_required if approval_required is not None else self.config.APPROVAL_REQUIRED,
            )
            db.add(session)

        logger.info(f"Created session {session_id} for event {event_id}, vm {vm_id}")
        return self._session_to_dict(session)

    async def create_session_from_template(
        self,
        template_name: str,
        event_id: int,
        vm_id: int | None = None,
        **overrides
    ) -> dict[str, Any]:
        """Create session from predefined template."""
        template = self._get_template(template_name)

        session_id = str(uuid.uuid4())
        attack_tree_json = None

        if vm_id and template.get("fetch_attack_tree", True):
            ctf_api_key = self.config.CTF_API_KEY
            if ctf_api_key:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    headers = {"X-API-Key": ctf_api_key}
                    try:
                        resp = await client.get(
                            f"{self.config.CTF_API_URL}/admin/caldera/attack-tree/{vm_id}",
                            headers=headers,
                        )
                        if resp.status_code == 200:
                            attack_tree_json = json.dumps(resp.json())
                    except Exception as e:
                        logger.warning(f"Failed to fetch attack tree for VM {vm_id}: {e}")

        with get_db() as db:
            session = AgentSession(
                id=session_id,
                ctf_event_id=event_id,
                ctf_vm_id=vm_id,
                target_ip=template.get("target_ip"),
                status="pending",
                attack_tree_json=attack_tree_json,
                max_steps=template.get("max_steps", 100),
                approval_required=template.get("approval_required", True),
                template_name=template_name,
                **overrides
            )
            db.add(session)

        logger.info(f"Created session {session_id} from template {template_name}")
        return self._session_to_dict(session)

    def _get_template(self, template_name: str) -> dict:
        """Get predefined session template."""
        templates = {
            "quick_exploit": {
                "name": "Quick Exploit",
                "description": "Fast exploitation session with minimal approval",
                "max_steps": 50,
                "approval_required": False,
                "fetch_attack_tree": True,
                "target_ip": None,
            },
            "thorough_scan": {
                "name": "Thorough Scan",
                "description": "Comprehensive attack surface enumeration",
                "max_steps": 100,
                "approval_required": True,
                "fetch_attack_tree": True,
                "target_ip": None,
            },
            "goal_hunt": {
                "name": "Goal Hunt",
                "description": "Focus on achieving specific objectives",
                "max_steps": 75,
                "approval_required": True,
                "fetch_attack_tree": True,
                "target_ip": None,
            },
        }
        return templates.get(template_name, templates["quick_exploit"])

    async def start_session(self, session_id: str) -> dict[str, Any]:
        with get_db() as db:
            session = db.query(AgentSession).filter(AgentSession.id == session_id).first()
            if not session:
                raise ValueError(f"Session {session_id} not found")

            session.status = "running"
            session.started_at = utcnow()

        planner = EGATSPlanner(session_id)
        if session.attack_tree_json:
            planner.load_attack_tree_from_ctf(session.attack_tree_json)
        self.planners[session_id] = planner

        logger.info(f"Started session {session_id}")
        return await self.get_session(session_id)

    async def stop_session(self, session_id: str) -> dict[str, Any]:
        with get_db() as db:
            session = db.query(AgentSession).filter(AgentSession.id == session_id).first()
            if not session:
                raise ValueError(f"Session {session_id} not found")

            session.status = "stopped"
            session.stopped_at = utcnow()

        # Save checkpoint for resume
        planner = self.planners.pop(session_id, None)
        checkpoint = None
        if planner:
            checkpoint = {
                "tree_state": planner.get_tree_state(),
                "state_store": planner.state_store.get_all(),
                "budget_remaining": planner.tree.budget_remaining,
                "stopped_at": utcnow().isoformat(),
            }
            await planner.caldera_tool.close()

        session.checkpoint_json = json.dumps(checkpoint) if checkpoint else None
        logger.info(f"Stopped session {session_id}")
        return await self.get_session(session_id)

    async def resume_session(self, session_id: str) -> dict[str, Any]:
        """Resume a stopped session from checkpoint."""
        with get_db() as db:
            session = db.query(AgentSession).filter(AgentSession.id == session_id).first()
            if not session:
                raise ValueError(f"Session {session_id} not found")

            if not session.checkpoint_json:
                raise ValueError(f"Session {session_id} has no checkpoint to resume from")

            session.status = "running"
            session.started_at = utcnow()

        # Restore from checkpoint
        checkpoint = json.loads(session.checkpoint_json)

        # Validate checkpoint structure
        if not self._validate_checkpoint(checkpoint):
            raise ValueError(f"Session {session_id} has invalid checkpoint data")

        planner = EGATSPlanner(session_id)
        planner.load_attack_tree_from_ctf(checkpoint["tree_state"])

        # Validate tree integrity
        if not self._validate_tree_integrity(planner.tree):
            logger.warning(f"Session {session_id} tree integrity check failed, attempting repair")
            planner.tree = self._repair_tree(planner.tree)

        planner.state_store = StateStore(session_id)
        planner.state_store._restore(checkpoint["state_store"])
        planner.tree.budget_remaining = checkpoint["budget_remaining"]

        self.planners[session_id] = planner
        logger.info(f"Resumed session {session_id}")
        return await self.get_session(session_id)

    def _validate_checkpoint(self, checkpoint: dict) -> bool:
        """Validate checkpoint structure and data integrity."""
        required_keys = ["tree_state", "state_store", "budget_remaining", "stopped_at"]
        for key in required_keys:
            if key not in checkpoint:
                logger.error(f"Checkpoint missing required key: {key}")
                return False

        # Check tree state has required structure
        tree = checkpoint.get("tree_state", {})
        if not isinstance(tree.get("root"), dict):
            logger.error("Checkpoint tree_state missing valid root")
            return False

        return True

    def _validate_tree_integrity(self, tree: AttackTree) -> bool:
        """Validate tree structure and relationships."""
        if not tree.root:
            logger.error("Tree has no root")
            return False

        def check_node(node: AttackNode):
            # Check parent-child relationships
            if node.parent_id:
                parent = self._find_node(tree.root, node.parent_id)
                if not parent:
                    logger.error(f"Node {node.id} has invalid parent_id: {node.parent_id}")
                    return False

                # Check if parent actually has this child
                has_child = any(c.id == node.id for c in parent.children)
                if not has_child:
                    logger.error(f"Node {node.id} not found in parent {node.parent_id} children")
                    return False

            # Recursively check children
            for child in node.children:
                if not check_node(child):
                    return False

            return True

        return check_node(tree.root)

    def _repair_tree(self, tree: AttackTree) -> AttackTree:
        """Attempt to repair tree structure issues."""
        logger.warning("Attempting to repair tree structure...")

        # Rebuild tree from nodes and edges
        if not tree.root:
            return tree

        # Collect all nodes
        nodes = self._collect_all_nodes(tree.root)

        # Rebuild relationships
        children_map: dict[str, list[str]] = {}
        has_parent: set[str] = set()

        for node in nodes:
            children_map[node.id] = []

        # Rebuild edges from nodes
        for node in nodes:
            if node.parent_id and node.parent_id in children_map:
                children_map[node.parent_id].append(node.id)
                has_parent.add(node.id)

        # Rebuild tree
        new_tree = AttackTree()
        new_tree.budget_remaining = tree.budget_remaining

        # Find root nodes (no parent in our map)
        root_nodes = [n for n in nodes if n.parent_id is None or n.parent_id not in children_map]

        # If we have a root, rebuild from it
        if root_nodes:
            new_root = AttackNode(
                id="root",
                description="CTF Red Team Operation",
                node_type="observation",
                status="active",
            )
            new_tree.root = new_root

            for root_node in root_nodes:
                new_root.children.append(root_node)
                # Rebuild children
                if root_node.id in children_map:
                    for child_id in children_map[root_node.id]:
                        child = self._find_node_by_id(nodes, child_id)
                        if child:
                            child.parent_id = root_node.id
                            new_root.children.append(child)
                            # Continue rebuilding
                            if child_id in children_map:
                                for grandchild_id in children_map[child_id]:
                                    grandchild = self._find_node_by_id(nodes, grandchild_id)
                                    if grandchild:
                                        grandchild.parent_id = child_id
                                        child.children.append(grandchild)

        logger.info(f"Tree repair complete: {len(nodes)} nodes processed")
        return new_tree

    def _collect_all_nodes(self, node: AttackNode) -> list[AttackNode]:
        """Collect all nodes in the tree."""
        nodes = [node]
        for child in node.children:
            nodes.extend(self._collect_all_nodes(child))
        return nodes

    def _find_node_by_id(self, nodes: list[AttackNode], target_id: str) -> AttackNode | None:
        """Find a node by ID in a list."""
        for node in nodes:
            if node.id == target_id:
                return node
        return None

    async def get_session(self, session_id: str) -> dict[str, Any]:
        with get_db() as db:
            session = db.query(AgentSession).filter(AgentSession.id == session_id).first()
            if not session:
                raise ValueError(f"Session {session_id} not found")
            return self._session_to_dict(session)

    async def get_session_status(self, session_id: str) -> dict[str, Any]:
        """Get real-time session status for monitoring."""
        session = await self.get_session(session_id)

        # Check for pending actions
        pending_count = len([a for a in session.get("pending_actions", [])])

        # Check VM status if VM is specified
        vm_status = None
        if session.get("ctf_vm_id"):
            vm_status = await self._get_vm_status(session["ctf_vm_id"])

        return {
            **session,
            "pending_actions_count": pending_count,
            "vm_status": vm_status,
            "estimated_completion": self._estimate_completion(session),
        }

    async def _get_vm_status(self, vm_id: int) -> dict:
        """Get status of a VM."""
        try:
            ctf_api_key = self.config.CTF_API_KEY
            if not ctf_api_key:
                return {"error": "CTF_API_KEY not configured"}

            async with httpx.AsyncClient(timeout=10.0) as client:
                headers = {"X-API-Key": ctf_api_key}
                resp = await client.get(
                    f"{self.config.CTF_API_URL}/admin/vms/{vm_id}",
                    headers=headers,
                )
                if resp.status_code == 200:
                    vm = resp.json()
                    return {
                        "status": vm.get("status"),
                        "ip_address": vm.get("ip_address"),
                        "os": vm.get("os"),
                    }
        except Exception as e:
            logger.warning(f"Failed to get VM status for {vm_id}: {e}")
            return {"error": str(e)}

    def _estimate_completion(self, session: dict) -> str | None:
        """Estimate session completion time."""
        if not session.get("started_at"):
            return None

        current_step = session.get("current_step", 0)
        max_steps = session.get("max_steps", 100)

        if max_steps == 0:
            return None

        progress = current_step / max_steps
        if progress >= 1.0:
            return "Complete"

        # Estimate remaining time based on average step time
        avg_step_time = 30  # seconds
        remaining_steps = max_steps - current_step
        remaining_seconds = remaining_steps * avg_step_time

        if remaining_seconds < 60:
            return f"{remaining_seconds}s"
        elif remaining_seconds < 3600:
            return f"{remaining_seconds / 60:.0f}m"
        else:
            return f"{remaining_seconds / 3600:.1f}h"

    async def list_sessions(self) -> list[dict[str, Any]]:
        with get_db() as db:
            sessions = db.query(AgentSession).order_by(AgentSession.created_at.desc()).all()
            return [self._session_to_dict(s) for s in sessions]

    async def approve_action(self, session_id: str, action_id: str) -> dict[str, Any]:
        with get_db() as db:
            action = db.query(AgentAction).filter(
                AgentAction.id == action_id,
                AgentAction.session_id == session_id,
            ).first()
            if not action:
                raise ValueError(f"Action {action_id} not found")

            action.status = "approved"
            action.approved_at = utcnow()

        logger.info(f"Action {action_id} approved in session {session_id}")
        return await self._execute_approved_action(session_id, action_id)

    async def reject_action(self, session_id: str, action_id: str) -> dict[str, Any]:
        with get_db() as db:
            action = db.query(AgentAction).filter(
                AgentAction.id == action_id,
                AgentAction.session_id == session_id,
            ).first()
            if not action:
                raise ValueError(f"Action {action_id} not found")

            action.status = "rejected"
            action.completed_at = utcnow()

        logger.info(f"Action {action_id} rejected in session {session_id}")
        return {"action_id": action_id, "status": "rejected"}

    async def step(self, session_id: str) -> dict[str, Any] | None:
        start_time = time.time()

        planner = self.planners.get(session_id)
        if not planner:
            return None

        action_data = await planner.plan_next_action()
        if not action_data:
            return None

        with get_db() as db:
            session = db.query(AgentSession).filter(AgentSession.id == session_id).first()
            if not session or session.status != "running":
                return None

            tool_args_json = json.dumps(action_data.get("tool_args", {}))
            action = AgentAction(
                id=str(uuid.uuid4()),
                session_id=session_id,
                action_type=action_data.get("action_type", "recon"),
                risk_level=action_data.get("risk_level", "medium"),
                description=action_data.get("description", ""),
                reasoning=action_data.get("reasoning", ""),
                target=action_data.get("target"),
                tool=action_data.get("tool"),
                tool_args_json=tool_args_json,
                target_node_id=action_data.get("target_node_id"),
                status="approved" if not session.approval_required else "pending",
            )
            db.add(action)
            session.current_step += 1

        if session.approval_required:
            logger.info(f"Action pending approval: {action.id}")
            ACTION_COUNTER.labels(
                type=action_data.get("action_type", "unknown"),
                status="pending",
                risk_level=action_data.get("risk_level", "medium")
            ).inc()
            ACTION_LATENCY.observe(time.time() - start_time)
            return {"action_id": action.id, "status": "pending", "action": self._action_to_dict(action)}
        else:
            result = await self._execute_approved_action(session_id, action.id)
            ACTION_LATENCY.observe(time.time() - start_time)
            return result

    async def _execute_approved_action(self, session_id: str, action_id: str) -> dict[str, Any]:
        """Execute action with better error handling and retry logic."""
        planner = self.planners.get(session_id)
        if not planner:
            return {"error": "Planner not found"}

        with get_db() as db:
            action = db.query(AgentAction).filter(
                AgentAction.id == action_id
            ).first()

        if not action:
            return {"error": "Action not found"}

        action.status = "executing"
        action.executed_at = utcnow()

        # Execute with timeout
        try:
            action_dict = {
                "action_type": action.action_type,
                "description": action.description,
                "target": action.target,
                "tool": action.tool,
                "tool_args": json.loads(action.tool_args_json) if action.tool_args_json else {},
            }

            result = await asyncio.wait_for(
                planner.execute_action(action_dict),
                timeout=self.config.STEP_TIMEOUT
            )

            with get_db() as db:
                action.status = "completed"
                action.result = result
                action.completed_at = utcnow()

            # Record result on the correct node
            node_id = action.target_node_id or "root"
            outcome = "success" if "error" not in result.lower() and "not found" not in result.lower() else "failure"
            planner.record_result(node_id, outcome, result[:500])

            # Store result in state store for context
            planner.state_store.add("action_result", {
                "action_id": action_id,
                "outcome": outcome,
                "result": result[:1000],
            }, source_node_id=node_id)

            return {"action_id": action_id, "status": "completed", "result": result}

        except asyncio.TimeoutError:
            with get_db() as db:
                action = db.query(AgentAction).filter(
                    AgentAction.id == action_id
                ).first()
                if action:
                    action.status = "failed"
                    action.result = f"Execution timed out after {self.config.STEP_TIMEOUT}s"

            return {"action_id": action_id, "status": "failed", "error": "Timeout"}

        except Exception as e:
            with get_db() as db:
                action = db.query(AgentAction).filter(
                    AgentAction.id == action_id
                ).first()
                if action:
                    action.status = "failed"
                    action.result = str(e)

            return {"action_id": action_id, "status": "failed", "error": str(e)}

    async def get_logs(self, session_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with get_db() as db:
            logs = (
                db.query(AgentLog)
                .filter(AgentLog.session_id == session_id)
                .order_by(AgentLog.timestamp.desc())
                .limit(limit)
                .all()
            )
            return [self._log_to_dict(l) for l in logs]

    def _session_to_dict(self, session: AgentSession) -> dict[str, Any]:
        with get_db() as db:
            pending_actions = (
                db.query(AgentAction)
                .filter(
                    AgentAction.session_id == session.id,
                    AgentAction.status == "pending",
                )
                .all()
            )
            recent_actions = (
                db.query(AgentAction)
                .filter(AgentAction.session_id == session.id)
                .order_by(AgentAction.created_at.desc())
                .limit(10)
                .all()
            )

        planner = self.planners.get(session.id)
        tree_state = planner.get_tree_state() if planner else {}

        attack_tree = tree_state
        if not attack_tree and session.attack_tree_json:
            try:
                attack_tree = json.loads(session.attack_tree_json)
            except (json.JSONDecodeError, TypeError):
                pass

        return {
            "id": session.id,
            "ctf_event_id": session.ctf_event_id,
            "ctf_vm_id": session.ctf_vm_id,
            "target_ip": session.target_ip,
            "status": session.status,
            "current_step": session.current_step,
            "max_steps": session.max_steps,
            "approval_required": session.approval_required,
            "error_message": session.error_message,
            "created_at": session.created_at.isoformat() if session.created_at else None,
            "started_at": session.started_at.isoformat() if session.started_at else None,
            "stopped_at": session.stopped_at.isoformat() if session.stopped_at else None,
            "pending_actions": [self._action_to_dict(a) for a in pending_actions],
            "recent_actions": [self._action_to_dict(a) for a in recent_actions],
            "attack_tree": attack_tree,
        }

    def _action_to_dict(self, action: AgentAction) -> dict[str, Any]:
        return {
            "id": action.id,
            "action_type": action.action_type,
            "risk_level": action.risk_level,
            "description": action.description,
            "reasoning": action.reasoning,
            "target": action.target,
            "tool": action.tool,
            "status": action.status,
            "result": action.result,
            "created_at": action.created_at.isoformat() if action.created_at else None,
        }

    def _log_to_dict(self, log: AgentLog) -> dict[str, Any]:
        return {
            "id": log.id,
            "level": log.level,
            "component": log.component,
            "message": log.message,
            "metadata": json.loads(log.metadata_json) if log.metadata_json else {},
            "timestamp": log.timestamp.isoformat() if log.timestamp else None,
        }


session_manager = SessionManager()
