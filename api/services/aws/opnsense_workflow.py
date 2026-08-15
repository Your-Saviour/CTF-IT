import json
from dataclasses import replace

from api.services.ssh_keys import get_or_create_platform_keypair

from .compute import AwsComputeProvider, InstanceSpec, NetworkInterfaceSpec
from .config import AwsConfig
from .images import AwsImageProvider
from .network import AwsNetworkProvider, SecurityGroupSpec, SiteNetworkSpec
from .session import AwsSessionFactory
from .tags import ownership_tags


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
            client_token=f"ctf-it-opnsense-{image.id}-{role}",
            network_interfaces=tuple(interfaces), tags={**tags, "ImageRole": role},
            key_name=key_name,
        ))
        self.compute.wait_running(result.instance_id)
        allocation = self.compute.allocate_eip({**tags, "ImageRole": role})
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
        key_name = f"ctf-it-opnsense-{image.id}"
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

        workflow._verify_freebsd_base(db, builder.public_ip)
        script, digest = bootstrap_downloader(image.bootstrap_source_url)
        image.bootstrap_sha256 = digest; db.commit()
        workflow._upload_atomic(db, builder.public_ip, "/conf/config.xml",
                                workflow.render_golden_config(db, image, cidr).encode())
        workflow._upload_atomic(db, builder.public_ip, "/root/opnsense-bootstrap.sh", script, 0o700)
        code, _, error = workflow._ssh(
            db, builder.public_ip,
            f"nohup sh /root/opnsense-bootstrap.sh -r {image.version} -y >/var/log/opnsense-bootstrap.log 2>&1 </dev/null &",
            timeout=30,
        )
        if code:
            raise RuntimeError(f"could not launch OPNsense bootstrap: {error[:300]}")
        workflow._wait_for_opnsense(db, builder.public_ip, image.version)
        workflow._validate_builder(db, image, builder.public_ip, cidr, "AWS builder validation")
        builder_key = workflow._fingerprint(db, builder.public_ip)

        adapter = type("ComputeStopAdapter", (), {
            "halt": lambda _, instance_id: self.compute.stop(instance_id),
            "wait_stopped": lambda _, instance_id: self.compute.wait_stopped(instance_id),
        })()
        workflow._sanitize_and_halt(db, adapter, image, builder.public_ip)
        image.phase = image.status = "snapshotting"; db.commit()
        artifact = self.images.create_image(
            builder.instance_id, f"ctf-it-opnsense-{image.version.replace('.', '-')}-{image.id}", tags,
        )
        image.ami_id = artifact.ami_id
        image.backing_snapshot_ids_json = json.dumps(artifact.snapshot_ids)
        db.commit()

        peer_lan = self.network.create_eni(
            network.subnet_ids["validation"], "172.31.254.2", [validation_sg], tags,
        )
        public_clone = self._launch(
            image, "public-clone", artifact.ami_id,
            [NetworkInterfaceSpec(0, subnet_id=network.subnet_ids["wan"],
                                  security_group_ids=(wan_sg,), associate_public_ip=False),
             NetworkInterfaceSpec(1, eni_id=peer_lan.eni_id, delete_on_termination=False)],
            tags, key_name,
        )
        image.test_instance_id = public_clone.instance_id; db.commit()
        public_key_fingerprint = workflow._validate_clone_one(
            db, image, public_clone.public_ip, cidr,
        )
        if public_key_fingerprint == builder_key:
            raise RuntimeError("public clone reused the builder SSH host key")
        peer_ip = workflow._configure_validation_peer(
            db, public_clone.public_ip,
            {"ip_address": peer_lan.private_ip, "mac_address": peer_lan.mac_address},
        )

        lan = self.network.create_eni(
            network.subnet_ids["validation"], "172.31.254.3", [validation_sg], tags,
        )
        private_clone = self._launch(
            image, "private-clone", artifact.ami_id,
            [NetworkInterfaceSpec(0, subnet_id=network.subnet_ids["wan"], security_group_ids=(wan_sg,)),
             NetworkInterfaceSpec(1, eni_id=lan.eni_id, delete_on_termination=False)],
            tags, key_name,
        )
        image.second_test_instance_id = private_clone.instance_id; db.commit()
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
