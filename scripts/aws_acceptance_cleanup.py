#!/usr/bin/env python3
"""Remove resources belonging to one explicitly approved AWS acceptance run."""

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
    def expected_tags(self):
        return {
            "ManagedBy": "ctf-it",
            "Environment": "acceptance",
            "AcceptanceRunId": self.run_id,
        }

    @property
    def filters(self):
        return [
            {"Name": f"tag:{key}", "Values": [value]}
            for key, value in self.expected_tags.items()
        ]


def _tag_dict(tags):
    return {row["Key"]: row["Value"] for row in tags or []}


def _assert_owned(resource_type, resource_id, tags, context):
    actual = _tag_dict(tags)
    if any(actual.get(key) != value for key, value in context.expected_tags.items()):
        raise RuntimeError(
            f"refusing cleanup of {resource_type} {resource_id}: acceptance ownership tags do not match"
        )


def _rows(response, key):
    return response.get(key, [])


def inventory(ec2, context: CleanupContext):
    instances = [
        item["InstanceId"]
        for reservation in ec2.describe_instances(Filters=context.filters + [{
            "Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"],
        }]).get("Reservations", [])
        for item in reservation.get("Instances", [])
    ]
    return {
        "instances": instances,
        "addresses": [row["AllocationId"] for row in _rows(
            ec2.describe_addresses(Filters=context.filters), "Addresses")],
        "images": [row["ImageId"] for row in _rows(
            ec2.describe_images(Owners=["self"], Filters=context.filters), "Images")],
        "snapshots": [row["SnapshotId"] for row in _rows(
            ec2.describe_snapshots(OwnerIds=["self"], Filters=context.filters), "Snapshots")],
        "network_interfaces": [row["NetworkInterfaceId"] for row in _rows(
            ec2.describe_network_interfaces(Filters=context.filters), "NetworkInterfaces")],
        "security_groups": [row["GroupId"] for row in _rows(
            ec2.describe_security_groups(Filters=context.filters), "SecurityGroups")],
        "route_tables": [row["RouteTableId"] for row in _rows(
            ec2.describe_route_tables(Filters=context.filters), "RouteTables")],
        "subnets": [row["SubnetId"] for row in _rows(
            ec2.describe_subnets(Filters=context.filters), "Subnets")],
        "internet_gateways": [row["InternetGatewayId"] for row in _rows(
            ec2.describe_internet_gateways(Filters=context.filters), "InternetGateways")],
        "key_pairs": [row["KeyPairId"] for row in _rows(
            ec2.describe_key_pairs(Filters=context.filters), "KeyPairs")],
        "vpcs": [row["VpcId"] for row in _rows(
            ec2.describe_vpcs(Filters=context.filters), "Vpcs")],
    }


def cleanup_owned(ec2, context: CleanupContext):
    """Delete only resources returned by the exact acceptance ownership filter."""
    removed = {key: [] for key in (
        "instances", "addresses", "images", "snapshots", "network_interfaces",
        "security_groups", "route_tables", "subnets", "internet_gateways",
        "key_pairs", "vpcs",
    )}

    instances = [
        item for reservation in ec2.describe_instances(Filters=context.filters + [{
            "Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"],
        }]).get("Reservations", []) for item in reservation.get("Instances", [])
    ]
    for row in instances:
        _assert_owned("instance", row["InstanceId"], row.get("Tags"), context)
    if instances:
        ids = [row["InstanceId"] for row in instances]
        ec2.terminate_instances(InstanceIds=ids)
        ec2.get_waiter("instance_terminated").wait(InstanceIds=ids)
        removed["instances"].extend(ids)

    addresses = _rows(ec2.describe_addresses(Filters=context.filters), "Addresses")
    for row in addresses:
        allocation_id = row["AllocationId"]
        _assert_owned("elastic IP", allocation_id, row.get("Tags"), context)
        if row.get("AssociationId"):
            ec2.disassociate_address(AssociationId=row["AssociationId"])
        ec2.release_address(AllocationId=allocation_id)
        removed["addresses"].append(allocation_id)

    images = _rows(ec2.describe_images(Owners=["self"], Filters=context.filters), "Images")
    image_snapshot_ids = set()
    for row in images:
        image_id = row["ImageId"]
        _assert_owned("AMI", image_id, row.get("Tags"), context)
        image_snapshot_ids.update(
            mapping["Ebs"]["SnapshotId"] for mapping in row.get("BlockDeviceMappings", [])
            if mapping.get("Ebs", {}).get("SnapshotId")
        )
        ec2.deregister_image(ImageId=image_id)
        removed["images"].append(image_id)

    snapshots = _rows(ec2.describe_snapshots(
        OwnerIds=["self"], Filters=context.filters,
    ), "Snapshots")
    for row in snapshots:
        snapshot_id = row["SnapshotId"]
        _assert_owned("snapshot", snapshot_id, row.get("Tags"), context)
        ec2.delete_snapshot(SnapshotId=snapshot_id)
        removed["snapshots"].append(snapshot_id)
        image_snapshot_ids.discard(snapshot_id)
    if image_snapshot_ids:
        raise RuntimeError(
            "refusing cleanup: AMI references snapshots absent from the owned acceptance inventory: "
            + ", ".join(sorted(image_snapshot_ids))
        )

    enis = _rows(ec2.describe_network_interfaces(Filters=context.filters), "NetworkInterfaces")
    for row in enis:
        eni_id = row["NetworkInterfaceId"]
        _assert_owned("network interface", eni_id, row.get("TagSet"), context)
        ec2.delete_network_interface(NetworkInterfaceId=eni_id)
        removed["network_interfaces"].append(eni_id)

    groups = _rows(ec2.describe_security_groups(Filters=context.filters), "SecurityGroups")
    for row in groups:
        group_id = row["GroupId"]
        _assert_owned("security group", group_id, row.get("Tags"), context)
        ec2.delete_security_group(GroupId=group_id)
        removed["security_groups"].append(group_id)

    route_tables = _rows(ec2.describe_route_tables(Filters=context.filters), "RouteTables")
    for row in route_tables:
        route_id = row["RouteTableId"]
        _assert_owned("route table", route_id, row.get("Tags"), context)
        for association in row.get("Associations", []):
            association_id = association.get("RouteTableAssociationId")
            if association_id and not association.get("Main"):
                ec2.disassociate_route_table(AssociationId=association_id)
        ec2.delete_route_table(RouteTableId=route_id)
        removed["route_tables"].append(route_id)

    subnets = _rows(ec2.describe_subnets(Filters=context.filters), "Subnets")
    for row in subnets:
        subnet_id = row["SubnetId"]
        _assert_owned("subnet", subnet_id, row.get("Tags"), context)
        ec2.delete_subnet(SubnetId=subnet_id)
        removed["subnets"].append(subnet_id)

    gateways = _rows(ec2.describe_internet_gateways(Filters=context.filters), "InternetGateways")
    for row in gateways:
        gateway_id = row["InternetGatewayId"]
        _assert_owned("internet gateway", gateway_id, row.get("Tags"), context)
        for attachment in row.get("Attachments", []):
            if attachment.get("VpcId"):
                ec2.detach_internet_gateway(
                    InternetGatewayId=gateway_id, VpcId=attachment["VpcId"],
                )
        ec2.delete_internet_gateway(InternetGatewayId=gateway_id)
        removed["internet_gateways"].append(gateway_id)

    key_pairs = _rows(ec2.describe_key_pairs(Filters=context.filters), "KeyPairs")
    for row in key_pairs:
        key_id = row["KeyPairId"]
        _assert_owned("key pair", key_id, row.get("Tags"), context)
        ec2.delete_key_pair(KeyPairId=key_id)
        removed["key_pairs"].append(key_id)

    vpcs = _rows(ec2.describe_vpcs(Filters=context.filters), "Vpcs")
    for row in vpcs:
        vpc_id = row["VpcId"]
        _assert_owned("VPC", vpc_id, row.get("Tags"), context)
        for association in row.get("CidrBlockAssociationSet", []):
            association_id = association.get("AssociationId")
            if association_id and association.get("CidrBlock") != row.get("CidrBlock"):
                ec2.disassociate_vpc_cidr_block(AssociationId=association_id)
        ec2.delete_vpc(VpcId=vpc_id)
        removed["vpcs"].append(vpc_id)
    return removed


def main():
    parser = argparse.ArgumentParser(description="Clean one owned AWS acceptance run")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--expected-account-id", required=True)
    parser.add_argument("--inventory-only", action="store_true")
    args = parser.parse_args()
    context = CleanupContext(args.run_id, args.expected_account_id)
    from api.services.aws import AwsConfig, AwsSessionFactory
    sessions = AwsSessionFactory(AwsConfig.from_env())
    identity = sessions.caller_identity()
    if identity.account_id != context.expected_account_id:
        raise SystemExit(f"refusing account {identity.account_id}; expected {context.expected_account_id}")
    ec2 = sessions.client("ec2")
    if not args.inventory_only:
        print({"removed": cleanup_owned(ec2, context)})
    remaining = inventory(ec2, context)
    print({"remaining": remaining})
    raise SystemExit(1 if any(remaining.values()) else 0)


if __name__ == "__main__":
    main()
