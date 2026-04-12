# Base VM Types Design Spec

## Context

The platform currently has no concept of reusable, configurable base VM definitions. VMs are provisioned by passing a raw Vultr OS name string through `vm_quota`, and there's no structured way to define base packages, setup scripts, or service configurations that should be applied before modules. The single `base/Dockerfile` serves this role for Docker containers but doesn't extend to VMs.

This feature introduces file-based base VM type definitions — YAML files with co-located scripts and playbooks in a `bases/` directory — that define the foundation a VM is built on before vulnerability/hardening modules are applied. Modules gain a `supported_bases` field to declare compatibility, and the selector filters accordingly.

**Scope:** VM/Ansible provisioning path only. Docker builds continue using the existing `base/Dockerfile` unchanged.

## Base Type YAML Schema

Each base type lives in `bases/<id>/` with a YAML definition and co-located scripts/files/playbooks.

```
bases/
  ubuntu_24_server/
    ubuntu_24_server.yaml
    setup_base.sh
    sshd_config
    harden_baseline.yml
  ubuntu_22_minimal/
    ubuntu_22_minimal.yaml
    setup.sh
```

### YAML Fields

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `id` | `str` | yes | — | Unique identifier, must match directory name. Pattern: `^[a-zA-Z0-9_]+$` |
| `name` | `str` | yes | — | Human-readable display name |
| `description` | `str` | yes | — | What this base type provides |
| `os` | `str` | yes | — | Vultr OS image name (e.g., `"Ubuntu 24.04 LTS x64"`) |
| `default_plan` | `str` | yes | — | Default Vultr plan ID (e.g., `"vc2-1c-2gb"`). Floor for plan sizing — module requirements can upsize beyond this |
| `packages` | `list[str]` | no | `[]` | APT packages to install before steps run |
| `steps` | `list` | no | `[]` | Ordered setup steps: `run`, `copy`, `playbook` (see below) |
| `disabled` | `bool` | no | `false` | If true, base type cannot be referenced by vm_quota |

### Step Types

Reuses the same step format as modules, plus a new `playbook` type:

- **`run: script.sh`** — Execute a shell script from the base directory
- **`copy: {src, dest, mode}`** — Copy a file from the base directory to the target
- **`playbook: playbook.yml`** — Run an Ansible playbook from the base directory against the target host

### Example

```yaml
id: ubuntu_24_server
name: "Ubuntu 24.04 Server"
description: "Standard Ubuntu 24.04 LTS with systemd, SSH, and common security tools"

os: "Ubuntu 24.04 LTS x64"
default_plan: "vc2-1c-2gb"

packages:
  - openssh-server
  - sudo
  - curl
  - python3
  - net-tools
  - iproute2
  - ufw
  - vim
  - wget
  - cron
  - rsyslog
  - ca-certificates
  - git

steps:
  - copy:
      src: sshd_config
      dest: /etc/ssh/sshd_config
      mode: "0644"
  - run: setup_base.sh
  - playbook: harden_baseline.yml
```

## Module Compatibility

Modules gain a new optional field:

```yaml
supported_bases:
  - ubuntu_24_server
  - ubuntu_22_minimal
```

- **Omitted or empty:** Module is compatible with all base types (backward compatible)
- **Present:** Module is only selected when the target VM's base type is in the list

### Selector Changes

In `select_modules()`, Phase 0 (existing `disabled` filter) gains an additional filter:

```python
if base_type_id:
    module_library = [
        m for m in module_library
        if not m.supported_bases or base_type_id in m.supported_bases
    ]
```

This runs before type/difficulty/category/tag selection, so quota counts apply against the already-filtered pool. If the filtered pool has fewer modules than the quota requests, the selector returns what's available (existing behavior for insufficient modules).

### Module Dataclass Change

In `builder/module_loader.py`, `Module` gains:

```python
supported_bases: list[str] = field(default_factory=list)
```

Loaded from YAML via `data.get("supported_bases", [])`.

## vm_quota Schema Changes

### Before

```json
{
  "ubuntu_target": {
    "os": "Ubuntu 24.04 LTS x64",
    "default_plan": "vc2-1c-2gb",
    "count": 3,
    "role": "target",
    "region": "ewr"
  }
}
```

### After

```json
{
  "ubuntu_target": {
    "base_type": "ubuntu_24_server",
    "count": 3,
    "role": "target",
    "region": "ewr"
  }
}
```

### Field Changes

- **`base_type`** (required, replaces `os`): References a base type ID from `bases/`. Validated against loaded base types.
- **`os`** (removed): Now comes from the base type definition.
- **`default_plan`** (optional override): If present, overrides the base type's `default_plan`. Allows event admins to set a higher resource floor for specific events without modifying the base type.
- **`count`**, **`role`**, **`region`**: Unchanged.

### Plan Sizing Resolution Order

1. Base type's `default_plan` (floor)
2. vm_quota's `default_plan` override (if present, becomes new floor)
3. Module `min_ram_mb` / `min_vcpu` sums can upsize beyond the floor (existing `plan_for_vm()` logic)

### Validation Changes

`builder/vm_quota_validation.py` → `validate_vm_quota()`:
- `base_type` is required, must be a non-empty string matching `^[a-zA-Z0-9_]+$`
- Must reference an existing, non-disabled base type (loaded via `load_all_bases()`)
- `os` field is no longer accepted
- `default_plan` becomes optional (string if present)
- Allowed keys: `base_type`, `default_plan`, `count`, `role`, `region`

## New Components

### `builder/base_loader.py`

Parallel to `module_loader.py`:

```python
@dataclass
class BaseType:
    id: str
    name: str
    description: str
    os: str
    default_plan: str
    packages: list[str]
    steps: list[RunStep | CopyStep | PlaybookStep]
    disabled: bool
    source_dir: Path

@dataclass
class PlaybookStep:
    playbook: str  # filename relative to base directory

def load_base_type(base_id: str) -> BaseType: ...
def load_all_bases() -> list[BaseType]: ...
```

- Scans `bases/` directory for subdirectories containing `<id>.yaml`
- Imports `RunStep` and `CopyStep` from `module_loader.py` (no need to move them — just import)
- Adds `PlaybookStep` for the new step type

### `builder/base_ansible.py`

Renders the base setup playbook:

```python
def render_base_playbook(base_type: BaseType) -> str: ...
def stage_base_files(base_type: BaseType, export_dir: Path) -> None: ...
```

Generates an Ansible playbook with:
1. Package installation task (`apt-get update && apt-get install -y ...`)
2. `run` steps → `ansible.builtin.script` tasks
3. `copy` steps → `ansible.builtin.copy` tasks
4. `playbook` steps → `ansible.builtin.include_tasks` (the playbook file is staged alongside other files)

**Note:** Playbook files referenced in `steps` are **Ansible task files** (a YAML list of tasks, not a full play with `hosts:`). They are staged alongside scripts/files and included inline via `ansible.builtin.include_tasks`. This matches how Ansible role task includes work.

Uses a new Jinja2 template `templates/base_playbook.yml.j2`.

## Provisioning Flow Changes

### `_run_provision()` in `api/routes/vm.py`

Updated to two phases:

1. **Base setup phase** (new):
   - Load base type from VM's `base_type` (resolved via vm_quota → base_type)
   - Generate base playbook via `render_base_playbook()`
   - Stage scripts/files/playbooks via `stage_base_files()`
   - Run via Semaphore
   - If fails → mark VM as `failed`, skip module phase

2. **Module application phase** (existing):
   - Generate module playbook from `VMModule` records (existing `render_playbook()`)
   - Run via Semaphore
   - If fails → mark VM as `failed`

### VM Model Change

`VM` model gains a `base_type` field (string, nullable) to track which base type was used. Set during `_provision_event_vms()` from the vm_quota entry's `base_type` value.

### Provision Step Updates

Full updated step sequence for a target VM:

```
queued → staging_playbook → configuring_semaphore → creating_instance → extracting_results
→ generating_base_playbook → running_base_playbook
→ generating_playbook → running_playbook → completed
```

New steps added for the base phase:
- `generating_base_playbook` — building the base setup Ansible playbook + staging files
- `running_base_playbook` — Semaphore is executing the base setup playbook against the target

Attacker VMs skip all playbook phases (no base setup, no modules) — they go straight to `active` after `extracting_results`.

## Admin UI Changes

### Event Form — VM Quota Editor

- Replace `os` dropdown with `base_type` dropdown (populated from loaded base types)
- `default_plan` becomes optional override field (show placeholder from base type's default)
- Show base type description on hover/selection

### VM Detail Page

- Show `base_type` in connection info section
- Show base type name and description

## Files to Create

- `bases/` directory with at least one example base type (e.g., `ubuntu_24_server`)
- `builder/base_loader.py` — BaseType dataclass and loader
- `builder/base_ansible.py` — Base playbook renderer and file stager
- `templates/base_playbook.yml.j2` — Jinja2 template for base setup playbook

## Files to Modify

- `builder/module_loader.py` — Add `supported_bases` field to `Module`, extract shared step types
- `builder/selector.py` — Add base type filtering in Phase 0 of `select_modules()`
- `builder/vm_quota_validation.py` — Update schema: `base_type` replaces `os`, `default_plan` optional
- `builder/plan_sizing.py` — Resolve `default_plan` from base type, allow vm_quota override
- `api/routes/vm.py` — Two-phase provisioning in `_run_provision()`, base_type on VM creation
- `api/routes/admin.py` — Pass base_type through event start flow, update validation
- `api/models.py` — Add `base_type` field to `VM` model
- `frontend/templates/` — Update VM quota editor and VM detail templates

## Verification Plan

1. **Unit:** Load a base type YAML, verify all fields parse correctly
2. **Unit:** `select_modules()` with `base_type_id` filters out incompatible modules
3. **Unit:** `validate_vm_quota()` accepts new schema, rejects old `os` field
4. **Unit:** `render_base_playbook()` produces valid Ansible YAML
5. **Integration:** Create an event with vm_quota referencing a base type, start it, verify VMs provision with base setup + modules in correct order
6. **Manual:** Admin UI — create event with VM quota, verify base type dropdown works, start event, watch provisioning dashboard
