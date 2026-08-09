from __future__ import annotations

# Constants for use across the agent service

# Session states
SESSION_PENDING = "pending"
SESSION_RUNNING = "running"
SESSION_PAUSED = "paused"
SESSION_STOPPED = "stopped"
SESSION_COMPLETED = "completed"
SESSION_ERROR = "error"

# Action types
ACTION_CALDERA_OPERATION = "caldera_operation"
ACTION_CALDERA_ABILITY = "caldera_ability"
ACTION_SSH_COMMAND = "ssh_command"
ACTION_RECON = "recon"
ACTION_EXPLOIT = "exploit"
ACTION_PIVOT = "pivot"

# Risk levels
RISK_LOW = "low"
RISK_MEDIUM = "medium"
RISK_HIGH = "high"
RISK_CRITICAL = "critical"
