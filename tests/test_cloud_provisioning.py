from types import SimpleNamespace

from sqlalchemy import StaticPool, create_engine
from sqlalchemy.orm import sessionmaker

from api.database import Base
from api.models import Event, Team, VM
from api.services.aws import ElasticIpResult, InstanceResult
from api.services.cloud_provisioning import CloudProviders, create_cloud_vm, destroy_cloud_vm


def database():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    db = factory()
    event = Event(name="AWS", quota="{}")
    db.add(event); db.flush()
    team = Team(name="Blue", event_id=event.id)
    db.add(team); db.flush()
    vm = VM(
        hostname="target-1", team_id=team.id, event_id=event.id,
        instance_type="t3.small", cloud_region="ap-southeast-2",
        status="creating", ssh_user="ubuntu",
    )
    db.add(vm); db.commit()
    return factory, vm.id


class Compute:
    def __init__(self):
        self.launches = []
        self.describes = []
        self.terminated = []
        self.released = []
        self.keys = []

    def ensure_key_pair(self, name, public_key, tags):
        self.keys.append((name, public_key))
        return "key-123"

    def launch_instance(self, spec):
        self.launches.append(spec)
        return InstanceResult(
            "i-123", "pending", "eni-123", None, "10.0.1.8", "ap-southeast-2a",
        )

    def instance(self, instance_id):
        self.describes.append(instance_id)
        return InstanceResult(
            instance_id, "running", "eni-123", None, "10.0.1.8", "ap-southeast-2a",
        )

    def wait_running(self, instance_id):
        pass

    def allocate_eip(self, tags):
        return ElasticIpResult("eipalloc-123", "198.51.100.20")

    def associate_eip(self, allocation_id, eni_id):
        return "eipassoc-123"

    def terminate_owned(self, instance_id, tags):
        self.terminated.append(instance_id)

    def wait_terminated(self, instance_id):
        pass

    def release_owned_eip(self, allocation_id, tags):
        self.released.append(allocation_id)


def providers(factory, compute, configured):
    return CloudProviders(
        config=SimpleNamespace(
            default_region="ap-southeast-2", environment="test",
            standard_subnet_id="subnet-123", ubuntu_ami=lambda region: "ami-ubuntu",
        ),
        compute=compute,
        session_factory=factory,
        key_name="ctf-it",
        public_key="ssh-ed25519 TEST",
        security_group_ids=("sg-123",),
        configure_guest=lambda vm_id: configured.append(vm_id),
        wait_for_ssh=lambda vm: None,
        reconcile_dns=lambda vm: None,
        delete_dns=lambda vm: None,
    )


def test_create_persists_instance_and_eip_before_guest_configuration():
    factory, vm_id = database(); compute = Compute(); configured = []

    def check_persisted(selected_vm_id):
        db = factory(); vm = db.get(VM, selected_vm_id)
        assert vm.cloud_instance_id == "i-123"
        assert vm.eip_allocation_id == "eipalloc-123"
        assert vm.public_ip == "198.51.100.20"
        configured.append(selected_vm_id)

    cloud = providers(factory, compute, configured)
    cloud.configure_guest = check_persisted
    create_cloud_vm(vm_id, providers=cloud)

    db = factory(); vm = db.get(VM, vm_id)
    assert vm.status == "ready" and vm.provision_step == "complete"
    assert configured == [vm_id]
    assert compute.keys == [("ctf-it", "ssh-ed25519 TEST")]


def test_retry_reconciles_recorded_instance_without_second_launch():
    factory, vm_id = database(); compute = Compute(); configured = []
    db = factory(); vm = db.get(VM, vm_id); vm.cloud_instance_id = "i-existing"; db.commit()

    create_cloud_vm(vm_id, providers=providers(factory, compute, configured))

    assert compute.launches == []
    assert compute.describes == ["i-existing"]


def test_destroy_verifies_cloud_resources_before_removing_database_row():
    factory, vm_id = database(); compute = Compute(); configured = []
    db = factory(); vm = db.get(VM, vm_id)
    vm.cloud_instance_id = "i-123"; vm.eip_allocation_id = "eipalloc-123"; db.commit()

    destroy_cloud_vm(vm_id, providers=providers(factory, compute, configured))

    assert compute.terminated == ["i-123"]
    assert compute.released == ["eipalloc-123"]
    assert factory().get(VM, vm_id) is None
