# Production Deployment Smoke Test

This is the live release gate for the VM-based platform. It creates infrastructure and must be run only on an isolated Linux host with a disposable domain and cloud account.

Do not run this procedure as part of ordinary unit tests.

## Preconditions

- Automated Docker test target passes.
- `bash -n quickstart.sh` passes.
- Production Compose renders successfully from example configuration.
- DNS credentials and Vultr credentials are scoped to disposable resources.
- `CTF_CONTROL_PLANE_CIDR` is the disposable control host's IPv4 CIDR and the
  host has at least 3 GB of free temporary image storage.
- The operator has recorded the cloud project, domain, region, and expected maximum spend.

## Deployment

1. Clone a clean checkout.
2. Set `DOMAIN`, `ACME_EMAIL`, `SERVER_IP`, and optional cloud variables.
3. Run `./quickstart.sh --non-interactive`.
4. Confirm `.env`, `deploy/.env`, `deploy/caldera/config/local.yml`, and `deploy/caldera/config/ssh_host_key` are mode `0600` and contain no placeholder values.
5. Create the documented DNS A records.
6. Wait for Traefik, API, Caldera, Semaphore, Semaphore Postgres, and Dockhand to become healthy.

## Authentication and exposure

- Register the initial owner before enabling participant access.
- Disable admin bootstrap after the owner exists.
- Confirm TLS for every routed hostname.
- Confirm the Traefik and Dockhand routes require authentication.
- Confirm the API has access only to the restricted Docker socket proxy.
- Confirm the host Docker socket is not mounted into Internet-facing application containers.

## Event exercise

Before creating the event, build the intended OPNsense release under **Admin →
Settings → OPNsense images**. Complete the console installation with
WAN=`vtnet0` and LAN=`vtnet1`, load the generated generic configuration, mark
the installer complete, and activate the resulting validated snapshot.

Record the total build duration. Then create one GameNet event with at least
one team and its site endpoints.

Verify:

1. The firewall and team VPC are created first.
2. The target joins the team VPC with the expected private address.
3. The attacker is created without target modules.
4. Semaphore applies the base and selected module playbooks.
5. Caldera receives the target agent and can run the exported adversary.
6. Provisioning status and errors are visible in the admin UI.
7. `deface_website`, `install_c2`, and `exfil_shadow` can each transition from pending to achieved and then defended.
8. Every site firewall records the activated OPNsense release and snapshot ID,
   has a distinct SSH host key, and reaches active state without invoking the
   legacy `opnsense-bootstrap` conversion.

For the release acceptance run, provision at least five firewalls from the
same snapshot. Record ready time for each deployment; target median under
10 minutes and p95 under 15 minutes. If snapshot restoration alone exceeds the
target, retain the validated image workflow and track a warm pool separately.

## Teardown

- Destroy all created VMs through the platform.
- Confirm Cloudflare DNS records are removed.
- Confirm the team VPC is removed after its final VM.
- Retire the disposable OPNsense image with confirmed artifact deletion only
  after all firewall records that reference it have been removed.
- Confirm no Semaphore task remains running.
- Record any resource that required manual cleanup.

## Idempotency

Run `./quickstart.sh --non-interactive` a second time with the same inputs. It must preserve secrets, retain configuration permissions, report the correct service hostnames, and leave healthy services running.
