from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, event, func, inspect, select, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.database import Base
from api.database import get_db


def utcnow():
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    session_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=True)
    deactivated_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    password_changed_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), nullable=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=True)

    event: Mapped["Event"] = relationship(back_populates="users")
    team: Mapped["Team"] = relationship(back_populates="users")
    vpn_credential: Mapped["VPNCredential"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )


class AccountToken(Base):
    """A single-use invitation or password-reset token.

    Only a SHA-256 digest is persisted. Raw tokens exist just long enough to be
    returned to the administrator that created them.
    """

    __tablename__ = "account_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    purpose: Mapped[str] = mapped_column(String(24), nullable=False)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), nullable=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=True)
    target_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=True)
    intended_username: Mapped[str] = mapped_column(String(64), nullable=True)
    intended_is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    redeemed_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)


class AdminAudit(Base):
    __tablename__ = "admin_audit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=True)
    target_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(48), nullable=False)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    quota: Mapped[str] = mapped_column(Text, nullable=False)
    infrastructure: Mapped[str] = mapped_column(Text, nullable=True)
    infrastructure_layout: Mapped[str] = mapped_column(Text, nullable=True)
    module_plan: Mapped[str] = mapped_column(Text, nullable=True)
    operation_plan: Mapped[str] = mapped_column(Text, nullable=True)
    timeline: Mapped[str] = mapped_column(Text, nullable=True)
    scenario_id: Mapped[int] = mapped_column(ForeignKey("scenarios.id"), nullable=True)
    scenario_version: Mapped[int] = mapped_column(Integer, nullable=True)
    scenario_fingerprint: Mapped[str] = mapped_column(String(64), nullable=True)
    open: Mapped[bool] = mapped_column(Boolean, default=False)  # kept for SQLite compat; superseded by status
    status: Mapped[str] = mapped_column(String(16), default="draft")
    description: Mapped[str] = mapped_column(Text, nullable=True)
    welcome_message: Mapped[str] = mapped_column(Text, nullable=True)
    time_limit_minutes: Mapped[int] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    ends_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, server_default=func.current_timestamp(), nullable=False
    )
    # Semaphore project created once per event, reused for all VM provisions
    semaphore_project_id: Mapped[int] = mapped_column(Integer, nullable=True)
    semaphore_key_id: Mapped[int] = mapped_column(Integer, nullable=True)

    users: Mapped[list["User"]] = relationship(back_populates="event")
    teams: Mapped[list["Team"]] = relationship(back_populates="event")
    vms: Mapped[list["VM"]] = relationship(back_populates="event")
    sites: Mapped[list["Site"]] = relationship(back_populates="event", cascade="all, delete-orphan")
    operations: Mapped[list["EventOperation"]] = relationship(
        back_populates="event",
        cascade="all, delete-orphan",
        order_by="EventOperation.position",
    )
    integrations: Mapped[list["EventIntegration"]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )
    green_deployment_facts: Mapped[list["GreenDeploymentFact"]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )


class EventOperation(Base):
    __tablename__ = "event_operations"
    __table_args__ = (
        UniqueConstraint("event_id", "name", name="uq_event_operations_event_name"),
        Index("ix_event_operations_event_position", "event_id", "position"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    operation_plan: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, server_default=func.current_timestamp(), nullable=False
    )

    event: Mapped["Event"] = relationship(back_populates="operations")


class OperationRun(Base):
    __tablename__ = "operation_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), nullable=False)
    operation_id: Mapped[int] = mapped_column(ForeignKey("event_operations.id"), nullable=False)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="queued", nullable=False)
    plan_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    fact_store: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    trigger: Mapped[str] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, server_default=func.current_timestamp(), nullable=False
    )

    steps: Mapped[list["OperationRunStep"]] = relationship(back_populates="run", cascade="all, delete-orphan")


class OperationRunStep(Base):
    __tablename__ = "operation_run_steps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("operation_runs.id", ondelete="CASCADE"), nullable=False)
    node_id: Mapped[str] = mapped_column(String(64), nullable=False)
    node_type: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="queued", nullable=False)
    result: Mapped[str] = mapped_column(String(16), nullable=True)
    output: Mapped[str] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    caldera_operation_id: Mapped[str] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    run: Mapped["OperationRun"] = relationship(back_populates="steps")


class Scenario(Base):
    __tablename__ = "scenarios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    quota: Mapped[str] = mapped_column(Text, nullable=False)
    infrastructure: Mapped[str] = mapped_column(Text, nullable=True)
    infrastructure_layout: Mapped[str] = mapped_column(Text, nullable=True)
    module_plan: Mapped[str] = mapped_column(Text, nullable=True)
    operations_json: Mapped[str] = mapped_column(Text, nullable=True)
    timeline: Mapped[str] = mapped_column(Text, nullable=True)
    content_fingerprint: Mapped[str] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, server_default=func.current_timestamp(), nullable=False
    )


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    # VPC networking (set when event has a firewall role in vm_quota)
    vpc_id: Mapped[str] = mapped_column(String(64), nullable=True)
    team_index: Mapped[int] = mapped_column(Integer, nullable=True)

    event: Mapped["Event"] = relationship(back_populates="teams")
    vms: Mapped[list["VM"]] = relationship(back_populates="team")
    users: Mapped[list["User"]] = relationship(back_populates="team")
    training_credential: Mapped["TeamTrainingCredential"] = relationship(
        back_populates="team", uselist=False, cascade="all, delete-orphan"
    )
    sites: Mapped[list["Site"]] = relationship(back_populates="team", cascade="all, delete-orphan")
    vpn_gateway: Mapped["TeamVPNGateway"] = relationship(
        back_populates="team", uselist=False, cascade="all, delete-orphan"
    )


class TeamVPNGateway(Base):
    __tablename__ = "team_vpn_gateways"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False, unique=True)
    vm_id: Mapped[int] = mapped_column(ForeignKey("vms.id"), nullable=True, unique=True)
    listen_port: Mapped[int] = mapped_column(Integer, nullable=False, default=51820)
    vpn_address: Mapped[str] = mapped_column(String(45), nullable=False, unique=True)
    public_key: Mapped[str] = mapped_column(Text, nullable=False)
    private_key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    platform_public_key: Mapped[str] = mapped_column(Text, nullable=True)
    platform_private_key_encrypted: Mapped[str] = mapped_column(Text, nullable=True)
    platform_address: Mapped[str] = mapped_column(String(45), nullable=True, unique=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    team: Mapped["Team"] = relationship(back_populates="vpn_gateway")


class VPNCredential(Base):
    __tablename__ = "vpn_credentials"
    __table_args__ = (UniqueConstraint("team_id", "address", name="uq_vpn_team_address"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, unique=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    address: Mapped[str] = mapped_column(String(45), nullable=False, unique=True)
    public_key: Mapped[str] = mapped_column(Text, nullable=False)
    private_key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    revoked_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship(back_populates="vpn_credential")


class Site(Base):
    __tablename__ = "sites"
    __table_args__ = (
        UniqueConstraint("team_id", "key", name="uq_site_team_key"),
        UniqueConstraint("allocated_cidr", name="uq_site_allocated_cidr"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), nullable=False)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    region: Mapped[str] = mapped_column(String(16), nullable=False)
    allocated_cidr: Mapped[str] = mapped_column(String(43), nullable=False)
    infrastructure_subnet: Mapped[str] = mapped_column(String(43), nullable=False)
    vpc_id: Mapped[str] = mapped_column(String(64), nullable=True)
    availability_zone: Mapped[str] = mapped_column(String(32), nullable=True)
    public_subnet_id: Mapped[str] = mapped_column(String(64), nullable=True)
    infrastructure_subnet_id: Mapped[str] = mapped_column(String(64), nullable=True)
    internet_gateway_id: Mapped[str] = mapped_column(String(64), nullable=True)
    route_table_ids_json: Mapped[str] = mapped_column(Text, nullable=True)
    wan_security_group_id: Mapped[str] = mapped_column(String(64), nullable=True)
    lan_security_group_id: Mapped[str] = mapped_column(String(64), nullable=True)
    firewall_vm_id: Mapped[int] = mapped_column(
        ForeignKey("vms.id", name="fk_sites_firewall_vm_id", use_alter=True), nullable=True
    )
    tunnel_public_key: Mapped[str] = mapped_column(Text, nullable=True)
    tunnel_private_key_encrypted: Mapped[str] = mapped_column(Text, nullable=True)
    tunnel_address: Mapped[str] = mapped_column(String(45), nullable=True, unique=True)
    tunnel_status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    control_plane_status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    event: Mapped["Event"] = relationship(back_populates="sites")
    team: Mapped["Team"] = relationship(back_populates="sites")
    zones: Mapped[list["Zone"]] = relationship(back_populates="site", cascade="all, delete-orphan")
    private_boot_certifications: Mapped[list["PrivateBootCertification"]] = relationship(
        back_populates="site", cascade="all, delete-orphan"
    )


class PrivateBootCertification(Base):
    """Event-local record of the active AMI private-network validation gate."""

    __tablename__ = "private_boot_certifications"
    __table_args__ = (
        UniqueConstraint(
            "site_id", "os_id", "region", "vpc_id", "firewall_instance_id",
            name="uq_private_boot_site_image_vpc",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"), nullable=False)
    base_type: Mapped[str] = mapped_column(String(64), nullable=False)
    os_id: Mapped[int] = mapped_column(Integer, nullable=False)
    region: Mapped[str] = mapped_column(String(16), nullable=False)
    vpc_id: Mapped[str] = mapped_column(String(64), nullable=False)
    firewall_instance_id: Mapped[str] = mapped_column(String(64), nullable=False)
    plan: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    phase: Mapped[str] = mapped_column(String(48), nullable=False, default="pending")
    instance_id: Mapped[str] = mapped_column(String(64), nullable=True)
    provider_ip: Mapped[str] = mapped_column(String(45), nullable=True)
    mac_address: Mapped[str] = mapped_column(String(32), nullable=True)
    diagnostic_detail: Mapped[str] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    cleanup_completed_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    site: Mapped["Site"] = relationship(back_populates="private_boot_certifications")


class Zone(Base):
    __tablename__ = "zones"
    __table_args__ = (UniqueConstraint("site_id", "key", name="uq_zone_site_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"), nullable=False)
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    team_role: Mapped[str] = mapped_column(String(8), nullable=False)
    subnet: Mapped[str] = mapped_column(String(43), nullable=False, unique=True)
    gateway_address: Mapped[str] = mapped_column(String(45), nullable=False)
    subnet_id: Mapped[str] = mapped_column(String(64), nullable=True)
    security_group_id: Mapped[str] = mapped_column(String(64), nullable=True)
    order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    site: Mapped["Site"] = relationship(back_populates="zones")
    vms: Mapped[list["VM"]] = relationship(back_populates="zone")


class TeamTrainingCredential(Base):
    __tablename__ = "team_training_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False, unique=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False, default="ctf-trainee")
    private_key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    public_key: Mapped[str] = mapped_column(Text, nullable=False)
    sudo_password_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    provisioned_vm_ids_json: Mapped[str] = mapped_column(Text, nullable=True)
    last_error_code: Mapped[str] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    rotated_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    team: Mapped["Team"] = relationship(back_populates="training_credential")


class VM(Base):
    __tablename__ = "vms"
    __table_args__ = (
        Index("ix_vms_event_team", "event_id", "team_id"),
        Index(
            "uq_vms_event_green_key", "event_id", "green_key", unique=True,
            sqlite_where=text("green_key IS NOT NULL"),
            postgresql_where=text("green_key IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hostname: Mapped[str] = mapped_column(String(256), nullable=True)
    ip_address: Mapped[str] = mapped_column(String(64), nullable=True)
    os: Mapped[str] = mapped_column(String(64), nullable=True, default="Ubuntu 22.04")
    status: Mapped[str] = mapped_column(String(16), default="registered")
    ssh_port: Mapped[int] = mapped_column(Integer, nullable=True, default=22)
    ssh_user: Mapped[str] = mapped_column(String(64), nullable=True, default="root")
    ssh_host_key: Mapped[str] = mapped_column(String(512), nullable=True)
    notes: Mapped[str] = mapped_column(Text, nullable=True)
    ust_prompt: Mapped[str] = mapped_column(Text, nullable=True)
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), nullable=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), nullable=False)
    green_key: Mapped[str] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # Provisioning state
    provision_step: Mapped[str] = mapped_column(String(32), nullable=True)
    provision_error: Mapped[str] = mapped_column(Text, nullable=True)
    semaphore_project_id: Mapped[int] = mapped_column(Integer, nullable=True)
    semaphore_task_id: Mapped[int] = mapped_column(Integer, nullable=True)
    agent_status: Mapped[str] = mapped_column(String(16), nullable=True)
    # Legacy Vultr fields remain readable for historical records.
    vultr_id: Mapped[str] = mapped_column(String(64), nullable=True)
    vultr_plan: Mapped[str] = mapped_column(String(64), nullable=True)
    vultr_region: Mapped[str] = mapped_column(String(16), nullable=True)
    # Provider-neutral AWS provisioning fields.
    cloud_instance_id: Mapped[str] = mapped_column(String(64), nullable=True)
    instance_type: Mapped[str] = mapped_column(String(64), nullable=True)
    cloud_region: Mapped[str] = mapped_column(String(32), nullable=True)
    availability_zone: Mapped[str] = mapped_column(String(32), nullable=True)
    primary_eni_id: Mapped[str] = mapped_column(String(64), nullable=True)
    wan_eni_id: Mapped[str] = mapped_column(String(64), nullable=True)
    lan_eni_id: Mapped[str] = mapped_column(String(64), nullable=True)
    subnet_id: Mapped[str] = mapped_column(String(64), nullable=True)
    security_group_ids_json: Mapped[str] = mapped_column(Text, nullable=True)
    eip_allocation_id: Mapped[str] = mapped_column(String(64), nullable=True)
    cloudflare_record_id: Mapped[str] = mapped_column(String(64), nullable=True)
    vm_type: Mapped[str] = mapped_column(String(64), nullable=True)
    # Caldera attack tree cache
    attack_tree_json: Mapped[str] = mapped_column(Text, nullable=True)
    # Base type ID used when provisioned (e.g. "ubuntu_24_server")
    base_type: Mapped[str] = mapped_column(String(64), nullable=True)

    role: Mapped[str] = mapped_column(String(24), nullable=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"), nullable=True)
    zone_id: Mapped[int] = mapped_column(ForeignKey("zones.id"), nullable=True)
    public_ip: Mapped[str] = mapped_column(String(45), nullable=True)
    private_ip: Mapped[str] = mapped_column(String(45), nullable=True)

    # VPC networking
    vpc_ip: Mapped[str] = mapped_column(String(45), nullable=True)
    vpc_mac: Mapped[str] = mapped_column(String(32), nullable=True)
    network_boot_id: Mapped[str] = mapped_column(String(128), nullable=True)
    network_phase: Mapped[str] = mapped_column(String(32), nullable=True)
    # OPNsense admin password (firewall VMs only)
    admin_password: Mapped[str] = mapped_column(String(128), nullable=True)
    opnsense_image_id: Mapped[int] = mapped_column(ForeignKey("opnsense_images.id"), nullable=True)
    opnsense_release: Mapped[str] = mapped_column(String(16), nullable=True)
    opnsense_snapshot_id: Mapped[str] = mapped_column(String(64), nullable=True)
    opnsense_config_token: Mapped[str] = mapped_column(String(64), nullable=True)
    opnsense_config_fingerprint: Mapped[str] = mapped_column(String(64), nullable=True)
    opnsense_config_status: Mapped[str] = mapped_column(String(24), nullable=True)
    opnsense_config_started_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    opnsense_config_completed_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    team: Mapped["Team"] = relationship(back_populates="vms")
    event: Mapped["Event"] = relationship(back_populates="vms")
    modules: Mapped[list["VMModule"]] = relationship(back_populates="vm", cascade="all, delete-orphan")
    goals: Mapped[list["VMGoal"]] = relationship(back_populates="vm", cascade="all, delete-orphan")
    zone: Mapped["Zone"] = relationship(back_populates="vms")
    green_deployments: Mapped[list["GreenDeploymentState"]] = relationship(
        back_populates="vm", cascade="all, delete-orphan"
    )
    owned_service_credentials: Mapped[list["ServiceCredential"]] = relationship(
        back_populates="owner_green_vm", foreign_keys="ServiceCredential.owner_green_vm_id"
    )
    owned_integration_destinations: Mapped[list["IntegrationDestination"]] = relationship(
        back_populates="owner_green_vm", foreign_keys="IntegrationDestination.owner_green_vm_id"
    )


class GreenDeploymentFact(Base):
    __tablename__ = "green_deployment_facts"
    __table_args__ = (
        UniqueConstraint("event_id", "vm_key", "trait", name="uq_green_fact_scope"),
        Index("ix_green_facts_event_vm", "event_id", "vm_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), nullable=False)
    vm_key: Mapped[str] = mapped_column(String(64), nullable=False)
    trait: Mapped[str] = mapped_column(String(128), nullable=False)
    encrypted_value: Mapped[str] = mapped_column(Text, nullable=False)
    secret: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    event: Mapped["Event"] = relationship(back_populates="green_deployment_facts")


class GreenDeploymentState(Base):
    __tablename__ = "green_deployment_states"
    __table_args__ = (
        UniqueConstraint("vm_id", "module_id", name="uq_green_deployment_module"),
        Index("ix_green_deployments_vm", "vm_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vm_id: Mapped[int] = mapped_column(ForeignKey("vms.id", ondelete="CASCADE"), nullable=False)
    module_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)
    current_step: Mapped[str] = mapped_column(String(128), nullable=True)
    resolved_commit: Mapped[str] = mapped_column(String(64), nullable=True)
    service_url: Mapped[str] = mapped_column(String(512), nullable=True)
    health_status: Mapped[str] = mapped_column(String(24), nullable=True)
    error_code: Mapped[str] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    vm: Mapped["VM"] = relationship(back_populates="green_deployments")


class PlatformSettings(Base):
    __tablename__ = "platform_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)


class OpnsenseImage(Base):
    """Auditable state for one administrator-managed OPNsense snapshot build."""

    __tablename__ = "opnsense_images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[str] = mapped_column(String(16), nullable=False)
    # Legacy ISO metadata remains nullable so historical rows stay readable.
    artifact_url: Mapped[str] = mapped_column(Text, nullable=True)
    checksum_url: Mapped[str] = mapped_column(Text, nullable=True)
    signature_url: Mapped[str] = mapped_column(Text, nullable=True)
    compressed_sha256: Mapped[str] = mapped_column(String(64), nullable=True)
    iso_sha256: Mapped[str] = mapped_column(String(64), nullable=True)
    vultr_iso_id: Mapped[str] = mapped_column(String(64), nullable=True)
    builder_instance_id: Mapped[str] = mapped_column(String(64), nullable=True)
    builder_vpc_id: Mapped[str] = mapped_column(String(64), nullable=True)
    builder_firewall_group_id: Mapped[str] = mapped_column(String(64), nullable=True)
    test_instance_id: Mapped[str] = mapped_column(String(64), nullable=True)
    second_test_instance_id: Mapped[str] = mapped_column(String(64), nullable=True)
    validation_vpc_id: Mapped[str] = mapped_column(String(64), nullable=True)
    snapshot_id: Mapped[str] = mapped_column(String(64), nullable=True)
    ami_id: Mapped[str] = mapped_column(String(64), nullable=True)
    backing_snapshot_ids_json: Mapped[str] = mapped_column(Text, nullable=True)
    region: Mapped[str] = mapped_column(String(32), nullable=True)
    availability_zone: Mapped[str] = mapped_column(String(32), nullable=True)
    builder_subnet_id: Mapped[str] = mapped_column(String(64), nullable=True)
    validation_subnet_id: Mapped[str] = mapped_column(String(64), nullable=True)
    route_token: Mapped[str] = mapped_column(String(128), nullable=True)
    builder_config_token: Mapped[str] = mapped_column(String(128), nullable=True)
    build_method: Mapped[str] = mapped_column(String(32), nullable=True)
    base_os: Mapped[str] = mapped_column(String(64), nullable=True)
    bootstrap_source_url: Mapped[str] = mapped_column(Text, nullable=True)
    bootstrap_sha256: Mapped[str] = mapped_column(String(64), nullable=True)
    validation_results: Mapped[str] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="creating_builder")
    phase: Mapped[str] = mapped_column(String(32), nullable=False, default="creating_builder")
    error_detail: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    validated_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    activated_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    retired_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    build_duration_seconds: Mapped[int] = mapped_column(Integer, nullable=True)


class VMModule(Base):
    __tablename__ = "vm_modules"
    __table_args__ = (Index("ix_vm_modules_vm_module", "vm_id", "module_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vm_id: Mapped[int] = mapped_column(ForeignKey("vms.id"), nullable=False)
    module_id: Mapped[str] = mapped_column(String(64), nullable=False)
    module_type: Mapped[str] = mapped_column(String(32), nullable=False)
    difficulty: Mapped[str] = mapped_column(String(8), nullable=False)
    points: Mapped[int] = mapped_column(Integer, nullable=False)
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    last_verified_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    first_completed_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    completed_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=True)
    verification_error_code: Mapped[str] = mapped_column(String(64), nullable=True)
    verification_baseline_json: Mapped[str] = mapped_column(Text, nullable=True)
    # "preapplied" = blue team sees+fixes, "caldera" = red team exploits.
    # None for types where stage doesn't apply (hardening, application_*, goal).
    stage: Mapped[str] = mapped_column(String(16), nullable=True)

    vm: Mapped["VM"] = relationship(back_populates="modules")
    attempts: Mapped[list["VerificationAttempt"]] = relationship(
        back_populates="module_assignment", cascade="all, delete-orphan"
    )
    hint_reveals: Mapped[list["HintReveal"]] = relationship(
        back_populates="module_assignment", cascade="all, delete-orphan"
    )


class VerificationAttempt(Base):
    __tablename__ = "verification_attempts"
    __table_args__ = (Index("ix_verification_attempts_assignment_created", "module_assignment_id", "created_at", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    module_assignment_id: Mapped[int] = mapped_column(ForeignKey("vm_modules.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=True)
    trigger_type: Mapped[str] = mapped_column(String(16), nullable=False)
    result: Mapped[str] = mapped_column(String(24), nullable=False)
    safe_summary: Mapped[str] = mapped_column(String(256), nullable=False)
    error_code: Mapped[str] = mapped_column(String(64), nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    module_assignment: Mapped["VMModule"] = relationship(back_populates="attempts")


class HintReveal(Base):
    __tablename__ = "hint_reveals"
    __table_args__ = (
        UniqueConstraint("user_id", "module_assignment_id", "hint_index", name="uq_hint_reveal"),
        Index("ix_hint_reveals_assignment_revealed", "module_assignment_id", "revealed_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    module_assignment_id: Mapped[int] = mapped_column(ForeignKey("vm_modules.id"), nullable=False)
    hint_index: Mapped[int] = mapped_column(Integer, nullable=False)
    revealed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    module_assignment: Mapped["VMModule"] = relationship(back_populates="hint_reveals")


@event.listens_for(VMModule, "before_insert")
def _initial_module_status(_mapper, _connection, target: VMModule):
    if target.completed and target.status in {None, "open"}:
        target.status = "completed"
    target.completed = target.status == "completed"


@event.listens_for(VMModule, "before_update")
def _synchronise_module_status(_mapper, _connection, target: VMModule):
    state = inspect(target)
    if state.attrs.status.history.has_changes():
        target.completed = target.status == "completed"
    elif state.attrs.completed.history.has_changes():
        target.status = "completed" if target.completed else "open"


@event.listens_for(User, "before_insert")
@event.listens_for(User, "before_update")
def _validate_user_team_event(_mapper, connection, target: User):
    if target.is_admin and target.team_id is not None:
        raise ValueError("administrators cannot belong to participant teams")
    if target.team_id is not None:
        team_event_id = connection.execute(select(Team.event_id).where(Team.id == target.team_id)).scalar_one_or_none()
        if team_event_id is None or team_event_id != target.event_id:
            raise ValueError("participant team must belong to participant event")


class VMGoal(Base):
    __tablename__ = "vm_goals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vm_id: Mapped[int] = mapped_column(ForeignKey("vms.id"), nullable=False)
    module_id: Mapped[str] = mapped_column(String(64), nullable=False)  # goal module id
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending/achieved/defended
    red_points: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    defend_points: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    achievement_count: Mapped[int] = mapped_column(Integer, default=0)
    defend_count: Mapped[int] = mapped_column(Integer, default=0)
    achieved_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    defended_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    vm: Mapped["VM"] = relationship(back_populates="goals")


class ModuleRepo(Base):
    """An external git repository that contributes additional modules."""

    __tablename__ = "module_repos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    repo_url: Mapped[str] = mapped_column(String(512), nullable=False)
    branch: Mapped[str] = mapped_column(String(128), nullable=False, default="main")
    ssh_key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    last_sync_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class ServiceCredential(Base):
    __tablename__ = "service_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    service_name: Mapped[str] = mapped_column(String(64), nullable=False)
    credential_type: Mapped[str] = mapped_column(String(32), nullable=False)  # admin/user/token
    username: Mapped[str] = mapped_column(String(256), nullable=True)
    password: Mapped[str] = mapped_column(String(256), nullable=False)  # encrypted
    url: Mapped[str] = mapped_column(String(512), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=True)

    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=True)
    owner_green_vm_id: Mapped[int] = mapped_column(
        ForeignKey("vms.id", ondelete="SET NULL"), nullable=True
    )
    integration_destinations: Mapped[list["IntegrationDestination"]] = relationship(
        back_populates="credential"
    )
    owner_green_vm: Mapped["VM"] = relationship(
        back_populates="owned_service_credentials", foreign_keys=[owner_green_vm_id]
    )


class IntegrationDestination(Base):
    __tablename__ = "integration_destinations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    adapter_key: Mapped[str] = mapped_column(String(64), nullable=False)
    base_url: Mapped[str] = mapped_column(String(512), nullable=False)
    credential_id: Mapped[int] = mapped_column(
        ForeignKey("service_credentials.id", ondelete="RESTRICT"), nullable=False
    )
    owner_green_vm_id: Mapped[int] = mapped_column(
        ForeignKey("vms.id", ondelete="SET NULL"), nullable=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    allow_insecure_http: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    config_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    last_test_status: Mapped[str] = mapped_column(String(24), nullable=True)
    last_test_error: Mapped[str] = mapped_column(Text, nullable=True)
    last_tested_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    credential: Mapped["ServiceCredential"] = relationship(back_populates="integration_destinations")
    owner_green_vm: Mapped["VM"] = relationship(
        back_populates="owned_integration_destinations", foreign_keys=[owner_green_vm_id]
    )
    bindings: Mapped[list["EventIntegration"]] = relationship(back_populates="destination")


class EventIntegration(Base):
    __tablename__ = "event_integrations"
    __table_args__ = (
        UniqueConstraint("event_id", "destination_id", name="uq_event_integration_destination"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), nullable=False)
    destination_id: Mapped[int] = mapped_column(
        ForeignKey("integration_destinations.id", ondelete="RESTRICT"), nullable=False
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_success_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    last_status: Mapped[str] = mapped_column(String(24), nullable=True)
    last_error_code: Mapped[str] = mapped_column(String(64), nullable=True)
    last_error_message: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    event: Mapped["Event"] = relationship(back_populates="integrations")
    destination: Mapped["IntegrationDestination"] = relationship(back_populates="bindings")
    jobs: Mapped[list["IntegrationSyncJob"]] = relationship(
        back_populates="binding", cascade="all, delete-orphan"
    )
    attempts: Mapped[list["IntegrationSyncAttempt"]] = relationship(
        back_populates="binding", cascade="all, delete-orphan"
    )


class IntegrationSyncJob(Base):
    __tablename__ = "integration_sync_jobs"
    __table_args__ = (
        Index("ix_integration_jobs_due", "status", "next_attempt_at", "priority"),
        Index(
            "uq_integration_jobs_active_binding", "binding_id", unique=True,
            sqlite_where=text("status IN ('pending', 'running', 'retrying')"),
            postgresql_where=text("status IN ('pending', 'running', 'retrying')"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    binding_id: Mapped[int] = mapped_column(
        ForeignKey("event_integrations.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)
    trigger_reason: Mapped[str] = mapped_column(String(64), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    claimed_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    claim_token: Mapped[str] = mapped_column(String(64), nullable=True)
    follow_up_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    binding: Mapped["EventIntegration"] = relationship(back_populates="jobs")
    attempts: Mapped[list["IntegrationSyncAttempt"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )


class IntegrationSyncAttempt(Base):
    __tablename__ = "integration_sync_attempts"
    __table_args__ = (
        Index("ix_integration_attempts_binding_created", "binding_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("integration_sync_jobs.id", ondelete="CASCADE"), nullable=False
    )
    binding_id: Mapped[int] = mapped_column(
        ForeignKey("event_integrations.id", ondelete="CASCADE"), nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    result: Mapped[str] = mapped_column(String(24), nullable=False)
    http_status: Mapped[int] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str] = mapped_column(String(64), nullable=True)
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    finished_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    job: Mapped["IntegrationSyncJob"] = relationship(back_populates="attempts")
    binding: Mapped["EventIntegration"] = relationship(back_populates="attempts")
