# VM Onboarding

This file previously described VM onboarding as an unimplemented problem. The workflow is now implemented through the admin UI and API; this document records the current operational path.

## Platform SSH key

Open the admin VM controls and obtain the platform public key from `GET /admin/platform/ssh-key`. Add it to the target account's `authorized_keys` before testing or provisioning a manually registered VM.

The private key is generated once and stored in platform settings. It is used by connection tests and remote goal verification. Do not expose it through an API response or copy it to the frontend.

## Existing VM

1. Create the team and register the VM with its address, SSH port, and SSH user.
2. Install the displayed platform public key on the target.
3. Use **Test connection** on the VM detail page.
4. Assign modules from the event quota or add modules manually.
5. Select **Provision**. The API stages an Ansible export, submits it to Semaphore, and records task state on the VM.
6. Deploy the Caldera agent and confirm its check-in status from the VM detail page.

Provisioning state is available from `GET /admin/vms/{id}/provision-status` and failures are stored in `VM.provision_error`.

## Vultr event provisioning

When `VULTR_API_KEY` and GameNet infrastructure are configured, starting the event creates VMs for each team. A validated OPNsense snapshot must first be built and activated under **Admin → Settings → OPNsense images**. New firewall provisioning fails before allocating event resources when no active image exists.

- `site_firewall`: restores the active OPNsense snapshot, attaches the site VPC, and applies unique site configuration;
- `target`: attaches to the team VPC, assigns modules, and provisions through Semaphore;
- `attacker`: creates a bare VM without module deployment.

The admin event page polls the aggregate provision-status endpoint until every VM is active or failed.

## Operational limitations

- The first explicit connection test enrolls the VM's SSH host key. Later key changes are rejected and require an administrator to investigate before re-enrollment.
- External provisioning depends on Vultr, Semaphore, and optionally Cloudflare availability.
- The one-time OPNsense builder requires a valid `CTF_CONTROL_PLANE_CIDR`, at
  least 3 GB of temporary storage, and an interactive Vultr console install.
- Live provisioning and teardown require the manual release smoke test in `deploy/testing_plan.md`.
