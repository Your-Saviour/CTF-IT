from __future__ import annotations

import os
from functools import lru_cache


@lru_cache
def get_config():
    return AgentConfig()


class AgentConfig:
    """Configuration for the AI agent service."""

    # OpenAI-compatible API
    AI_API_BASE: str = os.environ.get("AI_API_BASE", "http://localhost:8080/v1")
    AI_API_KEY: str = os.environ.get("AI_API_KEY", "")
    AI_MODEL: str = os.environ.get("AI_MODEL", "gpt-4o")

    # Caldera
    CALDERA_URL: str = os.environ.get("CALDERA_INTERNAL_URL", "http://ctf-caldera:8888")
    CALDERA_CONFIG_PATH: str = os.environ.get("CALDERA_CONFIG_PATH", "/caldera-config/local.yml")

    # CTF API
    CTF_API_URL: str = os.environ.get("CTF_API_URL", "http://api:8000")
    CTF_API_KEY: str = os.environ.get("CTF_API_KEY", "")

    # Agent service auth (for proxy communication)
    AGENT_API_KEY: str = os.environ.get("AGENT_API_KEY", "")

    # Database
    DATABASE_URL: str = os.environ.get("AGENT_DATABASE_URL", "sqlite:///data/agent.db")

    # Agent behavior
    MAX_STEPS: int = int(os.environ.get("AGENT_MAX_STEPS", "100"))
    STEP_TIMEOUT: int = int(os.environ.get("AGENT_STEP_TIMEOUT", "120"))
    APPROVAL_REQUIRED: bool = os.environ.get("AGENT_APPROVAL_REQUIRED", "true").lower() == "true"

    # Caldera retry configuration
    CALDERA_MAX_RETRIES: int = int(os.environ.get("CALDERA_MAX_RETRIES", "3"))
    CALDERA_RETRY_DELAY: int = int(os.environ.get("CALDERA_RETRY_DELAY", "5"))
    CALDERA_RETRY_BACKOFF: int = int(os.environ.get("CALDERA_RETRY_BACKOFF", "2"))

    # Auto-stepping (background autonomous operation)
    AUTO_STEP: bool = os.environ.get("AGENT_AUTO_STEP", "false").lower() == "true"
    AUTO_STEP_INTERVAL: int = int(os.environ.get("AGENT_AUTO_STEP_INTERVAL", "30"))

    # Context management
    CONTEXT_MAX_TOKENS: int = int(os.environ.get("AGENT_CONTEXT_MAX_TOKENS", "128000"))
    CONTEXT_IDEAL_THRESHOLD: float = float(os.environ.get("AGENT_CONTEXT_IDEAL_THRESHOLD", "0.4"))
    CONTEXT_AGGRESSIVE_THRESHOLD: float = float(os.environ.get("AGENT_CONTEXT_AGGRESSIVE_THRESHOLD", "0.7"))
    CONTEXT_COMPRESSION_THRESHOLD: float = float(os.environ.get("AGENT_CONTEXT_COMPRESSION_THRESHOLD", "0.6"))

    # SSH (for direct VM access)
    SSH_KEY_PATH: str = os.environ.get("AGENT_SSH_KEY_PATH", "/shared/ssh/id_ed25519")
    SSH_USER: str = os.environ.get("AGENT_SSH_USER", "root")
    SSH_TIMEOUT: int = int(os.environ.get("AGENT_SSH_TIMEOUT", "30"))

    # WebSocket configuration
    WEBSOCKET_ENABLED: bool = os.environ.get("WEBSOCKET_ENABLED", "true").lower() == "true"
    WEBSOCKET_HEARTBEAT_INTERVAL: int = int(os.environ.get("WEBSOCKET_HEARTBEAT_INTERVAL", "30"))

    # Error feedback
    MAX_ERROR_HISTORY: int = int(os.environ.get("MAX_ERROR_HISTORY", "50"))
    ERROR_DISPLAY_DURATION: int = int(os.environ.get("ERROR_DISPLAY_DURATION", "300"))
    AUTO_RETRY_FAILED_OPS: bool = os.environ.get("AUTO_RETRY_FAILED_OPS", "false").lower() == "true"

    def get_caldera_api_key(self) -> str:
        import yaml

        if not os.path.exists(self.CALDERA_CONFIG_PATH):
            return ""
        with open(self.CALDERA_CONFIG_PATH) as f:
            config = yaml.safe_load(f)
        return config.get("api_key_red", "")
