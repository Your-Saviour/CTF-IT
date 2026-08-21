# External Module Repositories

## Goal

Let administrators attach private git repositories that contribute additional CTF modules, so sensitive modules can live outside the public main repository. The private repos are configured from the admin frontend using a git SSH key, and their modules are merged into the existing catalogue at load time.

This is **purely additive**: private modules join the same catalogue as the built-in `modules/` directory. There is no per-event scoping, no visibility restriction on private module content beyond keeping it out of the public repo, and no change to how modules are selected, staged, or scored.

## Background

- `builder/module_loader.py` — `load_all_modules()` scans a single hardcoded `MODULES_DIR` (`modules/`) recursively for `*.yaml`, builds `Module` dataclasses, and returns the full library. It is called in ~25 places (selector, Caldera export, Ansible export, verification, learner/admin routes, scenarios).
- Each `Module` carries `source_dir` (the YAML's parent directory), used to stage scripts/files into Ansible exports and Caldera payloads. Staging copies from `source_dir` into `/shared/playbooks`, so a private repo only needs to be readable by the API container.
- Module layout is `modules/<type>/<module_id>/<module_id>.yaml` with optional scripts/files alongside.
- Secrets are encrypted at rest with `api/services/secrets.py` (`encrypt_secret` / `decrypt_secret`, keyed by `DATA_ENCRYPTION_KEY`) — the same path used for VPN, training, and OPNsense credentials.
- The production API container (`deploy/docker-compose.yml`) has no persistent volume of its own for content — only `ctf-shared_playbooks`, Caldera mounts, and a Postgres DB. The dev compose (`docker-compose.yml`) has only `api_data` (the SQLite DB).

## Design decisions (summary)

- **Multi-root directory scan.** `load_all_modules()` scans the built-in `modules/` plus every subdirectory of a persistent `MODULE_REPOS_DIR`. No change to the ~25 call sites; `source_dir`, the selector, and all exports keep working unchanged.
- **Repo layout mirrors `modules/`.** A private repo's root contains the type directories (`vulns/`, `hardening/`, `application_external/`, `application_internal/`, `payloads/`, `goals/`), each with `<module_id>/<module_id>.yaml` plus scripts/files.
- **Clone-fresh → validate → atomic swap.** Sync does a fresh shallow clone into a temp dir, validates every YAML, then atomically swaps it into place. A broken repo can never leave the platform in a state where `load_all_modules()` raises.
- **SSH key pasted and encrypted at rest.** The private key is stored encrypted via `encrypt_secret`; it is only ever written to disk transiently (mode `0600`) for the duration of a git operation.
- **Manual + event-start sync.** Repos are refreshed via a "Sync now" button and automatically before module selection at event start. No background polling.

## Data model

New `ModuleRepo` table (`api/models.py`):

| Column | Type | Notes |
|---|---|---|
| `id` | Integer PK | |
| `name` | String(128) | display name |
| `repo_url` | String(512) | git SSH URL (`git@host:path` or `ssh://…`) |
| `branch` | String(128) | default `"main"` |
| `ssh_key_encrypted` | Text | encrypted private key via `encrypt_secret` |
| `status` | String(16) | `pending` / `synced` / `error`, default `pending` |
| `last_sync_at` | DateTime | nullable |
| `last_error` | Text | nullable |
| `created_at` / `updated_at` | DateTime | |

No changes to any existing table.

## Storage & security

- New named volume `ctf-module_repos` mounted at `/app/module_repos` in the `api` service of both `docker-compose.yml` and `deploy/docker-compose.yml`. Path overridable via `MODULE_REPOS_DIR` (default `/app/module_repos`).
- Each repo's working copy lives at `MODULE_REPOS_DIR/<repo_id>/`.
- SSH key handling during sync: decrypt → write to a `tempfile` file with `0600` perms → run git with `GIT_SSH_COMMAND="ssh -i <keyfile> -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"` → delete keyfile in a `finally`. The key is never logged, never returned by any API, and never persisted unencrypted.
- Add `git` and `openssh-client` to the Docker base image (`Dockerfile`), so both the runtime and test images can clone.

## Loader integration

`builder/module_loader.py`:

- Add `MODULE_REPOS_DIR = Path(os.environ.get("MODULE_REPOS_DIR", "/app/module_repos"))`.
- Introduce `_module_roots()` returning `[MODULES_DIR]` plus each immediate subdirectory of `MODULE_REPOS_DIR` (when it exists).
- `load_all_modules()` iterates roots in order, globbing `root.rglob("*.yaml")` and skipping any path containing a `.git` component.
- Module IDs remain globally unique across roots; the existing post-load `requires`/foundation composition in `load_all_modules()` already runs over the merged library and is unchanged.
- `source_dir=yaml_path.parent` continues to work because each root mirrors the built-in layout.

## Git service

New `builder/module_repo.py` (pure, testable module mirroring the `builder/*.py` style):

- `sync_repo(repo: ModuleRepo) -> None` (raises on failure; the route catches and records `status`/`last_error`):
  1. Decrypt `ssh_key_encrypted`.
  2. Write the key to a `0600` temp file.
  3. `git clone --depth 1 --branch <branch> <url> <tmpdir>` with `GIT_SSH_COMMAND` set, into `MODULE_REPOS_DIR/.sync-<uuid>`.
  4. Validate: parse every `*.yaml` (excluding `.git`) through the same `Module` construction; fail on any malformed definition.
  5. Atomically swap: remove the existing `MODULE_REPOS_DIR/<repo_id>` and `os.replace(tmpdir, final)`.
  6. Always delete the temp keyfile.
- `delete_repo(repo)` removes the working-copy directory.

## Admin API & UI

New `api/routes/module_repos.py` (admin-only), template `frontend/templates/module_repos.html`:

- `GET /admin/module-repos` — page: repo list (name, URL, branch, status, last sync, error) + "Add repository" form (name, URL, branch, SSH key paste) + per-row Sync/Delete.
- `POST /admin/module-repos` — create; encrypts the key; immediately triggers an initial sync (surfacing result).
- `POST /admin/module-repos/{id}/sync` — clone-fresh sync; updates `status`/`last_sync_at`/`last_error`.
- `DELETE /admin/module-repos/{id}` — delete row and remove its working copy.
- `GET /admin/module-repos/data` — JSON list for the page.

A link is added to the admin panel alongside the existing modules/teams cards.

## Event start hook

`start_event` (wherever module selection is triggered for provisioning) runs `sync_repo` for all `ModuleRepo` rows before any `select_modules` / `load_all_modules` call, so newly pushed private modules are available at event start. A failed sync is recorded on the repo and surfaced in the UI, but does not block event start (the previous good clone remains in place).

## Error handling

- **Malformed private YAML cannot break the platform.** Validation happens at sync time; the atomic swap preserves the last-good clone. `load_all_modules()` never sees a broken working copy.
- **Clone/auth failures** set `status="error"` and `last_error`; the repo remains listed so the admin can correct and re-sync.
- **Missing `DATA_ENCRYPTION_KEY`** behaves as elsewhere (`encrypt_secret`/`decrypt_secret` raise a clear error before any key is persisted).
- **Duplicate module IDs** across a private repo and the built-in catalogue are caught by the existing uniqueness validation (surfaced as a sync-time error rather than a runtime crash).

## Testing

Follow the disposable Docker test-service pattern.

- **Loader** (`tests/test_module_loader.py`): multi-root scan via `tmp_path` + `monkeypatch` of `MODULE_REPOS_DIR`; `.git` directories are skipped; merged `requires` composition still works.
- **Git service** (`builder/module_repo.py`): clone from a local `file://` repo (no SSH needed); validation rejects malformed YAML; atomic swap leaves the previous clone on failure; keyfile cleanup.
- **Secrets**: encrypted round-trip for the SSH key (existing `test_secrets.py` pattern).
- **Routes** (`api/routes/module_repos.py`): CRUD + sync with a mocked `sync_repo`; admin auth enforced; key never echoed in responses.

## Non-goals

- No per-event scoping or visibility restriction of private modules.
- No scheduled/background auto-pull.
- No per-repo branch workflows beyond a single default branch.
- No support for private **base types** (`bases/`) — modules only.
