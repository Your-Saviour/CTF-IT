import pytest

from api.services.aws import AwsOwnershipError
from api.services.aws.network import AwsNetworkProvider, SecurityGroupSpec, SiteNetworkSpec


class FakeEc2:
    def __init__(self):
        self.calls = []
        self.vpcs = []

    def describe_vpcs(self, **kwargs):
        self.calls.append(("describe_vpcs", kwargs))
        return {"Vpcs": self.vpcs}

    def create_vpc(self, **kwargs):
        self.calls.append(("create_vpc", kwargs))
        return {"Vpc": {"VpcId": "vpc-123"}}

    def modify_vpc_attribute(self, **kwargs):
        self.calls.append(("modify_vpc_attribute", kwargs)); return {}

    def associate_vpc_cidr_block(self, **kwargs):
        self.calls.append(("associate_vpc_cidr_block", kwargs))
        return {"CidrBlockAssociation": {"AssociationId": "vpc-cidr-assoc-123"}}

    def describe_security_groups(self, **kwargs):
        self.calls.append(("describe_security_groups", kwargs)); return {"SecurityGroups": []}

    def create_security_group(self, **kwargs):
        self.calls.append(("create_security_group", kwargs)); return {"GroupId": "sg-123"}

    def create_tags(self, **kwargs):
        self.calls.append(("create_tags", kwargs)); return {}

    def revoke_security_group_egress(self, **kwargs):
        self.calls.append(("revoke_security_group_egress", kwargs)); return {}

    def authorize_security_group_ingress(self, **kwargs):
        self.calls.append(("authorize_security_group_ingress", kwargs)); return {}

    def authorize_security_group_egress(self, **kwargs):
        self.calls.append(("authorize_security_group_egress", kwargs)); return {}

    def describe_internet_gateways(self, **kwargs):
        self.calls.append(("describe_internet_gateways", kwargs)); return {"InternetGateways": []}

    def create_internet_gateway(self, **kwargs):
        self.calls.append(("create_internet_gateway", kwargs)); return {"InternetGateway": {"InternetGatewayId": "igw-123"}}

    def attach_internet_gateway(self, **kwargs):
        self.calls.append(("attach_internet_gateway", kwargs)); return {}

    def describe_subnets(self, **kwargs):
        self.calls.append(("describe_subnets", kwargs)); return {"Subnets": []}

    def create_subnet(self, **kwargs):
        self.calls.append(("create_subnet", kwargs))
        cidr = kwargs["CidrBlock"]
        return {"Subnet": {"SubnetId": "subnet-" + cidr.split(".")[2]}}

    def describe_route_tables(self, **kwargs):
        self.calls.append(("describe_route_tables", kwargs)); return {"RouteTables": []}

    def create_route_table(self, **kwargs):
        self.calls.append(("create_route_table", kwargs))
        return {"RouteTable": {"RouteTableId": f"rtb-{len([c for c in self.calls if c[0] == 'create_route_table'])}"}}

    def associate_route_table(self, **kwargs):
        self.calls.append(("associate_route_table", kwargs)); return {"AssociationId": "rtbassoc-1"}

    def create_route(self, **kwargs):
        self.calls.append(("create_route", kwargs)); return {"Return": True}

    def delete_vpc(self, **kwargs):
        self.calls.append(("delete_vpc", kwargs)); return {}


def site_spec():
    return SiteNetworkSpec(
        region="ap-southeast-2", availability_zone="ap-southeast-2a",
        vpc_cidr="10.40.0.0/20", secondary_cidrs=("172.31.255.0/28",),
        subnets={"wan": "172.31.255.0/28", "infra": "10.40.0.0/24", "blue": "10.40.1.0/24"},
        tags={"ManagedBy": "ctf-it", "SiteId": "12"},
    )


def test_site_network_creates_subnets_in_one_az_and_public_default_route():
    ec2 = FakeEc2()
    result = AwsNetworkProvider(ec2).ensure_site_network(site_spec())
    assert result.vpc_id == "vpc-123"
    assert result.availability_zone == "ap-southeast-2a"
    assert set(result.subnet_ids) == {"wan", "infra", "blue"}
    subnet_calls = [payload for name, payload in ec2.calls if name == "create_subnet"]
    assert {call["AvailabilityZone"] for call in subnet_calls} == {"ap-southeast-2a"}
    assert any(name == "create_route" and payload.get("GatewayId") == "igw-123" for name, payload in ec2.calls)
    assert any(
        name == "associate_vpc_cidr_block" and payload["CidrBlock"] == "172.31.255.0/28"
        for name, payload in ec2.calls
    )


def test_existing_vpc_must_have_expected_ownership_tags():
    ec2 = FakeEc2()
    ec2.vpcs = [{"VpcId": "vpc-foreign", "Tags": [{"Key": "ManagedBy", "Value": "other"}]}]
    with pytest.raises(AwsOwnershipError):
        AwsNetworkProvider(ec2).ensure_site_network(site_spec())


def test_cleanup_refuses_vpc_without_expected_site_tag():
    ec2 = FakeEc2()
    ec2.vpcs = [{"VpcId": "vpc-foreign", "Tags": [{"Key": "ManagedBy", "Value": "ctf-it"}]}]
    with pytest.raises(AwsOwnershipError):
        AwsNetworkProvider(ec2).delete_owned_vpc("vpc-foreign", site_spec().tags)
    assert not any(name == "delete_vpc" for name, _ in ec2.calls)


def test_security_group_creation_replaces_default_egress_with_exact_rules():
    ec2 = FakeEc2()
    ingress = ({"IpProtocol": "udp", "FromPort": 51820, "ToPort": 51820,
                "IpRanges": [{"CidrIp": "0.0.0.0/0"}]},)
    egress = ({"IpProtocol": "-1", "IpRanges": [{"CidrIp": "10.128.0.0/20"}]},)
    group_id = AwsNetworkProvider(ec2).ensure_security_group(SecurityGroupSpec(
        vpc_id="vpc-123", name="ctf-site-wan", description="GameNet WAN",
        ingress=ingress, egress=egress,
        tags={"ManagedBy": "ctf-it", "SiteId": "12"},
    ))
    assert group_id == "sg-123"
    assert any(name == "revoke_security_group_egress" for name, _ in ec2.calls)
    assert ("authorize_security_group_ingress", {
        "GroupId": "sg-123", "IpPermissions": list(ingress),
    }) in ec2.calls
    assert ("authorize_security_group_egress", {
        "GroupId": "sg-123", "IpPermissions": list(egress),
    }) in ec2.calls


def test_route_reconciliation_replaces_wrong_existing_target():
    ec2 = FakeEc2()
    def describe(**kwargs):
        ec2.calls.append(("describe_route_tables", kwargs))
        return {"RouteTables": [{"RouteTableId": "rtb-zone", "Routes": [{
            "DestinationCidrBlock": "0.0.0.0/0", "GatewayId": "igw-wrong",
        }]}]}
    ec2.describe_route_tables = describe
    ec2.replace_route = lambda **kwargs: ec2.calls.append(("replace_route", kwargs)) or {}
    AwsNetworkProvider(ec2).ensure_route("rtb-zone", "0.0.0.0/0", eni_id="eni-firewall-lan")
    assert ("replace_route", {
        "RouteTableId": "rtb-zone", "DestinationCidrBlock": "0.0.0.0/0",
        "NetworkInterfaceId": "eni-firewall-lan",
    }) in ec2.calls
