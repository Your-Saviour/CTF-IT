from dataclasses import dataclass
from typing import Mapping

from .compute import AwsComputeProvider
from .tags import assert_owned, aws_tag_dict, aws_tag_list
from .types import NetworkInterfaceResult, SiteNetworkResult


@dataclass(frozen=True)
class SiteNetworkSpec:
    region: str
    availability_zone: str
    vpc_cidr: str
    subnets: Mapping[str, str]
    tags: Mapping[str, str]
    secondary_cidrs: tuple[str, ...] = ()


@dataclass(frozen=True)
class SecurityGroupSpec:
    vpc_id: str
    name: str
    description: str
    ingress: tuple[dict, ...]
    egress: tuple[dict, ...]
    tags: Mapping[str, str]


class AwsNetworkProvider:
    def __init__(self, ec2_client):
        self.ec2 = ec2_client
        self._compute = AwsComputeProvider(ec2_client)

    def _call(self, operation: str, **kwargs):
        return self._compute._call(operation, **kwargs)

    @staticmethod
    def _tag_spec(resource_type: str, tags: Mapping[str, str]) -> list[dict]:
        return [{"ResourceType": resource_type, "Tags": aws_tag_list(tags)}]

    def _ensure_vpc(self, spec: SiteNetworkSpec) -> str:
        response = self._call("describe_vpcs", Filters=[
            {"Name": "cidr-block", "Values": [spec.vpc_cidr]},
            {"Name": "tag:ManagedBy", "Values": [spec.tags["ManagedBy"]]},
            {"Name": "tag:SiteId", "Values": [spec.tags["SiteId"]]},
        ])
        if response.get("Vpcs"):
            vpc = response["Vpcs"][0]
            assert_owned(aws_tag_dict(vpc.get("Tags")), spec.tags)
            return vpc["VpcId"]
        vpc_id = self._call(
            "create_vpc", CidrBlock=spec.vpc_cidr,
            TagSpecifications=self._tag_spec("vpc", spec.tags),
        )["Vpc"]["VpcId"]
        self._call("modify_vpc_attribute", VpcId=vpc_id, EnableDnsSupport={"Value": True})
        self._call("modify_vpc_attribute", VpcId=vpc_id, EnableDnsHostnames={"Value": True})
        return vpc_id

    def _ensure_igw(self, vpc_id: str, tags: Mapping[str, str]) -> str:
        response = self._call("describe_internet_gateways", Filters=[
            {"Name": "attachment.vpc-id", "Values": [vpc_id]},
        ])
        if response.get("InternetGateways"):
            gateway = response["InternetGateways"][0]
            assert_owned(aws_tag_dict(gateway.get("Tags")), tags)
            return gateway["InternetGatewayId"]
        response = self._call("describe_internet_gateways", Filters=[
            {"Name": f"tag:{key}", "Values": [value]} for key, value in tags.items()
        ])
        gateways = response.get("InternetGateways", [])
        if len(gateways) > 1:
            raise RuntimeError("multiple internet gateways match one site identity")
        if gateways:
            gateway = gateways[0]
            assert_owned(aws_tag_dict(gateway.get("Tags")), tags)
            gateway_id = gateway["InternetGatewayId"]
            self._call("attach_internet_gateway", InternetGatewayId=gateway_id, VpcId=vpc_id)
            return gateway_id
        gateway_id = self._call(
            "create_internet_gateway",
            TagSpecifications=self._tag_spec("internet-gateway", tags),
        )["InternetGateway"]["InternetGatewayId"]
        self._call("attach_internet_gateway", InternetGatewayId=gateway_id, VpcId=vpc_id)
        return gateway_id

    def _ensure_secondary_cidr(self, vpc_id: str, cidr: str) -> None:
        response = self._call("describe_vpcs", VpcIds=[vpc_id])
        vpcs = response.get("Vpcs", [])
        associations = vpcs[0].get("CidrBlockAssociationSet", []) if vpcs else []
        if any(row.get("CidrBlock") == cidr and row.get("CidrBlockState", {}).get("State") != "failing"
               for row in associations):
            return
        self._call("associate_vpc_cidr_block", VpcId=vpc_id, CidrBlock=cidr)

    def _ensure_subnet(self, vpc_id: str, role: str, cidr: str, spec: SiteNetworkSpec) -> str:
        tags = {**spec.tags, "NetworkRole": role}
        response = self._call("describe_subnets", Filters=[
            {"Name": "vpc-id", "Values": [vpc_id]},
            {"Name": "cidr-block", "Values": [cidr]},
        ])
        if response.get("Subnets"):
            subnet = response["Subnets"][0]
            assert_owned(aws_tag_dict(subnet.get("Tags")), tags)
            return subnet["SubnetId"]
        return self._call(
            "create_subnet", VpcId=vpc_id, CidrBlock=cidr,
            AvailabilityZone=spec.availability_zone,
            TagSpecifications=self._tag_spec("subnet", tags),
        )["Subnet"]["SubnetId"]

    def _ensure_route_table(self, vpc_id: str, role: str, subnet_id: str,
                            tags: Mapping[str, str]) -> str:
        role_tags = {**tags, "NetworkRole": role}
        response = self._call("describe_route_tables", Filters=[
            {"Name": "vpc-id", "Values": [vpc_id]},
            {"Name": "tag:NetworkRole", "Values": [role]},
        ])
        if response.get("RouteTables"):
            table = response["RouteTables"][0]
            assert_owned(aws_tag_dict(table.get("Tags")), role_tags)
            return table["RouteTableId"]
        table_id = self._call(
            "create_route_table", VpcId=vpc_id,
            TagSpecifications=self._tag_spec("route-table", role_tags),
        )["RouteTable"]["RouteTableId"]
        self._call("associate_route_table", RouteTableId=table_id, SubnetId=subnet_id)
        return table_id

    def ensure_site_network(self, spec: SiteNetworkSpec) -> SiteNetworkResult:
        vpc_id = self._ensure_vpc(spec)
        for cidr in spec.secondary_cidrs:
            self._ensure_secondary_cidr(vpc_id, cidr)
        igw_id = self._ensure_igw(vpc_id, spec.tags)
        subnet_ids = {
            role: self._ensure_subnet(vpc_id, role, cidr, spec)
            for role, cidr in spec.subnets.items()
        }
        route_table_ids = {
            role: self._ensure_route_table(vpc_id, role, subnet_id, spec.tags)
            for role, subnet_id in subnet_ids.items()
        }
        self.ensure_route(route_table_ids["wan"], "0.0.0.0/0", gateway_id=igw_id)
        return SiteNetworkResult(
            vpc_id, spec.availability_zone, subnet_ids, route_table_ids, igw_id,
        )

    def ensure_route(self, route_table_id: str, destination: str, *,
                     eni_id: str | None = None, gateway_id: str | None = None) -> None:
        request = {"RouteTableId": route_table_id, "DestinationCidrBlock": destination}
        if eni_id:
            request["NetworkInterfaceId"] = eni_id
        elif gateway_id:
            request["GatewayId"] = gateway_id
        else:
            raise ValueError("A route requires an ENI or gateway target")
        response = self._call("describe_route_tables", RouteTableIds=[route_table_id])
        routes = response.get("RouteTables", [{}])[0].get("Routes", []) if response.get("RouteTables") else []
        existing = next((row for row in routes if row.get("DestinationCidrBlock") == destination), None)
        target_key = "NetworkInterfaceId" if eni_id else "GatewayId"
        target_value = eni_id or gateway_id
        if existing and existing.get(target_key) == target_value:
            return
        self._call("replace_route" if existing else "create_route", **request)

    def create_eni(self, subnet_id: str, private_ip: str | None,
                   security_group_ids: list[str], tags: Mapping[str, str]) -> NetworkInterfaceResult:
        request = {
            "SubnetId": subnet_id, "Groups": security_group_ids,
            "TagSpecifications": self._tag_spec("network-interface", tags),
        }
        if private_ip:
            request["PrivateIpAddress"] = private_ip
        eni = self._call("create_network_interface", **request)["NetworkInterface"]
        return NetworkInterfaceResult(
            eni["NetworkInterfaceId"], subnet_id, eni.get("PrivateIpAddress"), eni.get("MacAddress")
        )

    def ensure_eni(self, subnet_id: str, private_ip: str | None,
                   security_group_ids: list[str],
                   tags: Mapping[str, str]) -> NetworkInterfaceResult:
        filters = [{"Name": "subnet-id", "Values": [subnet_id]}]
        if private_ip:
            filters.append({"Name": "addresses.private-ip-address", "Values": [private_ip]})
        filters.extend(
            {"Name": f"tag:{key}", "Values": [value]} for key, value in tags.items()
        )
        interfaces = self._call(
            "describe_network_interfaces", Filters=filters,
        ).get("NetworkInterfaces", [])
        if len(interfaces) > 1:
            raise RuntimeError("multiple network interfaces match one ownership identity")
        if not interfaces:
            return self.create_eni(subnet_id, private_ip, security_group_ids, tags)
        eni = interfaces[0]
        assert_owned(aws_tag_dict(eni.get("TagSet")), tags)
        if {row["GroupId"] for row in eni.get("Groups", [])} != set(security_group_ids):
            raise RuntimeError("existing owned network interface has unexpected security groups")
        return NetworkInterfaceResult(
            eni["NetworkInterfaceId"], eni["SubnetId"], eni.get("PrivateIpAddress"),
            eni.get("MacAddress"),
        )

    @staticmethod
    def _permissions_equal(actual: list[dict], expected: tuple[dict, ...]) -> bool:
        def canonical(permission: dict) -> dict:
            return {
                key: permission[key]
                for key in ("IpProtocol", "FromPort", "ToPort", "IpRanges",
                            "Ipv6Ranges", "PrefixListIds", "UserIdGroupPairs")
                if key in permission and permission[key] not in (None, [])
            }
        return sorted((canonical(row) for row in actual), key=repr) == sorted(
            (canonical(row) for row in expected), key=repr,
        )

    def ensure_security_group(self, spec: SecurityGroupSpec) -> str:
        response = self._call("describe_security_groups", Filters=[
            {"Name": "vpc-id", "Values": [spec.vpc_id]},
            {"Name": "group-name", "Values": [spec.name]},
        ])
        if response.get("SecurityGroups"):
            group = response["SecurityGroups"][0]
            assert_owned(aws_tag_dict(group.get("Tags")), spec.tags)
        else:
            group_id = self._call(
                "create_security_group", VpcId=spec.vpc_id, GroupName=spec.name,
                Description=spec.description,
            )["GroupId"]
            self._call("create_tags", Resources=[group_id], Tags=aws_tag_list(spec.tags))
            group = {
                "GroupId": group_id, "IpPermissions": [],
                "IpPermissionsEgress": [{"IpProtocol": "-1", "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}],
            }
        group_id = group["GroupId"]
        current_ingress = group.get("IpPermissions", [])
        current_egress = group.get("IpPermissionsEgress", [])
        if not self._permissions_equal(current_ingress, spec.ingress):
            if current_ingress:
                self._call("revoke_security_group_ingress", GroupId=group_id,
                           IpPermissions=current_ingress)
            if spec.ingress:
                self._call("authorize_security_group_ingress", GroupId=group_id,
                           IpPermissions=list(spec.ingress))
        if not self._permissions_equal(current_egress, spec.egress):
            if current_egress:
                self._call("revoke_security_group_egress", GroupId=group_id,
                           IpPermissions=current_egress)
            if spec.egress:
                self._call("authorize_security_group_egress", GroupId=group_id,
                           IpPermissions=list(spec.egress))
        return group_id

    def security_group_rules(self, group_id: str) -> tuple[dict, ...]:
        response = self._call("describe_security_groups", GroupIds=[group_id])
        groups = response.get("SecurityGroups", [])
        if not groups:
            return ()
        return tuple(groups[0].get("IpPermissions", []))

    def delete_owned_vpc(self, vpc_id: str, expected_tags: Mapping[str, str]) -> None:
        response = self._call("describe_vpcs", VpcIds=[vpc_id])
        vpc = response["Vpcs"][0]
        assert_owned(aws_tag_dict(vpc.get("Tags")), expected_tags)
        self._call("delete_vpc", VpcId=vpc_id)

    def delete_owned_eni(self, eni_id: str, expected_tags: Mapping[str, str]) -> None:
        response = self._call("describe_network_interfaces", NetworkInterfaceIds=[eni_id])
        eni = response["NetworkInterfaces"][0]
        assert_owned(aws_tag_dict(eni.get("TagSet")), expected_tags)
        self._call("delete_network_interface", NetworkInterfaceId=eni_id)

    def delete_owned_security_group(self, group_id: str,
                                    expected_tags: Mapping[str, str]) -> None:
        response = self._call("describe_security_groups", GroupIds=[group_id])
        group = response["SecurityGroups"][0]
        assert_owned(aws_tag_dict(group.get("Tags")), expected_tags)
        self._call("delete_security_group", GroupId=group_id)

    def delete_owned_site(self, site, expected_tags: Mapping[str, str]) -> None:
        for group_id in [site.wan_security_group_id, site.lan_security_group_id,
                         *[zone.security_group_id for zone in site.zones]]:
            if not group_id:
                continue
            group = self._call("describe_security_groups", GroupIds=[group_id])["SecurityGroups"][0]
            assert_owned(aws_tag_dict(group.get("Tags")), expected_tags)
            self._call("delete_security_group", GroupId=group_id)
        route_ids = list(__import__("json").loads(site.route_table_ids_json or "{}").values())
        for route_id in route_ids:
            table = self._call("describe_route_tables", RouteTableIds=[route_id])["RouteTables"][0]
            assert_owned(aws_tag_dict(table.get("Tags")), expected_tags)
            for association in table.get("Associations", []):
                if not association.get("Main") and association.get("RouteTableAssociationId"):
                    self._call("disassociate_route_table",
                               AssociationId=association["RouteTableAssociationId"])
            self._call("delete_route_table", RouteTableId=route_id)
        for subnet_id in [site.public_subnet_id, site.infrastructure_subnet_id,
                          *[zone.subnet_id for zone in site.zones]]:
            if not subnet_id:
                continue
            subnet = self._call("describe_subnets", SubnetIds=[subnet_id])["Subnets"][0]
            assert_owned(aws_tag_dict(subnet.get("Tags")), expected_tags)
            self._call("delete_subnet", SubnetId=subnet_id)
        if site.internet_gateway_id:
            gateway = self._call(
                "describe_internet_gateways", InternetGatewayIds=[site.internet_gateway_id],
            )["InternetGateways"][0]
            assert_owned(aws_tag_dict(gateway.get("Tags")), expected_tags)
            self._call("detach_internet_gateway", InternetGatewayId=site.internet_gateway_id,
                       VpcId=site.vpc_id)
            self._call("delete_internet_gateway", InternetGatewayId=site.internet_gateway_id)
        response = self._call("describe_vpcs", VpcIds=[site.vpc_id])
        vpc = response["Vpcs"][0]
        assert_owned(aws_tag_dict(vpc.get("Tags")), expected_tags)
        for association in vpc.get("CidrBlockAssociationSet", []):
            cidr = association.get("CidrBlock")
            association_id = association.get("AssociationId")
            if cidr != site.allocated_cidr and association_id:
                self._call("disassociate_vpc_cidr_block", AssociationId=association_id)
        self._call("delete_vpc", VpcId=site.vpc_id)
