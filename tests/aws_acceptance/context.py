import os
import io
import time
from dataclasses import dataclass

import paramiko
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scripts.aws_acceptance_cleanup import CleanupContext, cleanup_owned, inventory


@dataclass(frozen=True)
class AcceptanceContext:
    run_id: str
    expected_account_id: str
    account_id: str
    region: str
    config: object
    sessions: object

    @property
    def tags(self):
        return {"Application": "ctf-it", "ManagedBy": "ctf-it",
                "Environment": "acceptance", "AcceptanceRunId": self.run_id}

    @property
    def standard_vm_tags(self):
        return {**self.tags, "Canary": "standard-vm"}

    @property
    def cleanup_context(self):
        return CleanupContext(self.run_id, self.expected_account_id)

    def ec2(self):
        return self.sessions.client("ec2")

    def cleanup(self):
        return cleanup_owned(self.ec2(), self.cleanup_context)

    def inventory(self):
        return inventory(self.ec2(), self.cleanup_context)

    def create_standard_vm(self):
        from api.services.aws import AwsComputeProvider, InstanceSpec, NetworkInterfaceSpec
        from api.services.aws.tags import aws_tag_list

        ec2 = self.ec2()
        tags = self.standard_vm_tags
        cidr = os.environ.get("CTF_CONTROL_PLANE_CIDR", "").strip()
        if not cidr:
            raise ValueError("CTF_CONTROL_PLANE_CIDR is required for the SSH canary")
        group_name = f"ctf-it-acceptance-{self.run_id}-standard"
        group = ec2.create_security_group(
            VpcId=self.config.standard_vpc_id, GroupName=group_name,
            Description="CTF-IT disposable standard VM acceptance canary",
            TagSpecifications=[{"ResourceType": "security-group", "Tags": aws_tag_list(tags)}],
        )
        group_id = group["GroupId"]
        ec2.authorize_security_group_ingress(
            GroupId=group_id, IpPermissions=[{
                "IpProtocol": "tcp", "FromPort": 22, "ToPort": 22,
                "IpRanges": [{"CidrIp": cidr}],
            }],
        )

        private = Ed25519PrivateKey.generate()
        private_text = private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.OpenSSH,
            serialization.NoEncryption(),
        ).decode()
        public_text = private.public_key().public_bytes(
            serialization.Encoding.OpenSSH,
            serialization.PublicFormat.OpenSSH,
        ).decode()
        key_name = f"ctf-it-acceptance-{self.run_id}"
        ec2.import_key_pair(
            KeyName=key_name, PublicKeyMaterial=public_text.encode(),
            TagSpecifications=[{"ResourceType": "key-pair", "Tags": aws_tag_list(tags)}],
        )
        compute = AwsComputeProvider(ec2)
        instance = compute.launch_instance(InstanceSpec(
            ami_id=self.config.ubuntu_ami(self.region),
            instance_type=os.environ.get("AWS_ACCEPTANCE_INSTANCE_TYPE", "t3.small"),
            client_token=f"ctf-it-acceptance-{self.run_id}-standard",
            network_interfaces=(NetworkInterfaceSpec(
                0, subnet_id=self.config.standard_subnet_id,
                security_group_ids=(group_id,), associate_public_ip=False,
            ),),
            tags=tags, key_name=key_name,
            user_data="#cloud-config\nwrite_files:\n  - path: /var/lib/ctf-it-ready\n    content: ctf-it-ready\n",
        ))
        compute.wait_running(instance.instance_id)
        address = compute.ensure_eip(tags)
        compute.associate_eip(address.allocation_id, instance.primary_eni_id)
        return {
            "instance_id": instance.instance_id, "public_ip": address.public_ip,
            "private_key": private_text, "allocation_id": address.allocation_id,
            "security_group_id": group_id, "key_name": key_name, "tags": tags,
        }

    def destroy_standard_vm(self, vm):
        from api.services.aws import AwsComputeProvider, AwsNetworkProvider
        from api.services.aws.tags import assert_owned, aws_tag_dict

        ec2 = self.ec2()
        compute = AwsComputeProvider(ec2)
        tags = vm["tags"]
        compute.terminate_owned(vm["instance_id"], tags)
        compute.wait_terminated(vm["instance_id"])
        compute.release_owned_eip(vm["allocation_id"], tags)
        AwsNetworkProvider(ec2).delete_owned_security_group(
            vm["security_group_id"], tags,
        )
        key = ec2.describe_key_pairs(KeyNames=[vm["key_name"]])["KeyPairs"][0]
        assert_owned(aws_tag_dict(key.get("Tags")), tags)
        ec2.delete_key_pair(KeyName=vm["key_name"])

    def run_standard_vm_smoke(self, vm):
        key = paramiko.Ed25519Key.from_private_key(io.StringIO(vm["private_key"]))
        deadline = time.monotonic() + int(os.environ.get("AWS_ACCEPTANCE_SSH_TIMEOUT", "600"))
        last_error = None
        while time.monotonic() < deadline:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            try:
                client.connect(
                    vm["public_ip"],
                    username=os.environ.get("AWS_ACCEPTANCE_SSH_USER", "ubuntu"),
                    pkey=key, allow_agent=False, look_for_keys=False,
                    timeout=15, banner_timeout=15, auth_timeout=15,
                )
                _, stdout, stderr = client.exec_command(
                    "cloud-init status --wait >/dev/null && cat /var/lib/ctf-it-ready", timeout=300,
                )
                code = stdout.channel.recv_exit_status()
                if code:
                    raise RuntimeError(stderr.read().decode(errors="replace")[:500])
                return stdout.read().decode().strip()
            except Exception as exc:
                last_error = exc
                time.sleep(5)
            finally:
                client.close()
        raise RuntimeError(f"standard VM SSH smoke check timed out: {last_error}")


def require_acceptance_context(session_factory=None) -> AcceptanceContext:
    if os.environ.get("RUN_AWS_ACCEPTANCE") != "1":
        pytest.skip("AWS acceptance requires RUN_AWS_ACCEPTANCE=1")
    expected = os.environ.get("AWS_ACCEPTANCE_ACCOUNT_ID", "").strip()
    run_id = os.environ.get("AWS_ACCEPTANCE_RUN_ID", "").strip()
    region = os.environ.get("AWS_DEFAULT_REGION", "").strip()
    if not expected or not expected.isdigit() or not run_id or len(run_id) < 8 or not region:
        raise ValueError("approved account ID, unique run ID, and AWS region are required")
    if session_factory is None:
        from api.services.aws import AwsConfig, AwsSessionFactory
        config = AwsConfig.from_env()
        session_factory = AwsSessionFactory(config)
    else:
        config = session_factory.config
    identity = session_factory.caller_identity()
    if identity.account_id != expected:
        raise RuntimeError(f"refusing acceptance in AWS account {identity.account_id}; expected {expected}")
    return AcceptanceContext(run_id, expected, identity.account_id, region, config, session_factory)
