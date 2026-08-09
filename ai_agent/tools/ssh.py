from __future__ import annotations

import logging
import os

import paramiko

from ai_agent.config import get_config

logger = logging.getLogger(__name__)


class SSHTool:
    """Tool for executing commands on target VMs via SSH.

    TODO: Currently disabled by default. Enable when SSH key is configured
    and security review is complete.
    """

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.config = get_config()
        self._client: paramiko.SSHClient | None = None

    def is_available(self) -> bool:
        return os.path.exists(self.config.SSH_KEY_PATH)

    def _get_client(self) -> paramiko.SSHClient:
        if self._client is None:
            self._client = paramiko.SSHClient()
            self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        return self._client

    def execute_command(self, target_ip: str, command: str, ssh_port: int = 22) -> str:
        """Execute a command on a remote host via SSH."""
        if not self.is_available():
            return "SSH tool unavailable: key not found"

        if not target_ip or not command:
            return "Missing target_ip or command"

        # Basic command validation - reject obviously dangerous patterns
        dangerous_patterns = ["rm -rf /", "> /etc/", "mkfs", "dd if=/dev"]
        if any(p in command for p in dangerous_patterns):
            return "Command rejected: potentially destructive"

        client = self._get_client()
        try:
            client.connect(
                hostname=target_ip,
                port=ssh_port,
                username=self.config.SSH_USER,
                key_filename=self.config.SSH_KEY_PATH,
                timeout=self.config.SSH_TIMEOUT,
            )
            stdin, stdout, stderr = client.exec_command(command, timeout=self.config.SSH_TIMEOUT)
            result = stdout.read().decode("utf-8", errors="replace")
            error = stderr.read().decode("utf-8", errors="replace")
            if error:
                result += f"\nSTDERR: {error}"
            return result[:4000]  # Truncate very long outputs
        except paramiko.AuthenticationException:
            return f"SSH authentication failed for {target_ip}"
        except paramiko.SSHException as e:
            return f"SSH error: {e}"
        except Exception as e:
            return f"Command execution failed: {e}"
        finally:
            client.close()
            self._client = None
