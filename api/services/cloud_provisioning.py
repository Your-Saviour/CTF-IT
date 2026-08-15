import logging
from dataclasses import dataclass
from typing import Callable

from sqlalchemy.orm import Session, sessionmaker

from api.database import SessionLocal
from api.models import VM
from api.services.aws import (
    AwsComputeProvider,
    AwsConfig,
    InstanceSpec,
    NetworkInterfaceSpec,
    ownership_tags,
)

_log = logging.getLogger(__name__)


@dataclass
class CloudProviders:
    config: AwsConfig
    compute: AwsComputeProvider
    session_factory: sessionmaker
    key_name: str
    public_key: str
    security_group_ids: tuple[str, ...]
    configure_guest: Callable[[int], None]
    wait_for_ssh: Callable[[VM], None]
    reconcile_dns: Callable[[VM], None]
    delete_dns: Callable[[VM], None]


def _require_vm(db: Session, vm_id: int) -> VM:
    vm = db.get(VM, vm_id)
    if not vm:
        raise ValueError(f"VM {vm_id} does not exist")
    return vm


def _tags(providers: CloudProviders, vm: VM) -> dict[str, str]:
    return ownership_tags(
        providers.config.environment,
        event_id=vm.event_id,
        team_id=vm.team_id,
        vm_id=vm.id,
    )


def _instance_spec(providers: CloudProviders, vm: VM) -> InstanceSpec:
    region = vm.cloud_region or providers.config.default_region
    return InstanceSpec(
        ami_id=providers.config.ubuntu_ami(region),
        instance_type=vm.instance_type or "t3.small",
        client_token=f"ctf-it-vm-{vm.id}",
        network_interfaces=(NetworkInterfaceSpec(
            0,
            subnet_id=vm.subnet_id or providers.config.standard_subnet_id,
            security_group_ids=providers.security_group_ids,
        ),),
        tags=_tags(providers, vm),
        key_name=providers.key_name,
    )


def create_cloud_vm(vm_id: int, providers: CloudProviders) -> None:
    db = providers.session_factory()
    try:
        vm = _require_vm(db, vm_id)
        vm.status = "provisioning"
        vm.provision_step = "creating_instance"
        vm.provision_error = None
        db.commit()

        if vm.cloud_instance_id:
            result = providers.compute.instance(vm.cloud_instance_id)
        else:
            providers.compute.ensure_key_pair(
                providers.key_name,
                providers.public_key,
                ownership_tags(providers.config.environment),
            )
            result = providers.compute.launch_instance(_instance_spec(providers, vm))
            vm.cloud_instance_id = result.instance_id
        vm.primary_eni_id = result.primary_eni_id
        vm.private_ip = result.private_ip
        vm.cloud_region = vm.cloud_region or providers.config.default_region
        vm.availability_zone = result.availability_zone
        vm.subnet_id = vm.subnet_id or providers.config.standard_subnet_id
        db.commit()

        providers.compute.wait_running(result.instance_id)
        if not vm.eip_allocation_id:
            allocation = providers.compute.allocate_eip(_tags(providers, vm))
            providers.compute.associate_eip(allocation.allocation_id, result.primary_eni_id)
            vm.eip_allocation_id = allocation.allocation_id
            vm.public_ip = allocation.public_ip
            vm.ip_address = allocation.public_ip
            db.commit()

        vm.provision_step = "waiting_for_ssh"
        db.commit()
        providers.wait_for_ssh(vm)
        providers.reconcile_dns(vm)
        vm.provision_step = "configuring_guest"
        db.commit()
        providers.configure_guest(vm.id)

        db.expire_all()
        vm = _require_vm(db, vm_id)
        vm.status = "ready"
        vm.provision_step = "complete"
        vm.provision_error = None
        db.commit()
    except Exception as exc:
        db.rollback()
        vm = db.get(VM, vm_id)
        if vm:
            vm.status = "failed"
            vm.provision_error = str(exc)[:1000]
            db.commit()
        _log.exception("AWS VM creation failed for VM %d", vm_id)
        raise
    finally:
        db.close()


def destroy_cloud_vm(vm_id: int, providers: CloudProviders) -> None:
    db = providers.session_factory()
    try:
        vm = _require_vm(db, vm_id)
        vm.status = "destroying"
        db.commit()
        providers.delete_dns(vm)
        tags = _tags(providers, vm)
        if vm.cloud_instance_id:
            providers.compute.terminate_owned(vm.cloud_instance_id, tags)
            providers.compute.wait_terminated(vm.cloud_instance_id)
        if vm.eip_allocation_id:
            providers.compute.release_owned_eip(vm.eip_allocation_id, tags)
        db.delete(vm)
        db.commit()
    except Exception as exc:
        db.rollback()
        vm = db.get(VM, vm_id)
        if vm:
            vm.status = "destroy_failed"
            vm.provision_error = str(exc)[:1000]
            db.commit()
        raise
    finally:
        db.close()


def default_session_factory():
    return SessionLocal
