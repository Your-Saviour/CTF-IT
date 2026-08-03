"""Ansible Semaphore REST API client.

Synchronous client designed to be called from asyncio.to_thread() background
tasks, matching the pattern used by _run_build() in api/routes/auth.py.

Semaphore API docs: https://ansible-semaphore.com/api-docs/
"""

from __future__ import annotations

import os

import httpx

SEMAPHORE_URL = os.environ.get("SEMAPHORE_URL", "http://ctf-semaphore:3000")
SEMAPHORE_ADMIN = os.environ.get("SEMAPHORE_ADMIN", "admin")
SEMAPHORE_ADMIN_PASSWORD = os.environ.get("SEMAPHORE_ADMIN_PASSWORD", "")


class SemaphoreError(Exception):
    """Raised when a Semaphore API call fails."""


class SemaphoreClient:
    """Stateful Semaphore API client.

    Usage::

        client = SemaphoreClient()
        client.login()
        project_id = client.create_project("VM 42")
        ...
        client.close()

    Or use as a context manager::

        with SemaphoreClient() as client:
            client.login()
            ...
    """

    def __init__(self):
        self._client = httpx.Client(
            base_url=SEMAPHORE_URL,
            timeout=30.0,
            follow_redirects=True,
        )

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        self._client.close()

    # ── Auth ─────────────────────────────────────────────────────────────────

    def login(self) -> None:
        """Authenticate and store session cookie."""
        resp = self._client.post(
            "/api/auth/login",
            json={"auth": SEMAPHORE_ADMIN, "password": SEMAPHORE_ADMIN_PASSWORD},
        )
        if resp.status_code != 204:
            raise SemaphoreError(
                f"Semaphore login failed ({resp.status_code}): {resp.text[:200]}"
            )

    # ── Projects ─────────────────────────────────────────────────────────────

    def create_project(self, name: str) -> int:
        """Create a project and return its ID."""
        resp = self._client.post(
            "/api/projects",
            json={"name": name, "alert": False},
        )
        if resp.status_code not in (200, 201):
            raise SemaphoreError(
                f"create_project failed ({resp.status_code}): {resp.text[:200]}"
            )
        return resp.json()["id"]

    # ── Key Store ─────────────────────────────────────────────────────────────

    def create_key(self, project_id: int, name: str, private_key_pem: str) -> int:
        """Store an SSH private key and return the key store ID."""
        resp = self._client.post(
            f"/api/project/{project_id}/keys",
            json={
                "name": name,
                "type": "ssh",
                "project_id": project_id,
                "ssh": {
                    "login": "",
                    "passphrase": "",
                    "private_key": private_key_pem,
                },
            },
        )
        if resp.status_code not in (200, 201):
            raise SemaphoreError(
                f"create_key failed ({resp.status_code}): {resp.text[:200]}"
            )
        return resp.json()["id"]

    # ── Inventory ─────────────────────────────────────────────────────────────

    def create_inventory(
        self,
        project_id: int,
        name: str,
        ip: str,
        ssh_user: str,
        ssh_port: int,
        key_id: int,
    ) -> int:
        """Create a static inventory for one host and return its ID."""
        port_arg = f" ansible_port={ssh_port}" if ssh_port != 22 else ""
        inventory_ini = (
            f"[targets]\n"
            f"{ip} ansible_user={ssh_user}{port_arg} "
            f"ansible_ssh_common_args='-o StrictHostKeyChecking=accept-new'\n"
        )
        resp = self._client.post(
            f"/api/project/{project_id}/inventory",
            json={
                "name": name,
                "project_id": project_id,
                "type": "static",
                "inventory": inventory_ini,
                "ssh_key_id": key_id,
                "become_key_id": None,
            },
        )
        if resp.status_code not in (200, 201):
            raise SemaphoreError(
                f"create_inventory failed ({resp.status_code}): {resp.text[:200]}"
            )
        return resp.json()["id"]

    def create_localhost_inventory(self, project_id: int, name: str, key_id: int) -> int:
        """Create a localhost inventory for local-connection playbooks."""
        inventory_ini = "localhost ansible_connection=local\n"
        resp = self._client.post(
            f"/api/project/{project_id}/inventory",
            json={
                "name": name,
                "project_id": project_id,
                "type": "static",
                "inventory": inventory_ini,
                "ssh_key_id": key_id,
                "become_key_id": None,
            },
        )
        if resp.status_code not in (200, 201):
            raise SemaphoreError(
                f"create_localhost_inventory failed ({resp.status_code}): {resp.text[:200]}"
            )
        return resp.json()["id"]

    # ── Repositories ──────────────────────────────────────────────────────────

    def create_repository(self, project_id: int, name: str, local_path: str, key_id: int) -> int:
        """Create a local-path repository (Semaphore reads playbook files from here)."""
        resp = self._client.post(
            f"/api/project/{project_id}/repositories",
            json={
                "name": name,
                "project_id": project_id,
                "git_url": local_path,
                "git_branch": "main",
                "ssh_key_id": key_id,
            },
        )
        if resp.status_code not in (200, 201):
            raise SemaphoreError(
                f"create_repository failed ({resp.status_code}): {resp.text[:200]}"
            )
        return resp.json()["id"]

    # ── Environments ──────────────────────────────────────────────────────────

    def create_environment(self, project_id: int, name: str, env_vars: dict) -> int:
        """Create a Semaphore environment (env vars passed to Ansible) and return its ID."""
        import json as _json
        resp = self._client.post(
            f"/api/project/{project_id}/environment",
            json={
                "name": name,
                "project_id": project_id,
                "password": None,
                "json": _json.dumps(env_vars),
                "env": None,
            },
        )
        if resp.status_code not in (200, 201):
            raise SemaphoreError(
                f"create_environment failed ({resp.status_code}): {resp.text[:200]}"
            )
        return resp.json()["id"]

    # ── Templates ─────────────────────────────────────────────────────────────

    def create_template(
        self,
        project_id: int,
        name: str,
        playbook: str,
        inventory_id: int,
        repository_id: int,
        key_id: int,
        extra_vars: dict | None = None,
        environment_id: int | None = None,
    ) -> int:
        """Create a job template and return its ID.

        extra_vars: optional dict passed as ``-e '{...}'`` ansible-playbook argument.
        environment_id: optional Semaphore environment ID for env var injection.
        """
        import json as _json
        if extra_vars:
            arguments = _json.dumps(["-e", _json.dumps(extra_vars)])
        else:
            arguments = "[]"
        resp = self._client.post(
            f"/api/project/{project_id}/templates",
            json={
                "name": name,
                "project_id": project_id,
                "inventory_id": inventory_id,
                "repository_id": repository_id,
                "environment_id": environment_id,
                "playbook": playbook,
                "arguments": arguments,
                "allow_override_args_in_task": False,
                "become_authorization": "",
                "type": "",
                "app": "ansible",
                "vault_key_id": None,
            },
        )
        if resp.status_code not in (200, 201):
            raise SemaphoreError(
                f"create_template failed ({resp.status_code}): {resp.text[:200]}"
            )
        return resp.json()["id"]

    # ── Tasks ─────────────────────────────────────────────────────────────────

    def run_task(self, project_id: int, template_id: int) -> int:
        """Start a task from a template and return the task ID."""
        resp = self._client.post(
            f"/api/project/{project_id}/tasks",
            json={
                "template_id": template_id,
                "debug": False,
                "dry_run": False,
                "playbook": "",
                "environment": "",
            },
        )
        if resp.status_code not in (200, 201):
            raise SemaphoreError(
                f"run_task failed ({resp.status_code}): {resp.text[:200]}"
            )
        return resp.json()["id"]

    def get_task_status(self, project_id: int, task_id: int) -> str:
        """Return task status: 'waiting', 'running', 'success', 'error'."""
        resp = self._client.get(f"/api/project/{project_id}/tasks/{task_id}")
        if resp.status_code != 200:
            raise SemaphoreError(
                f"get_task_status failed ({resp.status_code}): {resp.text[:200]}"
            )
        return resp.json().get("status", "")

    def get_task_output(self, project_id: int, task_id: int) -> list[str]:
        """Return list of log output lines for a task."""
        resp = self._client.get(f"/api/project/{project_id}/tasks/{task_id}/output")
        if resp.status_code != 200:
            raise SemaphoreError(
                f"get_task_output failed ({resp.status_code}): {resp.text[:200]}"
            )
        records = resp.json()
        return [r.get("output", "") for r in records if r.get("output")]
