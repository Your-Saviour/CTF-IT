# OPNsense Acceptance AMI Cache Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retain one fully validated, ownership-tagged OPNsense acceptance AMI so later GameNet acceptance runs reuse it without rebuilding or redownloading OPNsense.

**Architecture:** A focused cache module owns deterministic keys, discovery, promotion, and deletion of acceptance AMIs. The AWS acceptance fixture discovers or builds once, promotes successful builds by replacing run ownership with cache ownership, and constructs the normal active database image record on cache hits. Per-run cleanup remains unchanged because cached artifacts no longer carry `AcceptanceRunId`; a separate containerized command removes expired or all cached artifacts.

**Tech Stack:** Python 3.12, boto3/EC2, SQLAlchemy, pytest, Docker Compose.

## Global Constraints

- Cached artifacts are accepted only in AWS account `512349491663` when that account is explicitly supplied by the acceptance environment.
- Cache ownership requires `Application=ctf-it`, `ManagedBy=ctf-it`, `Environment=acceptance`, and `ArtifactRole=opnsense-acceptance-cache`.
- The cache key includes region, architecture, OPNsense release, bootstrap SHA-256, golden-config schema revision, and image-build revision.
- Default retention is seven days; failed or partially validated builds are never promoted.
- Per-run cleanup must continue removing every temporary resource and must not special-case ordinary run resources.
- All tests, AWS login, cache inspection, and cache cleanup run in Docker containers.

---

### Task 1: Deterministic cache identity and discovery

**Files:**
- Create: `scripts/aws_acceptance_opnsense_cache.py`
- Create: `tests/test_aws_acceptance_opnsense_cache.py`

**Interfaces:**
- Produces: `CacheIdentity`, `cache_key(...) -> str`, `cache_tags(...) -> dict[str, str]`, and `discover_cache(ec2, identity, now=None) -> CachedAmi | None`.
- `CachedAmi` contains `ami_id: str`, `snapshot_ids: tuple[str, ...]`, `cache_key: str`, and `expires_at: datetime`.

- [ ] **Step 1: Write failing identity and discovery tests**

Test deterministic key changes for every declared input. Use a narrow fake EC2 client to assert discovery returns one available, unexpired, fully owned AMI; returns `None` for no match or expiry; and raises for multiple matches, missing ownership tags, missing snapshots, or snapshot ownership mismatch.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `docker compose --profile test run --rm --build tests pytest -q tests/test_aws_acceptance_opnsense_cache.py`

Expected: collection fails because `scripts.aws_acceptance_opnsense_cache` does not exist.

- [ ] **Step 3: Implement the minimal cache types and discovery**

Use canonical JSON plus SHA-256 for the key. Query self-owned images with all cache ownership tags and `CacheKey`; accept only `State=available`, parse an RFC3339 `ExpiresAt`, extract EBS snapshot IDs, then independently describe and ownership-check every snapshot.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the Task 1 command and expect all tests to pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/aws_acceptance_opnsense_cache.py tests/test_aws_acceptance_opnsense_cache.py
git commit -m "feat: discover cached OPNsense acceptance AMIs"
```

### Task 2: Promotion and fixture reuse

**Files:**
- Modify: `scripts/aws_acceptance_opnsense_cache.py`
- Modify: `tests/aws_acceptance/conftest.py`
- Modify: `tests/aws_acceptance/test_opnsense_ami.py`
- Modify: `tests/test_aws_acceptance_opnsense_cache.py`

**Interfaces:**
- Produces: `promote_cache(ec2, ami_id, snapshot_ids, identity, now=None, retention_days=7) -> CachedAmi`.
- Produces: `cached_image_record(db, cached, version, region) -> OpnsenseImage` in the acceptance fixture helper.
- Consumes: `AWS_ACCEPTANCE_FORCE_OPNSENSE_BUILD=1` to bypass discovery for a deliberate clean-slate builder run.

- [ ] **Step 1: Write failing promotion and fixture tests**

Assert promotion verifies the run-owned AMI and every snapshot, adds cache tags, removes `AcceptanceRunId` and other run-only identity tags only after validation succeeded, and returns the retained artifact. Assert a cache hit creates an active `OpnsenseImage` record with AMI/snapshot IDs and cache evidence without calling `run_image_build`. Assert forced mode calls the builder even when discovery returns a hit.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `docker compose --profile test run --rm --build tests pytest -q tests/test_aws_acceptance_opnsense_cache.py tests/test_opnsense_images.py`

Expected: failures for missing promotion and fixture-reuse behavior.

- [ ] **Step 3: Implement promotion and cache-aware fixture setup**

Download and hash the verified bootstrap source before computing identity. On a cache hit, populate the same active database state consumed by GameNet. On a miss, run the existing build and require `status=ready` plus both clone validation records before promotion. Track whether the fixture owns a temporary image; its finalizer retires only unpromoted artifacts.

- [ ] **Step 4: Make clean-slate intent explicit**

Update the OPNsense canary assertion to require `AWS_ACCEPTANCE_FORCE_OPNSENSE_BUILD=1` when it is being used as the dedicated builder test. Normal full-suite and GameNet runs use discovery/build-on-miss.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run the Task 2 command and expect all tests to pass.

- [ ] **Step 6: Commit**

```bash
git add scripts/aws_acceptance_opnsense_cache.py tests/aws_acceptance/conftest.py tests/aws_acceptance/test_opnsense_ami.py tests/test_aws_acceptance_opnsense_cache.py
git commit -m "feat: reuse validated OPNsense acceptance AMIs"
```

### Task 3: Explicit containerized cache cleanup

**Files:**
- Modify: `scripts/aws_acceptance_opnsense_cache.py`
- Modify: `docker-compose.yml`
- Modify: `Makefile`
- Modify: `tests/test_aws_acceptance_opnsense_cache.py`
- Modify: `tests/test_deploy_compose.py`

**Interfaces:**
- Produces: `cleanup_cache(ec2, expected_account_id, all_artifacts=False, now=None) -> dict[str, list[str]]`.
- Produces CLI flags `--expected-account-id` and `--all`; default behavior removes expired artifacts only.
- Produces Make target `aws-opnsense-cache-clean` that invokes the existing `aws-acceptance` container profile.

- [ ] **Step 1: Write failing cleanup safety tests**

Assert default cleanup deletes only expired owned AMIs and their exclusively referenced owned snapshots, `--all` deletes every owned cache artifact, account mismatch aborts before mutation, and missing/ambiguous/shared snapshot ownership aborts. Assert Compose/Make invoke the command in the acceptance container and expose no static AWS keys.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `docker compose --profile test run --rm --build tests pytest -q tests/test_aws_acceptance_opnsense_cache.py tests/test_deploy_compose.py`

Expected: failures for missing cleanup and command wiring.

- [ ] **Step 3: Implement cleanup and CLI**

Verify STS account identity before listing artifacts. Deregister each selected AMI, then delete only snapshots named in its block mappings after confirming full cache ownership and that no remaining self-owned AMI references them. Print JSON inventory/removal results and return nonzero on any safety refusal.

- [ ] **Step 4: Add containerized operator command**

Wire the cache CLI through the existing `aws-acceptance` image and AWS credential volume. Do not install boto3, AWS CLI, WireGuard, or project packages on the host.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run the Task 3 command, `docker compose --profile aws-acceptance config`, and `git diff --check`.

- [ ] **Step 6: Commit**

```bash
git add scripts/aws_acceptance_opnsense_cache.py docker-compose.yml Makefile tests/test_aws_acceptance_opnsense_cache.py tests/test_deploy_compose.py
git commit -m "feat: clean cached acceptance AMIs safely"
```

### Task 4: Containerized and live verification

**Files:**
- Modify only if a verified failure requires a TDD fix.

**Interfaces:**
- Consumes the cache and cleanup commands from Tasks 1-3.
- Produces test evidence and an empty temporary-resource inventory, with only the tagged cache AMI/snapshots retained.

- [ ] **Step 1: Run the entire offline suite**

Run: `docker compose --profile test run --rm --build tests pytest -q`

Expected: all non-live tests pass.

- [ ] **Step 2: Run one forced clean-slate builder acceptance**

Use a fresh run ID and `AWS_ACCEPTANCE_FORCE_OPNSENSE_BUILD=1`; run only `tests/aws_acceptance/test_opnsense_ami.py`. Confirm it promotes exactly one cache AMI and its snapshots and leaves no temporary run resources.

- [ ] **Step 3: Run focused GameNet acceptance from cache**

Use a second fresh run ID without force mode. Confirm logs/inventory show no builder instance or `CreateImage`, the cached AMI ID is reused, private endpoints route through OPNsense, and teardown leaves no run resources.

- [ ] **Step 4: Run the complete AWS acceptance suite from cache**

Use a third fresh run ID. Expect all acceptance tests to pass and independently inventory the run ID as empty afterward.

- [ ] **Step 5: Remove host-installed project tooling**

After live container verification succeeds, remove only `/opt/ctf-it-aws/.venv`, `/usr/local/aws-cli` plus `/usr/local/bin/aws`, and the `wireguard-tools` package from the dev host. Preserve Docker, `/dev/net/tun`, networking kernel modules, project files, and the Docker AWS credentials volume.

- [ ] **Step 6: Verify after host cleanup**

Run the full offline suite, Compose config validation, a cache inventory command, `git diff --check`, and `git status --short`, all through containers where applicable.
