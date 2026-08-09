from __future__ import annotations

from ai_agent.planner.attack_tree import AttackTree, AttackNode
from ai_agent.config import get_config


class ContextManager:
    """Manages LLM context assembly and compression."""

    def __init__(self, session_id: str, state_store=None):
        self.session_id = session_id
        self.config = get_config()
        self.state_store = state_store

    def assemble(self, node: AttackNode, tree: AttackTree, mode: str, tdi_value: float) -> dict:
        return {
            "path": self._build_path_context(node, tree),
            "mode": self._build_mode_guidance(mode, tdi_value),
            "siblings": self._build_sibling_summaries(node, tree),
            "state": self._build_state_summary(tree),
            "vm_info": self._build_vm_context(),
            "goals": self._build_goal_context(),
        }

    def _build_path_context(self, node: AttackNode, tree: AttackTree) -> str:
        path = []
        current = node
        while current:
            status_marker = {
                "active": "[ACTIVE]",
                "completed": "[DONE]",
                "failed": "[FAILED]",
                "pruned": "[PRUNED]",
                "pending": "[PENDING]",
            }.get(current.status, "[?]")
            path.append(f"  {status_marker} {current.description} (id={current.id})")
            parent = self._find_parent(tree, current)
            current = parent

        return "## Current Attack Path\n" + "\n".join(reversed(path))

    def _build_mode_guidance(self, mode: str, tdi_value: float) -> str:
        guidance = {
            "bfs": "Breadth-first: enumerate all attack surfaces before going deep. Scan multiple targets.",
            "dfs": "Depth-first: focus on the most promising vector and exploit it fully.",
            "hybrid": "Hybrid (balanced): alternate between breadth and depth based on TDI.",
        }.get(mode, "Proceed with caution.")

        return f"## Mode Guidance\n{guidance}\nTDI: {tdi_value:.2f}"

    def _build_sibling_summaries(self, node: AttackNode, tree: AttackTree) -> str:
        parent = self._find_parent(tree, node)
        if not parent:
            return ""

        siblings = [c for c in parent.children if c.id != node.id]
        if not siblings:
            return ""

        lines = []
        for s in siblings:
            lines.append(f"- **{s.id}** [{s.status}, TDI={s.tdi:.2f}]: {s.description[:60]}")

        return "## Sibling Branch Summaries\n" + "\n".join(lines)

    def _build_state_summary(self, tree: AttackTree) -> str:
        completed = self._count_status(tree.root, "completed")
        failed = self._count_status(tree.root, "failed")
        pending = self._count_status(tree.root, "pending")
        return f"## Progress\nCompleted: {completed} | Failed: {failed} | Pending: {pending} | Budget: {tree.budget_remaining}"

    def _build_vm_context(self) -> str:
        """Add VM-specific context (OS, installed packages, open ports)."""
        vm_info = self.state_store.get_by_type("vm_info")
        if not vm_info:
            return ""

        info = vm_info[0]
        return f"""## Target VM Context
- OS: {info.get('os', 'unknown')}
- Open Ports: {', '.join(info.get('open_ports', []))}
- Installed Packages: {', '.join(info.get('packages', [])[:10])}
- Current User: {info.get('current_user', 'unknown')}"""

    def _build_goal_context(self) -> str:
        """Add goal status context."""
        goals = self.state_store.get_by_type("goal_status")
        if not goals:
            return ""

        goal_list = []
        for goal in goals:
            status = goal.get('status', 'unknown')
            achievement_count = goal.get('achievement_count', 0)
            defend_count = goal.get('defend_count', 0)
            goal_list.append(f"- {goal.get('name', 'Unknown')}: {status} (achieved: {achievement_count}, defended: {defend_count})")

        return "## Goal Status\n" + "\n".join(goal_list)

    def _count_status(self, node: AttackNode | None, status: str) -> int:
        if node is None:
            return 0
        count = 1 if node.status == status else 0
        for child in node.children:
            count += self._count_status(child, status)
        return count

    def _find_parent(self, tree: AttackTree, node: AttackNode) -> AttackNode | None:
        if not node.parent_id or not tree.root:
            return None
        return self._find_node(tree.root, node.parent_id)

    def _find_node(self, root: AttackNode, target_id: str) -> AttackNode | None:
        if root.id == target_id:
            return root
        for child in root.children:
            result = self._find_node(child, target_id)
            if result:
                return result
        return None

    def estimate_load(self, tree: AttackTree) -> float:
        """Estimate context load based on actual token counts."""
        context = self.assemble(None, tree, "hybrid", 0.5)
        context_text = self._format_context(context)
        token_count = self.estimate_tokens(context_text)

        # Calculate load as percentage of max tokens
        load = min(token_count / self.config.CONTEXT_MAX_TOKENS, 1.0)

        return load

    def _format_context(self, context: dict) -> str:
        """Format context dictionary into a single text string."""
        parts = []

        # Path context
        if "path" in context:
            parts.append("## Current Attack Path\n" + context["path"])

        # Mode guidance
        if "mode" in context:
            parts.append(f"## Mode Guidance\n{context['mode']}")

        # Sibling summaries
        if "siblings" in context:
            parts.append("## Sibling Branch Summaries\n" + context["siblings"])

        # State summary
        if "state" in context:
            parts.append("## Progress\n" + context["state"])

        # VM context
        if "vm_info" in context and context["vm_info"]:
            parts.append("## Target VM Context\n" + context["vm_info"])

        # Goal context
        if "goals" in context and context["goals"]:
            parts.append("## Goal Status\n" + context["goals"])

        return "\n\n".join(parts)

    def _count_all(self, node: AttackNode | None) -> int:
        if node is None:
            return 0
        return 1 + sum(self._count_all(c) for c in node.children)

    def _count_evidence(self, node: AttackNode | None) -> int:
        if node is None:
            return 0
        return len(node.evidence) + sum(self._count_evidence(c) for c in node.children)

    def should_compress(self, load: float) -> bool:
        """Check if context should be compressed based on load."""
        return load > self.config.CONTEXT_COMPRESSION_THRESHOLD

    def compress(self, context: dict) -> dict:
        """Compress context based on token count and evidence lists."""
        compressed = context.copy()

        # Calculate current token count
        context_text = self._format_context(compressed)
        current_tokens = self.estimate_tokens(context_text)

        # Compress path context
        if "path" in compressed:
            path_parts = compressed["path"].split("\n")
            if len(path_parts) > 20:
                compressed["path"] = "\n".join([
                    path_parts[0],
                    f"  ... ({len(path_parts) - 2} more nodes)",
                    path_parts[-1]
                ])
                compressed["path"] = self._compress_evidence(compressed["path"])

        # Compress sibling summaries
        if "siblings" in compressed:
            compressed["siblings"] = self._compress_evidence(compressed["siblings"])

        # Compress state summary if too long
        if "state" in compressed:
            state_tokens = self.estimate_tokens(compressed["state"])
            if state_tokens > 500:
                compressed["state"] = "## Progress\nCompleted: {completed} | Failed: {failed} | Pending: {pending} | Budget: {budget}".format(
                    completed=self._count_status(tree.root, "completed"),
                    failed=self._count_status(tree.root, "failed"),
                    pending=self._count_status(tree.root, "pending"),
                    budget=tree.budget_remaining
                )

        return compressed

    def _compress_evidence(self, text: str) -> str:
        """Compress evidence sections in text."""
        lines = text.split("\n")
        compressed_lines = []

        for line in lines:
            if "[ACTIVE]" in line or "[DONE]" in line or "[FAILED]" in line or "[PRUNED]" in line:
                compressed_lines.append(line)
            elif "evidence:" in line.lower():
                # Keep evidence count but truncate details
                if "..." in line:
                    compressed_lines.append(line)
                else:
                    compressed_lines.append(line[:100] + "...")
            else:
                compressed_lines.append(line)

        return "\n".join(compressed_lines)
