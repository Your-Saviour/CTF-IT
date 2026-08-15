# Firewall Zone Topology Design

## Purpose

The planner currently renders a site firewall as a special node and attaches workload zones directly to the site. That representation is technically misleading. A site has a firewall routing zone, the firewall VM is a member of that zone, and workload-zone traffic is routed through the firewall zone. The visual and planner model must express this distinction without prematurely implementing high availability provisioning.

## Approved Topology

Every site receives one automatic, system-managed Firewall Zone. The existing site firewall specification produces one Primary Firewall VM inside that zone. Workload zones remain owned by the site in the saved infrastructure document, but their visual network links originate from the Firewall Zone.

```text
VPN Gateway → Site → Firewall Zone
                       ├── Primary Firewall VM
                       ├→ Corporate Zone → VMs
                       └→ Red Team Zone → VMs
```

The Firewall Zone is the routing boundary. An individual firewall VM is not the parent of workload zones. This keeps the topology correct when a later HA implementation adds a second firewall VM to the same Firewall Zone.

## Data Boundary

This change does not introduce a `firewalls` collection or HA provisioning. The canonical infrastructure JSON remains backward compatible:

- `site.firewall` continues to hold the single firewall machine specification;
- `site.zones` continues to hold workload zones only;
- the automatic Firewall Zone is derived by planner normalization and is not serialized into `site.zones`;
- provisioning continues to create one firewall VM per site;
- address allocation continues to reserve the first site `/24` as the infrastructure subnet.

This boundary avoids inventing HA behavior before virtual addressing, failover, health checking, placement, and active/passive policy are specified.

## Planner Node Model

The planner uses distinct stable nodes:

- `site:<site-key>` — the site container;
- `firewall-zone:<site-key>` — the automatic routing zone;
- `firewall:<site-key>/primary` — the current firewall VM;
- `zone:<site-key>/<zone-key>` — a workload zone;
- `vm:<site-key>/<zone-key>/<vm-key>` — a workload VM.

Node ownership and diagram linkage are deliberately separate:

- the site owns the Firewall Zone and every workload zone;
- the Firewall Zone owns the Primary Firewall VM;
- workload zones use the Firewall Zone as their visual parent/link source;
- workload VMs use their workload zone as their parent.

The client node index will expose both a data owner and a visual parent where they differ. Editing and deletion use data ownership. Canvas edges use visual parentage. This prevents diagram requirements from corrupting JSON mutation behavior.

## Layout Compatibility

Existing layouts may contain `firewall:<site-key>`. On load, that coordinate is migrated to `firewall:<site-key>/primary`. The new `firewall-zone:<site-key>` receives an automatic default coordinate when no saved position exists.

Site-key renaming remaps the Firewall Zone, Primary Firewall VM, workload zones, workload VMs, and all corresponding saved coordinates atomically. Layout validation accepts only the new stable IDs after normalization. Stale legacy IDs are pruned during planner state normalization or save.

## Inspector and Editing Behavior

Selecting the Firewall Zone displays:

- the label `Firewall Zone`;
- its system-managed status;
- the site region;
- the reserved infrastructure subnet when an allocated subnet is available, otherwise an automatic-allocation explanation;
- an explanation that workload-zone traffic routes through firewall VMs in this zone.

The Firewall Zone cannot be renamed or deleted. It is not presented as a normal workload zone and has no team-role selector.

Selecting the Primary Firewall VM edits the existing `site.firewall` fields:

- base type;
- cloud plan;
- UST prompt.

The existing firewall specification validation remains authoritative.

## Contextual Actions

- Add Site remains globally available in editable drafts.
- Add Zone is available when a site, its Firewall Zone, its Primary Firewall VM, or one of its workload descendants establishes a site context.
- Add VM remains available only for workload zones and workload VMs; it is not available for the automatic Firewall Zone or Primary Firewall VM.
- Delete controls never appear for the automatic Firewall Zone or Primary Firewall VM.

## Canvas Rendering

The canvas renders structural edges in this order:

1. Gateway to Site.
2. Site to Firewall Zone.
3. Firewall Zone to Primary Firewall VM.
4. Firewall Zone to each workload zone.
5. Each workload zone to its workload VMs.

The Firewall Zone uses the same zone-container visual vocabulary as workload zones, with a system/infrastructure distinction rather than a blue/red team role. The Primary Firewall VM uses the VM visual vocabulary, ensuring future firewall members can be added without redesigning the hierarchy.

Drag behavior remains unchanged: nodes and connected links follow the pointer continuously, while coordinates persist on release.

## Backend and Preview Behavior

Provisioning, preview counts, cost estimation, addressing, firewall configuration, and lifecycle behavior remain unchanged. The preview continues to count one firewall VM per site. The automatic Firewall Zone is a planner representation of the already-reserved infrastructure network and does not add a VM, VPC, or billable resource.

## Testing

Executable planner-state tests will cover:

- automatic Firewall Zone and Primary Firewall VM node creation;
- workload-zone visual parents pointing to the Firewall Zone;
- data ownership remaining with the site;
- legacy firewall layout-coordinate migration;
- site-key layout remapping across the new IDs;
- contextual action eligibility.

Template/canvas contract tests will cover the new node types and visual-edge source. Existing backend tests must continue to prove unchanged provisioning counts and one firewall VM per site. The full disposable Docker suite and JavaScript syntax/state tests will run before local deployment.

## Acceptance Criteria

- Every site visibly contains an automatic Firewall Zone.
- The existing firewall is visibly a Primary Firewall VM inside that zone.
- Workload zones connect from the Firewall Zone, not the Site or firewall VM.
- The site remains the data owner of workload zones.
- Firewall Zone and Primary Firewall VM cannot be deleted.
- Existing firewall settings remain editable on the Primary Firewall VM.
- Existing layouts migrate without losing the saved firewall position.
- Provisioning behavior and resource counts do not change.
- The model can display additional firewall VM children later without changing workload-zone attachment semantics.
