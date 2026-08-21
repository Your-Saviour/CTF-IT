# Historical provider private-boot issue

This note applies only to infrastructure created before the AWS hard cutover. It is retained for incident history and does not describe a supported runtime path.

On 2026-08-15, a stock Ubuntu private-only canary on the former provider reported an active private attachment but never brought its guest interface online. OPNsense emitted unanswered ARP requests, SSH was unreachable, and the certification gate correctly prevented workload creation. The affected diagnostic resources were retained for operator review.

AWS GameNet uses explicit EC2 ENIs, persisted MAC addresses, isolated subnets, route tables targeting the OPNsense LAN ENI, and opt-in live acceptance canaries. No current operation manages the historical resources described here.
