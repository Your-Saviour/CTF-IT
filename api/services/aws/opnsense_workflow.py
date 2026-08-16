import json
from dataclasses import replace

from api.services.ssh_keys import get_or_create_platform_keypair

from .compute import AwsComputeProvider, InstanceSpec, NetworkInterfaceSpec
from .config import AwsConfig
from .images import AwsImageProvider
from .network import AwsNetworkProvider, SecurityGroupSpec, SiteNetworkSpec
from .session import AwsSessionFactory
from .tags import ownership_tags
from .tags import assert_owned, aws_tag_dict
from .types import ImageResult


class AwsOpnsenseWorkflow:
    """Build and validate an OPNsense AMI inside a dedicated single-AZ VPC."""

    def __init__(self, config, ec2):
        self.config = config
        self.compute = AwsComputeProvider(ec2)
        self.network = AwsNetworkProvider(ec2)
        self.images = AwsImageProvider(ec2)
        self.ec2 = ec2

    @classmethod
    def from_env(cls):
        config = AwsConfig.from_env()
        return cls(config, AwsSessionFactory(config).client("ec2"))

    def preflight(self, base_os):
        region = self.config.default_region
        ami_id = self.config.freebsd_ami(region)
        images = self.ec2.describe_images(ImageIds=[ami_id]).get("Images", [])
        if not images or images[0].get("State") != "available":
            raise RuntimeError(f"approved FreeBSD AMI {ami_id} is unavailable in {region}")
        return {"region": region, "availability_zone": self.config.availability_zone(region)}

    def _launch(self, image, role, ami_id, interfaces, tags, key_name):
        result = self.compute.launch_instance(InstanceSpec(
            ami_id=ami_id, instance_type="t3.medium",
            client_token=self.config.resource_token("opnsense", image.id, role),
            network_interfaces=tuple(interfaces), tags={**tags, "ImageRole": role},
            key_name=key_name,
        ))
        if result.state in {"stopping", "stopped"} and role == "builder":
            if result.state == "stopping":
                self.compute.wait_stopped(result.instance_id)
            result = replace(result, state="stopped")
        else:
            if result.state == "stopped":
                self.compute.start(result.instance_id)
            self.compute.wait_running(result.instance_id)
        allocation = self.compute.ensure_eip({**tags, "ImageRole": role})
        self.compute.associate_eip(allocation.allocation_id, result.primary_eni_id)
        return replace(result, public_ip=allocation.public_ip,
                       eip_allocation_id=allocation.allocation_id)

    def build(self, db, image, bootstrap_downloader):
        from api.services import opnsense_images as workflow

        cidr = workflow.validate_control_plane_cidr()
        tags = ownership_tags(self.config.environment, site_id=image.id)
        network = self.network.ensure_site_network(SiteNetworkSpec(
            region=image.region, availability_zone=image.availability_zone,
            vpc_cidr="172.31.252.0/22",
            subnets={"wan": "172.31.252.0/24", "validation": "172.31.254.0/24"},
            tags=tags,
        ))
        ssh_permission = ({
            "IpProtocol": "tcp", "FromPort": 22, "ToPort": 22,
            "IpRanges": [{"CidrIp": cidr}],
        },)
        egress = ({"IpProtocol": "-1", "IpRanges": [{"CidrIp": "0.0.0.0/0"}]},)
        wan_sg = self.network.ensure_security_group(SecurityGroupSpec(
            network.vpc_id, f"ctf-it-opnsense-{image.id}-wan", "OPNsense AMI builder WAN",
            ssh_permission, egress, {**tags, "NetworkRole": "wan"},
        ))
        validation_sg = self.network.ensure_security_group(SecurityGroupSpec(
            network.vpc_id, f"ctf-it-opnsense-{image.id}-validation", "OPNsense AMI validation LAN",
            ({"IpProtocol": "-1", "IpRanges": [{"CidrIp": "172.31.252.0/22"}]},),
            egress, {**tags, "NetworkRole": "validation"},
        ))
        _, public_key = get_or_create_platform_keypair(db)
        key_name = self.config.resource_token("opnsense", image.id, "key")
        self.compute.ensure_key_pair(key_name, public_key, tags)
        builder = self._launch(
            image, "builder", self.config.freebsd_ami(image.region),
            [NetworkInterfaceSpec(0, subnet_id=network.subnet_ids["wan"],
                                  security_group_ids=(wan_sg,), associate_public_ip=False)],
            tags, key_name,
        )
        image.builder_instance_id = builder.instance_id
        image.builder_vpc_id = network.vpc_id
        image.builder_subnet_id = network.subnet_ids["wan"]
        image.validation_subnet_id = network.subnet_ids["validation"]
        image.phase = image.status = "bootstrapping"
        db.commit()

        evidence = json.loads(image.validation_results or "{}")
        builder_key = evidence.get("builder", {}).get("ssh_host_key")
        if image.ami_id:
            artifact = ImageResult(
                image.ami_id, tuple(json.loads(image.backing_snapshot_ids_json or "[]")),
                "available",
            )
        else:
            if builder.state != "stopped":
                guest_state = workflow._guest_state(db, builder.public_ip)
                if guest_state == "freebsd":
                    workflow._verify_freebsd_base(db, builder.public_ip)
                    script, digest = bootstrap_downloader(image.bootstrap_source_url)
                    image.bootstrap_sha256 = digest; db.commit()
                    workflow._upload_atomic(
                        db, builder.public_ip, "/conf/config.xml",
                        workflow.render_golden_config(db, image, cidr).encode(),
                    )
                    workflow._upload_atomic(
                        db, builder.public_ip, "/root/opnsense-bootstrap.sh", script, 0o700,
                    )
                    code, _, error = workflow._ssh(
                        db, builder.public_ip,
                        workflow.bootstrap_launch_command(image.version),
                        timeout=30,
                    )
                    if code:
                        raise RuntimeError(f"could not launch OPNsense bootstrap: {error[:300]}")
                elif guest_state not in {"converting", "opnsense"}:
                    raise RuntimeError(f"unexpected builder guest state: {guest_state}")
                workflow._wait_for_opnsense(db, builder.public_ip, image.version)
                workflow._validate_builder(
                    db, image, builder.public_ip, cidr, "AWS builder validation",
                )
                builder_key = workflow._fingerprint(db, builder.public_ip)
                workflow._record_validation(image, "builder", ssh_host_key=builder_key)
                db.commit()
                adapter = type("ComputeStopAdapter", (), {
                    "halt": lambda _, instance_id: self.compute.stop(instance_id),
                    "wait_stopped": lambda _, instance_id: self.compute.wait_stopped(instance_id),
                })()
                workflow._sanitize_and_halt(db, adapter, image, builder.public_ip)
            if not builder_key:
                raise RuntimeError("stopped builder is missing persisted host-key evidence")
            image.phase = image.status = "snapshotting"; db.commit()
            artifact = self.images.ensure_image(
                builder.instance_id,
                self.config.resource_token("opnsense", image.version.replace('.', '-'), image.id),
                tags,
            )
            image.ami_id = artifact.ami_id
            image.backing_snapshot_ids_json = json.dumps(artifact.snapshot_ids)
            db.commit()

        peer_lan = self.network.ensure_eni(
            network.subnet_ids["validation"], "172.31.254.2", [validation_sg],
            {**tags, "NetworkRole": "public-clone-validation"},
        )
        public_clone = self._launch(
            image, "public-clone", artifact.ami_id,
            [NetworkInterfaceSpec(0, subnet_id=network.subnet_ids["wan"],
                                  security_group_ids=(wan_sg,), associate_public_ip=False),
             NetworkInterfaceSpec(1, eni_id=peer_lan.eni_id, delete_on_termination=False)],
            tags, key_name,
        )
        image.test_instance_id = public_clone.instance_id; db.commit()
        evidence = json.loads(image.validation_results or "{}")
        public_key_fingerprint = evidence.get("clone_wan", {}).get("ssh_host_key")
        if not public_key_fingerprint:
            public_key_fingerprint = workflow._validate_clone_one(
                db, image, public_clone.public_ip, cidr,
            )
        if public_key_fingerprint == builder_key:
            raise RuntimeError("public clone reused the builder SSH host key")
        peer_ip = workflow._configure_validation_peer(
            db, public_clone.public_ip,
            {"ip_address": peer_lan.private_ip, "mac_address": peer_lan.mac_address},
        )

        lan = self.network.ensure_eni(
            network.subnet_ids["validation"], "172.31.254.3", [validation_sg],
            {**tags, "NetworkRole": "private-clone-validation"},
        )
        private_clone = self._launch(
            image, "private-clone", artifact.ami_id,
            [NetworkInterfaceSpec(0, subnet_id=network.subnet_ids["wan"], security_group_ids=(wan_sg,)),
             NetworkInterfaceSpec(1, eni_id=lan.eni_id, delete_on_termination=False)],
            tags, key_name,
        )
        image.second_test_instance_id = private_clone.instance_id; db.commit()
        evidence = json.loads(image.validation_results or "{}")
        private_key_fingerprint = evidence.get("clone_vpc", {}).get("ssh_host_key")
        if not private_key_fingerprint:
            private_key_fingerprint = workflow._validate_clone_two(
                db, image, private_clone.public_ip,
                {"ip_address": lan.private_ip, "mac_address": lan.mac_address},
                cidr, public_clone.public_ip, peer_ip,
            )
        if len({builder_key, public_key_fingerprint, private_key_fingerprint}) != 3:
            raise RuntimeError("builder and AMI clone SSH host keys are not unique")
        return {
            "builder_instance_id": builder.instance_id,
            "builder_vpc_id": network.vpc_id,
            "builder_subnet_id": network.subnet_ids["wan"],
            "validation_subnet_id": network.subnet_ids["validation"],
            "ami_id": artifact.ami_id,
            "snapshot_ids": list(artifact.snapshot_ids),
            "validation_results": {
                "public_clone": {"passed": True, "ssh_host_key": public_key_fingerprint},
                "private_clone": {"passed": True, "ssh_host_key": private_key_fingerprint,
                                  "private_ip": lan.private_ip},
            },
        }

    def cleanup_temporary(self, image, _result):
        """Remove disposable builders and validation networking, retaining the AMI."""
        tags = ownership_tags(self.config.environment, site_id=image.id)
        filters = [{"Name": f"tag:{key}", "Values": [value]} for key, value in tags.items()]
        response = self.ec2.describe_instances(Filters=filters + [{
            "Name": "instance-state-name",
            "Values": ["pending", "running", "stopping", "stopped"],
        }])
        instances = [
            row for reservation in response.get("Reservations", [])
            for row in reservation.get("Instances", [])
        ]
        instance_ids = []
        for row in instances:
            assert_owned(aws_tag_dict(row.get("Tags")), tags)
            instance_ids.append(row["InstanceId"])
        if instance_ids:
            self.ec2.terminate_instances(InstanceIds=instance_ids)
            self.ec2.get_waiter("instance_terminated").wait(InstanceIds=instance_ids)

        for address in self.ec2.describe_addresses(Filters=filters).get("Addresses", []):
            assert_owned(aws_tag_dict(address.get("Tags")), tags)
            if address.get("AssociationId"):
                self.ec2.disassociate_address(AssociationId=address["AssociationId"])
            self.ec2.release_address(AllocationId=address["AllocationId"])

        for eni in self.ec2.describe_network_interfaces(Filters=filters).get("NetworkInterfaces", []):
            assert_owned(aws_tag_dict(eni.get("TagSet")), tags)
            self.ec2.delete_network_interface(NetworkInterfaceId=eni["NetworkInterfaceId"])

        for group in self.ec2.describe_security_groups(Filters=filters).get("SecurityGroups", []):
            assert_owned(aws_tag_dict(group.get("Tags")), tags)
            self.ec2.delete_security_group(GroupId=group["GroupId"])

        for table in self.ec2.describe_route_tables(Filters=filters).get("RouteTables", []):
            assert_owned(aws_tag_dict(table.get("Tags")), tags)
            for association in table.get("Associations", []):
                if association.get("RouteTableAssociationId") and not association.get("Main"):
                    self.ec2.disassociate_route_table(
                        AssociationId=association["RouteTableAssociationId"],
                    )
            self.ec2.delete_route_table(RouteTableId=table["RouteTableId"])

        for subnet in self.ec2.describe_subnets(Filters=filters).get("Subnets", []):
            assert_owned(aws_tag_dict(subnet.get("Tags")), tags)
            self.ec2.delete_subnet(SubnetId=subnet["SubnetId"])

        for gateway in self.ec2.describe_internet_gateways(Filters=filters).get("InternetGateways", []):
            assert_owned(aws_tag_dict(gateway.get("Tags")), tags)
            for attachment in gateway.get("Attachments", []):
                self.ec2.detach_internet_gateway(
                    InternetGatewayId=gateway["InternetGatewayId"], VpcId=attachment["VpcId"],
                )
            self.ec2.delete_internet_gateway(InternetGatewayId=gateway["InternetGatewayId"])

        for key in self.ec2.describe_key_pairs(Filters=filters).get("KeyPairs", []):
            assert_owned(aws_tag_dict(key.get("Tags")), tags)
            self.ec2.delete_key_pair(KeyPairId=key["KeyPairId"])

        vpcs = self.ec2.describe_vpcs(Filters=filters).get("Vpcs", [])
        for vpc in vpcs:
            assert_owned(aws_tag_dict(vpc.get("Tags")), tags)
            for association in vpc.get("CidrBlockAssociationSet", []):
                if (association.get("AssociationId") and
                        association.get("CidrBlock") != vpc.get("CidrBlock")):
                    self.ec2.disassociate_vpc_cidr_block(
                        AssociationId=association["AssociationId"],
                    )
            self.ec2.delete_vpc(VpcId=vpc["VpcId"])
