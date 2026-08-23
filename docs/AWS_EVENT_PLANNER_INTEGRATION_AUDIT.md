# AWS and Event Planner Integration Audit

Date: 2026-08-21

## Outcome

The event planner and AWS provisioning paths now share the same provider contract from draft creation through GameNet provisioning.

## Contract map

1. New event drafts and newly added planner nodes use `ap-southeast-2`, `t3.small`, and `t3.medium` AWS defaults.
2. The planner loads live AWS catalogue entries from `GET /admin/api/aws/plans`.
3. Event start validates every saved region and instance type against `AwsConfig` before readiness checks or cloud mutation.
4. AWS readiness sizing expands both normalized endpoint records and legacy count-based endpoint groups, so instance, ENI, and vCPU quotas include every planned VM.
5. Network allocation and placeholder creation preserve stable planner endpoint keys as `VM.vm_type` and store AWS metadata in provider-neutral columns.
6. Blue endpoint provisioning derives `vm:{site_key}/{zone_key}/{vm_type}` and consumes that assignment's saved `resolved_module_ids`. Quota selection remains the fallback only when the endpoint has no explicit assignment.
7. Event start rejects module assignments that reference removed planner VMs, unavailable modules, or modules incompatible with the endpoint base.
8. The Alembic graph has one head, `0010_aws_provider`, following the complete planner and operation migration chain.

Display-only planner address annotations remain isolated from AWS subnet and private-address allocation.

## Verification completed in this audit

- Focused Python suite: `116 passed` across `test_event_plan_template.py`, `test_module_plan.py`, and `test_gamenet.py`.
- Focused planner JavaScript suite: `17 passed`.
- JavaScript syntax checks passed for `event-planner.js` and `event-planner-state.js`.
- `alembic heads` returned only `0010_aws_provider`.
- `git diff --check` passed.

## Deferred to the full-test session

- Run the complete offline suite: `docker compose --profile test run --rm --build tests pytest -q`.
- Run the complete JavaScript test set.
- Apply migrations to a disposable copy of both a pre-planner and pre-AWS database.
- Exercise browser creation, editing, module resolution, preview, start, retry, and destroy workflows.
- Run the opt-in AWS readiness and acceptance suites with an approved account and unique run ID.
- Confirm AWS cleanup leaves no owned resources for that run ID.
