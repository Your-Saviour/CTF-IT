# Green-Team Infrastructure and Expo-IT Deployment Design

## Goal

CTF-IT will provision shared, event-level green-team infrastructure as part of GameNet deployment. The first deployment module is Expo-IT: one dedicated Vultr VM per event, reachable by participants only through their team GameNet VPN, built from the Expo-IT repository's fixed `stable` branch, and automatically connected to CTF-IT's existing outbound integration subsystem.

The design establishes a reusable green-infrastructure and deployment-module contract rather than embedding Expo-IT provisioning inside the Expo-IT integration adapter.

## Scope

The first release includes:

- Event-level green infrastructure nodes that are provisioned once per event rather than repeated per team.
- Planner creation and editing of green VMs.
- Assignment of deployment modules to green VMs through the existing module-picker interaction.
- Deployment-module input and output facts, including encrypted secret inputs.
- A built-in Expo-IT deployment module with a fixed repository URL and `stable` branch.
- One-way, VPN-only participant access to the Expo-IT service.
- Automatic Expo-IT credential, destination, event binding, and initial synchronization setup.
- Retry-safe provisioning, progress reporting, redaction, and owned-resource cleanup.

The first release excludes:

- In-place Expo-IT upgrades.
- Configurable Expo-IT repositories or branches.
- Multiple Expo-IT instances in one event.
- Green-infrastructure providers other than the existing Vultr GameNet provider.
- A general-purpose secret manager or arbitrary secret sharing between events.

## Canonical Infrastructure Model

The event infrastructure document gains a top-level `green_infrastructure` object alongside `vpn_gateway` and `sites`:

```json
{
  "vpn_gateway": {},
  "sites": [],
  "green_infrastructure": {
    "vms": [
      {
        "key": "expo-it",
        "name": "Expo-IT",
        "base_type": "ubuntu_24_server",
        "default_plan": "vc2-1c-2gb",
        "region": "syd"
      }
    ]
  }
}
```

Green VM keys are unique within the event and produce stable planner identifiers of the form `green:<vm_key>`. Unlike nodes below `sites`, green nodes do not expand per team and do not have a team owner. Existing infrastructure documents without `green_infrastructure` normalize to an empty collection and retain their current behavior.

The module plan accepts green stable identifiers in addition to the existing `vm:<site>/<zone>/<endpoint>` identifiers. Green nodes use the same pinned/resolved module assignment shape, compatibility checks, dependency resolution, resource sizing, and resolution fingerprints as endpoint nodes. Only modules explicitly marked as deployment-capable can be assigned to green nodes; ordinary vulnerability, payload, goal, and learner-facing application modules remain endpoint-only.

## Deployment-Module Contract

Deployment modules reuse the existing module catalogue and ordered copy/run step model. They add a deployment capability and a fact contract. The contract declares:

- Input facts, each with a trait, label, value type, and secret classification.
- Output facts, each with a trait, value type, secret classification, and consuming integration action when applicable.
- A completion check used to determine whether a previously started step is already complete.
- Resource requirements and supported base types through the existing module fields.

The Expo-IT module is built into the catalogue and fixes both the repository URL and branch name. Its required secret input is `git.ssh_private_key`. Its non-secret outputs include the resolved Git commit, private service URL, and health status. Its generated API key is a secret output consumed directly by integration setup.

Fact references are resolved only at execution time. Secret values must not be interpolated into persisted playbooks, staged scripts, command strings stored in the database, progress messages, exception messages, or logs. The provisioning executor supplies secrets through protected temporary files or process input with restrictive permissions and removes those materials after the step.

Deployment facts are separate from Caldera operation facts. Caldera facts describe attack-operation inputs and discoveries; deployment facts configure and report infrastructure installation. The two contracts may share validation conventions, but they do not share storage or lifecycle.

## Secret Fact Storage and Planner UX

Secret deployment facts are not stored in the infrastructure or module-plan JSON. A dedicated event green-node fact API accepts create/replace and clear operations, verifies administrator authorization, validates that the requested trait is declared by an assigned deployment module, and encrypts the value using the platform's existing data-encryption facility.

Read responses expose only trait metadata, presence, and timestamps. They never return the encrypted payload or plaintext. The database record is scoped by event, green node, and trait. A uniqueness constraint prevents ambiguous duplicate values.

In the event planner, green nodes appear in a distinct “Green-team infrastructure” section outside the repeated team topology. Admins choose the VM base, region, and plan, then assign deployment modules with the existing module-picker interaction. Selecting Expo-IT reveals a write-only Git SSH private-key control. After saving, the control displays `Configured` with Replace and Clear actions. Planner saves record only the topology and module assignment; the secret control writes through the dedicated fact API.

Removing Expo-IT from the node or deleting the green node clears its stored input facts after explicit confirmation. Event-start preflight rejects missing required facts and returns the affected node and trait so the UI can link directly to the planner control.

## Provisioning Architecture

Green infrastructure is part of the existing retry-safe GameNet provisioning state machine. New phases are inserted before final lockdown and acceptance:

1. Validate green topology, deployment assignments, required facts, provider capacity, and module compatibility.
2. Materialize one event-owned VM record per green node.
3. Create the Vultr VM with temporary control-plane SSH access.
4. Configure participant VPN routing and provider/host firewall policies.
5. Execute assigned deployment modules in dependency order.
6. Run module completion and health checks.
7. Consume integration outputs and create owned integration records.
8. Trigger initial integration synchronization.
9. Delete consumed input secrets and temporary execution material.
10. Remove temporary access and include green services in final GameNet acceptance checks.

Green VMs are represented by normal VM records with `event_id`, no `team_id`, a dedicated green role, and durable provisioning step/error fields. Their planned stable key is persisted so retries resolve to the same VM record and provider instance. Materialization happens before provider mutations, matching the existing placeholder strategy.

The Expo-IT installer performs these idempotent operations:

- Install declared prerequisites on its supported Ubuntu base.
- Create a restrictive deploy-key file for the Git operation.
- Clone the fixed repository or fetch it when the working tree already exists.
- Checkout the remote `stable` branch and record its resolved commit.
- Build and configure Expo-IT using the repository's supported deployment procedure.
- Generate its management API key without logging or persisting it as a general deployment output.
- Start the service under the host's service manager or repository-defined container runtime.
- Verify the private health endpoint and authenticated management API contract.

A completion check validates the running service, repository commit, and required configuration before skipping work on retry. A partial or incompatible installation is repaired by the relevant idempotent step rather than by creating a second VM.

## Networking and Isolation

Expo-IT has a provider address during bootstrap, but it is not generally reachable from the Internet. The final provider firewall permits the Expo-IT application port only from the event's team VPN gateway addresses. Each generated participant VPN configuration receives a route for the Expo-IT service address through that team's gateway.

Host firewall policy on the Expo-IT VM permits established return traffic and required platform operations but prevents the VM from initiating connections into team workload networks. Gateway and firewall rules preserve existing cross-team isolation: a team can reach the shared Expo-IT service, but cannot use the shared route to reach another team's gateway or workload networks.

Temporary control-plane SSH follows the existing GameNet security ordering. It exists only while CTF-IT installs and validates the service, is restricted to the configured control-plane source, and is removed before the event opens. Final acceptance checks verify:

- Expo-IT is unreachable directly from an unapproved public source.
- Each team VPN path can reach Expo-IT HTTPS.
- Expo-IT cannot initiate connections into any team workload subnet.
- Existing same-site and cross-team isolation checks still pass.

## Automatic Integration Setup

After Expo-IT passes health and authenticated API checks, its output facts are consumed transactionally to configure the existing integration subsystem:

1. Store the generated API key in an encrypted token `ServiceCredential` owned by the green deployment.
2. Create an `IntegrationDestination` owned by the green deployment, using the Expo-IT private VPN URL and the generated credential.
3. Create and enable the event's `EventIntegration` binding.
4. Enqueue a priority initial synchronization job.

These operations are idempotent. A durable ownership reference connects the generated credential and destination to the event and green node, allowing retry to update the same records and cleanup to distinguish generated resources from administrator-managed destinations. Existing uniqueness rules still enforce one enabled Expo-IT binding per event and prevent a destination from being actively shared across events.

The API key moves directly from the module executor into encrypted service-credential storage. It is never returned by the deployment-fact API or retained as a plaintext output fact. The non-secret URL and resolved commit remain available as deployment provenance.

## Lifecycle, Retry, and Cleanup

Green infrastructure is a required part of event provisioning. The event remains closed in `provisioning` until Expo-IT is healthy, its integration binding exists, initial sync is queued, temporary access is removed, and acceptance checks pass. Failures set the event to `provision_failed` and identify the green VM, module, and safe step description.

The encrypted Git SSH-key input remains available while provisioning is incomplete so Retry can resume without asking for the key again. It is deleted only after all Expo-IT installation, integration setup, lockdown, and acceptance work succeeds. A failed cleanup of temporary secret material prevents successful completion and is itself retryable.

Destroy/reprovision removes the generated event binding, destination, and credential only when their ownership metadata points to the green deployment. It never deletes an administrator-managed credential or destination. Existing stop behavior remains unchanged; stopping an event does not implicitly destroy its infrastructure. A later, explicitly invoked infrastructure-destroy path owns VM and generated-integration cleanup.

The initial release does not pull a newer `stable` commit into a successfully deployed event. Reprovision is the supported way to adopt a different resolved commit.

## API and UI Observability

Provision-status responses include green VMs alongside team VMs with an explicit event-owned/green classification. The planner and provisioning views show:

- VM creation and deployment status.
- Current safe module step.
- Resolved Expo-IT commit after checkout.
- Private VPN URL after configuration.
- Health-check status.
- Generated integration binding and synchronization status.

Errors may contain module identifiers, step names, exit categories, and redacted diagnostics. They must not contain secret facts, Git key material, generated API keys, sensitive environment values, authenticated URLs, or raw subprocess output that may echo those values. Logging helpers apply the same redaction rules before messages reach application logs.

## Validation and Error Handling

Draft validation rejects:

- Duplicate or malformed green node keys.
- Unsupported bases or disabled deployment modules.
- Endpoint-only modules assigned to green nodes.
- Missing module dependencies or incompatible assignments.
- More than one Expo-IT deployment module in the event.
- A green node without a valid region or plan.

Event-start preflight additionally rejects missing required input facts, insufficient provider capacity, unavailable provider credentials, and conflicts with an existing enabled Expo-IT binding that is not owned by this deployment. No provider resource is mutated until all preflight checks pass.

Provider, Git, build, health, networking, and integration failures map to stable error categories. Retriable failures preserve provider IDs, resolved state, and encrypted input facts. Contract or configuration failures remain visible as non-destructive provisioning failures and require an administrator change before Retry.

## Testing Strategy

Automated tests cover:

- Infrastructure normalization and validation for absent, valid, duplicate, and malformed green nodes.
- Module-plan reconciliation and resolution for `green:<vm_key>` assignments.
- Deployment-module schema, dependency, base compatibility, input/output fact, and completion-check validation.
- Secret-fact create, replace, clear, authorization, encryption-at-rest, response redaction, and assignment-removal behavior.
- Planner state and DOM behavior for adding green VMs, assigning Expo-IT, and showing write-only fact state.
- Event-start preflight with missing facts and conflicting existing bindings.
- One-time green VM placeholder/provider creation and retry reuse.
- Expo-IT installation ordering, fixed `stable` checkout, resolved-commit provenance, health checks, and retry behavior with provider/Git/build failures.
- Firewall ordering, VPN routes, one-way access, public denial, team isolation, and temporary SSH removal.
- Transactional, idempotent creation of the owned credential, destination, binding, and initial sync job.
- SSH-key retention after failure and deletion only after complete success.
- Owned-resource cleanup that preserves administrator-managed integration records.
- Provision-status redaction and existing GameNet events with no green infrastructure.

The repository's disposable Docker test service remains the default test runner. A separately marked live acceptance test may clone the Expo-IT repository's `stable` branch, provision or emulate a target host, and prove that the resulting authenticated management API satisfies the existing Expo-IT round-trip contract. The live test is opt-in and is not required by the ordinary unit/integration suite.

## Success Criteria

The feature is complete when an administrator can add one green VM to a draft event, assign Expo-IT, save the required Git SSH key as a secret fact, and start the event; CTF-IT then creates exactly one Expo-IT VM, builds the fixed `stable` branch, restricts access to GameNet VPN paths, automatically creates and enables the Expo-IT integration, queues its first synchronization, removes the Git key after success, and reports enough non-sensitive state to diagnose or retry any failed step.
