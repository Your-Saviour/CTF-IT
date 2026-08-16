# Containerized AWS Acceptance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run AWS CLI authentication, readiness, cleanup, and all live AWS acceptance tests entirely through Docker Compose.

**Architecture:** An official pinned AWS CLI container writes temporary login state to a named Compose volume. A dedicated project acceptance image mounts the same volume and runs with host networking, `NET_ADMIN`, and `/dev/net/tun`; the production API image remains unchanged.

**Tech Stack:** Docker, Docker Compose, AWS CLI v2, Botocore CRT credential provider, pytest, WireGuard.

## Global Constraints

- The host supplies only Docker, network connectivity, and `/dev/net/tun`.
- No static AWS access keys or host credential-directory mounts.
- Live mutations retain the existing account, environment, opt-in, unique-run, and ownership-tag gates.
- Remove temporary host AWS CLI and Python tooling only after the container workflow passes.

---

### Task 1: Support AWS CLI Login Profiles in the Python Runtime

**Files:**
- Modify: `requirements.txt`
- Modify: `tests/test_aws_config.py`

**Interfaces:**
- Consumes: Botocore `login` credential provider selected by `AWS_PROFILE`.
- Produces: importable `awscrt==0.36.0` in test, acceptance, and production Python images.

- [x] **Step 1: Write the failing runtime dependency test**

```python
def test_runtime_includes_crt_for_aws_login_profiles():
    assert importlib.import_module("awscrt")
```

- [x] **Step 2: Run the focused test and verify RED**

Run: `docker compose --profile test run --rm tests pytest -q tests/test_aws_config.py -k runtime_includes_crt`

Expected and observed: FAIL with `ModuleNotFoundError: No module named 'awscrt'`.

- [ ] **Step 3: Add the exact Botocore CRT dependency**

Add `awscrt==0.36.0` to `requirements.txt`, matching the `botocore[crt]==1.43.51` metadata.

- [ ] **Step 4: Rebuild and verify the focused test**

Run: `docker compose --profile test run --rm --build tests pytest -q tests/test_aws_config.py`

Expected: PASS.

- [ ] **Step 5: Commit the credential-provider fix**

```bash
git add requirements.txt tests/test_aws_config.py
git commit -m "fix: support AWS CLI login profiles"
```

### Task 2: Add Containerized AWS Tooling and Acceptance Services

**Files:**
- Modify: `Dockerfile`
- Modify: `docker-compose.yml`
- Modify: `tests/test_deploy_compose.py`
- Modify: `tests/aws_acceptance/README.md`
- Modify: `TEST_PLAN.md`

**Interfaces:**
- Produces: `docker compose --profile aws-acceptance run --rm aws-tools login --remote --profile ctf-it-acceptance --region ap-southeast-2`.
- Produces: `docker compose --profile aws-acceptance run --rm aws-acceptance`.
- Produces: named `aws_credentials` volume mounted at `/root/.aws` only in AWS tooling services.

- [ ] **Step 1: Write failing Compose contract tests**

```python
def test_aws_login_and_acceptance_are_containerized():
    services = yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]
    tools = services["aws-tools"]
    acceptance = services["aws-acceptance"]
    assert "aws_credentials:/root/.aws" in tools["volumes"]
    assert "aws_credentials:/root/.aws" in acceptance["volumes"]
    assert acceptance["network_mode"] == "host"
    assert "NET_ADMIN" in acceptance["cap_add"]
    assert "/dev/net/tun:/dev/net/tun" in acceptance["devices"]
```

Also assert neither service contains `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, or a host `~/.aws` mount.

- [ ] **Step 2: Run the Compose contract and verify RED**

Run: `docker compose --profile test run --rm --build tests pytest -q tests/test_deploy_compose.py`

Expected: FAIL because `aws-tools` and `aws-acceptance` do not exist.

- [ ] **Step 3: Implement the acceptance image and services**

Add an `acceptance` Dockerfile target derived from `test` that installs `wireguard-tools`, `iproute2`, and `iptables`. Add an `aws-tools` service using `public.ecr.aws/aws-cli/aws-cli:2.36.24`, an `aws-acceptance` service built from the acceptance target, and an `aws_credentials` named volume. Pass only the existing AWS configuration, profile, and explicit acceptance-gate environment variables.

- [ ] **Step 4: Document exact container commands**

Replace host `python`, host `sudo`, and host AWS CLI instructions in `tests/aws_acceptance/README.md` and `TEST_PLAN.md` with the two Compose commands produced by this task plus the containerized cleanup command.

- [ ] **Step 5: Verify focused and offline regression tests**

Run:

```bash
docker compose --profile test run --rm --build tests pytest -q tests/test_aws_config.py tests/test_deploy_compose.py tests/aws_acceptance -k 'not standard_vm_create_configure_destroy and not gamenet_site_is_private_and_routes_through_opnsense and not opnsense_canary_builds_validates_and_activates_ami'
docker compose --profile aws-acceptance config >/dev/null
git diff --check
```

Expected: PASS; live cases remain inert without explicit opt-in.

- [ ] **Step 6: Commit containerized acceptance**

```bash
git add Dockerfile docker-compose.yml tests/test_deploy_compose.py tests/aws_acceptance/README.md TEST_PLAN.md
git commit -m "test: containerize AWS acceptance workflow"
```

### Task 3: Deploy and Run Live Acceptance

**Files:**
- No repository changes expected; operate the committed Compose workflow on `root@testubuntu.ye-et.com`.

**Interfaces:**
- Consumes: IAM user `ctf-it-automation`, account `512349491663`, region `ap-southeast-2`.
- Produces: readiness report, passing canaries, empty tagged inventory, and a host with no project Python virtualenv or host AWS CLI.

- [ ] **Step 1: Deploy the committed archive and build both container images**

Build `aws-tools` and `aws-acceptance` through the `aws-acceptance` profile.

- [ ] **Step 2: Authenticate inside the AWS tooling container**

Start `aws login --remote`, relay its URL, accept the one-time browser response, and verify STS returns `arn:aws:iam::512349491663:user/ctf-it-automation` from the same named volume.

- [ ] **Step 3: Run readiness inside the acceptance container**

Use the selected default VPC/subnet/security group, approved Ubuntu and FreeBSD AMIs, availability zone `ap-southeast-2a`, and control-plane CIDR `67.219.101.206/32`. Require every readiness check to pass before mutation.

- [ ] **Step 4: Run all tagged acceptance canaries**

Set `RUN_AWS_ACCEPTANCE=1`, `AWS_ENVIRONMENT=acceptance`, account `512349491663`, and a unique run ID of at least eight characters. Run `pytest -q tests/aws_acceptance` inside `aws-acceptance`.

- [ ] **Step 5: Prove cleanup**

Run the cleanup inventory command inside `aws-acceptance` for the exact run ID and require an empty result.

- [ ] **Step 6: Remove temporary host tooling**

After container verification succeeds, remove `/opt/ctf-it-aws/.venv` and uninstall the host AWS CLI installed during initial diagnostics. Retain Docker, `/dev/net/tun`, and WireGuard kernel support only.

- [ ] **Step 7: Run final repository verification**

Run the focused tests, full offline suite, Compose validation, `git diff --check`, and `git status --short`. Record exact pass/fail summaries.

