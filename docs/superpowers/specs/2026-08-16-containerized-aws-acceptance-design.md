# Containerized AWS Acceptance Design

## Goal

Run AWS authentication, account setup helpers, and the complete live acceptance suite in Docker containers. The dev host supplies only Docker, network connectivity, and the kernel `/dev/net/tun` device.

## Architecture

Add a dedicated Compose profile for AWS operations. An `aws-tools` service provides AWS CLI v2 for remote browser login and account inspection. An `aws-acceptance` service uses the project test runtime plus WireGuard tooling to execute `tests/aws_acceptance` and tagged cleanup.

Both services mount a named `aws_credentials` volume at `/root/.aws`. `aws login --remote` writes temporary login state into that volume; no AWS access keys or host credential directories are mounted. The acceptance container reads the same named profile through Botocore's standard credential chain.

The production API image and service remain separate from the AWS CLI tooling. The acceptance service receives only the capabilities required by the existing suite: host networking, `NET_ADMIN`, and `/dev/net/tun`.

## Images and Services

- Add an AWS tooling target that installs the official AWS CLI v2 and verifies its checksum during the image build.
- Add AWS CRT support to the Python runtime so Botocore can consume modern AWS CLI login profiles.
- Add WireGuard and required network utilities to the acceptance target.
- Add `aws-tools` and `aws-acceptance` Compose services behind an explicit `aws-acceptance` profile.
- Mount the named credential volume only into those services.

## Workflow

1. Build the tooling and acceptance images.
2. Start remote login inside `aws-tools` and complete authorization in a browser on another device.
3. Verify `sts:GetCallerIdentity` inside `aws-tools` reports the dedicated IAM user.
4. Run readiness and dry-run permission checks inside `aws-acceptance`.
5. Run the tagged standard VM, OPNsense AMI, and GameNet canaries inside `aws-acceptance`.
6. Run tagged cleanup and require an empty inventory.
7. Remove the temporary host Python virtualenv and host AWS CLI after the container workflow is proven.

## Safety and Failure Handling

- Live tests remain gated by `RUN_AWS_ACCEPTANCE=1`, the exact account ID, `AWS_ENVIRONMENT=acceptance`, and a unique run ID.
- All created resources retain `ManagedBy=ctf-it` and `AcceptanceRunId` tags.
- Cleanup operates only on the exact account and run ID and fails if owned inventory remains.
- The AWS credential volume contains temporary refresh/login material, is never committed, and can be deleted independently with `docker volume rm` after logout.
- No static AWS access key is accepted in Compose configuration.
- A failed canary triggers the existing session cleanup; interrupted runs use the containerized cleanup command.

## Verification

- A focused test proves AWS CRT is present in the runtime.
- Compose contract tests prove both services exist, use the named volume, expose no static keys, and restrict elevated networking to acceptance.
- Offline regression remains credential-free and passing.
- Live verification requires the dedicated IAM ARN, passing readiness, passing canaries, and an empty final tagged inventory.

