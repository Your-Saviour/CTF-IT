#!/usr/bin/env python3
import argparse
from dataclasses import dataclass


@dataclass(frozen=True)
class CleanupContext:
    run_id: str
    expected_account_id: str

    def __post_init__(self):
        if not self.run_id or len(self.run_id) < 8:
            raise ValueError("a unique acceptance run ID of at least eight characters is required")
        if not self.expected_account_id or not self.expected_account_id.isdigit():
            raise ValueError("the expected AWS account ID is required")

    @property
    def filters(self):
        return [
            {"Name": "tag:ManagedBy", "Values": ["ctf-it"]},
            {"Name": "tag:Environment", "Values": ["acceptance"]},
            {"Name": "tag:AcceptanceRunId", "Values": [self.run_id]},
        ]


def inventory(ec2, context: CleanupContext):
    instances = [item["InstanceId"] for reservation in ec2.describe_instances(
        Filters=context.filters + [{"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"]}],
    ).get("Reservations", []) for item in reservation.get("Instances", [])]
    return {
        "instances": instances,
        "vpcs": [row["VpcId"] for row in ec2.describe_vpcs(Filters=context.filters).get("Vpcs", [])],
        "images": [row["ImageId"] for row in ec2.describe_images(
            Owners=["self"], Filters=context.filters,
        ).get("Images", [])],
        "snapshots": [row["SnapshotId"] for row in ec2.describe_snapshots(
            OwnerIds=["self"], Filters=context.filters,
        ).get("Snapshots", [])],
        "addresses": [row["AllocationId"] for row in ec2.describe_addresses(
            Filters=context.filters,
        ).get("Addresses", [])],
    }


def main():
    parser = argparse.ArgumentParser(description="Inventory one owned AWS acceptance run")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--expected-account-id", required=True)
    args = parser.parse_args()
    context = CleanupContext(args.run_id, args.expected_account_id)
    from api.services.aws import AwsConfig, AwsSessionFactory
    sessions = AwsSessionFactory(AwsConfig.from_env())
    identity = sessions.caller_identity()
    if identity.account_id != context.expected_account_id:
        raise SystemExit(f"refusing account {identity.account_id}; expected {context.expected_account_id}")
    remaining = inventory(sessions.client("ec2"), context)
    print(remaining)
    raise SystemExit(1 if any(remaining.values()) else 0)


if __name__ == "__main__":
    main()
