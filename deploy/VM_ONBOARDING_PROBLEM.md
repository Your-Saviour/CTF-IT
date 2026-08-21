# VM onboarding and AWS provisioning

Existing machines can be registered with hostname, address, SSH user, and team. Managed machines are created through the AWS cloud endpoint and configured by Semaphore after SSH becomes reachable.

GameNet event start requires an active validated OPNsense AMI and a passing AWS readiness report. Failures leave the event closed and retain persisted AWS IDs for reconciliation or owned cleanup.

Production relies on the API workload IAM role, AWS service availability, quota headroom, Semaphore, and optionally Cloudflare. Operators should validate the role policy, approved AMIs, subnet address capacity, Elastic IPs, VPCs, ENIs, and vCPU quota before scheduled events.
