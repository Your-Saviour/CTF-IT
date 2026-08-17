# OPNsense Acceptance AMI Cache Design

## Goal

Stop rebuilding and redownloading an unchanged OPNsense image for every AWS
acceptance test run. Retain one validated acceptance AMI and its snapshots for
reuse by GameNet tests while preserving a separate clean-slate image-builder
test and exact, ownership-safe cleanup.

## Cache identity

The cache key is a SHA-256 digest of the inputs that affect the image:

- AWS region and architecture;
- requested OPNsense release;
- downloaded bootstrap source digest;
- golden configuration schema version; and
- an explicit image-build revision changed whenever conversion, sanitisation,
  validation, or bootstrap adaptation behavior changes.

The AMI and every backing snapshot carry the existing application, manager and
environment ownership tags plus `ArtifactRole=opnsense-acceptance-cache`, the
cache key, creation time and expiry time. Discovery accepts exactly one
available, fully owned AMI whose key matches and whose expiry is in the future.
Ambiguous, expired, unowned or incomplete artifacts are never reused.

## Test flow

The clean-slate OPNsense builder test always exercises the complete FreeBSD to
OPNsense conversion, clone validation and activation path. After successful
validation it promotes the resulting AMI into the retained acceptance cache
instead of deleting it.

GameNet acceptance first discovers the matching cache. When present, it creates
the database image record and validation evidence needed by the normal
provisioning path without rebuilding the AMI. When absent, it performs the same
validated build once and promotes the result before continuing.

Per-run teardown continues deleting all temporary instances, EIPs, ENIs, key
pairs, security groups, subnets, route tables, gateways and VPCs. Cached AMIs
and snapshots are excluded only when all cache ownership tags match.

## Cleanup and expiry

A containerized cleanup command lists cached artifacts for the approved AWS
account and acceptance environment. By default it deregisters expired cached
AMIs and deletes only their owned backing snapshots. An explicit `--all` mode
removes every owned acceptance cache artifact. Cleanup refuses account
mismatches, missing ownership tags, shared snapshots and ambiguous resources.

The default retention period is seven days. Successful promotion of a new key
does not immediately delete a different key; the expiry cleanup handles old
artifacts so concurrent or recently started test runs remain safe.

## Failure handling

A failed build is never cached. A cache hit whose AMI disappears or becomes
unavailable is treated as a miss. If reuse fails during launch, the test reports
the failing AMI and key; it does not silently rebuild in the same run. This
keeps failures attributable and avoids unexpected additional AWS spend.

## Verification

Unit tests cover deterministic key generation, discovery, expiry, ownership
rejection, promotion, per-run cleanup exclusion and explicit cache cleanup.
AWS acceptance verifies both paths:

1. a clean-slate build produces a reusable cache artifact; and
2. a fresh GameNet run reuses that exact AMI without creating an image builder.

The final inventory must contain no run-owned temporary resources. It may
contain only the explicitly tagged cached AMI and its owned snapshots until the
containerized cache cleanup command removes them.
