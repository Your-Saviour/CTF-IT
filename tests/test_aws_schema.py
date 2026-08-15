from types import SimpleNamespace

from api.models import OpnsenseImage, Site, VM
from builder.base_loader import BaseType, load_base_type
from builder.plan_sizing import plan_for_vm


def test_models_expose_neutral_aws_fields():
    assert {
        "cloud_instance_id", "instance_type", "cloud_region", "availability_zone",
        "primary_eni_id", "wan_eni_id", "lan_eni_id", "subnet_id",
        "security_group_ids_json",
    } <= set(VM.__table__.columns.keys())
    assert {
        "availability_zone", "public_subnet_id", "infrastructure_subnet_id",
        "internet_gateway_id", "route_table_ids_json",
    } <= set(Site.__table__.columns.keys())
    assert {
        "ami_id", "backing_snapshot_ids_json", "region", "availability_zone",
        "builder_subnet_id", "validation_subnet_id",
    } <= set(OpnsenseImage.__table__.columns.keys())


def test_sizing_selects_cheapest_offered_ec2_type():
    base = BaseType("ubuntu", "Ubuntu", "", "Ubuntu", "t3.small")
    modules = [SimpleNamespace(min_ram_mb=1024, min_vcpu=1)]
    catalogue = [
        {"instance_type": "t3.small", "memory_mb": 2048, "vcpu": 2,
         "hourly_cost": 0.02, "regions": ["ap-southeast-2"]},
        {"instance_type": "t3.medium", "memory_mb": 4096, "vcpu": 2,
         "hourly_cost": 0.04, "regions": ["ap-southeast-2"]},
    ]

    assert plan_for_vm(
        base, modules, None, catalogue, region="ap-southeast-2"
    ) == "t3.small"


def test_sizing_respects_module_floor_and_region():
    base = BaseType("ubuntu", "Ubuntu", "", "Ubuntu", "t3.small")
    modules = [SimpleNamespace(min_ram_mb=3072, min_vcpu=2)]
    catalogue = [
        {"instance_type": "t3.medium", "memory_mb": 4096, "vcpu": 2,
         "hourly_cost": 0.04, "regions": ["ap-southeast-2"]},
        {"instance_type": "t3.large", "memory_mb": 8192, "vcpu": 2,
         "hourly_cost": 0.03, "regions": ["us-east-1"]},
    ]

    assert plan_for_vm(
        base, modules, None, catalogue, region="ap-southeast-2"
    ) == "t3.medium"


def test_base_types_use_ec2_instance_type_defaults():
    assert load_base_type("ubuntu_24_server").default_plan == "t3.small"
    assert load_base_type("opnsense").default_plan == "t3.medium"
