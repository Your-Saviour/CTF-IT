from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
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

    event: Mapped["Event"] = relationship(back_populates="users")


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
    vm_quota: Mapped[str] = mapped_column(Text, nullable=True)
    open: Mapped[bool] = mapped_column(Boolean, default=False)  # kept for SQLite compat; superseded by status
    status: Mapped[str] = mapped_column(String(16), default="draft")
    description: Mapped[str] = mapped_column(Text, nullable=True)
    welcome_message: Mapped[str] = mapped_column(Text, nullable=True)
    time_limit_minutes: Mapped[int] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    ends_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    # Semaphore project created once per event, reused for all VM provisions
    semaphore_project_id: Mapped[int] = mapped_column(Integer, nullable=True)
    semaphore_key_id: Mapped[int] = mapped_column(Integer, nullable=True)

    users: Mapped[list["User"]] = relationship(back_populates="event")
    teams: Mapped[list["Team"]] = relationship(back_populates="event")
    vms: Mapped[list["VM"]] = relationship(back_populates="event")


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


class VM(Base):
    __tablename__ = "vms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hostname: Mapped[str] = mapped_column(String(256), nullable=True)
    ip_address: Mapped[str] = mapped_column(String(64), nullable=True)
    os: Mapped[str] = mapped_column(String(64), nullable=True, default="Ubuntu 22.04")
    status: Mapped[str] = mapped_column(String(16), default="registered")
    ssh_port: Mapped[int] = mapped_column(Integer, nullable=True, default=22)
    ssh_user: Mapped[str] = mapped_column(String(64), nullable=True, default="root")
    ssh_host_key: Mapped[str] = mapped_column(String(512), nullable=True)
    notes: Mapped[str] = mapped_column(Text, nullable=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # Provisioning state
    provision_step: Mapped[str] = mapped_column(String(32), nullable=True)
    provision_error: Mapped[str] = mapped_column(Text, nullable=True)
    semaphore_project_id: Mapped[int] = mapped_column(Integer, nullable=True)
    semaphore_task_id: Mapped[int] = mapped_column(Integer, nullable=True)
    agent_status: Mapped[str] = mapped_column(String(16), nullable=True)
    # Vultr cloud provisioning
    vultr_id: Mapped[str] = mapped_column(String(64), nullable=True)
    vultr_plan: Mapped[str] = mapped_column(String(64), nullable=True)
    vultr_region: Mapped[str] = mapped_column(String(16), nullable=True)
    cloudflare_record_id: Mapped[str] = mapped_column(String(64), nullable=True)
    vm_type: Mapped[str] = mapped_column(String(64), nullable=True)
    # Caldera attack tree cache
    attack_tree_json: Mapped[str] = mapped_column(Text, nullable=True)
    # Base type ID used when provisioned (e.g. "ubuntu_24_server")
    base_type: Mapped[str] = mapped_column(String(64), nullable=True)

    # VPC networking
    vpc_ip: Mapped[str] = mapped_column(String(45), nullable=True)
    # OPNsense admin password (firewall VMs only)
    admin_password: Mapped[str] = mapped_column(String(128), nullable=True)

    team: Mapped["Team"] = relationship(back_populates="vms")
    event: Mapped["Event"] = relationship(back_populates="vms")
    modules: Mapped[list["VMModule"]] = relationship(back_populates="vm", cascade="all, delete-orphan")
    goals: Mapped[list["VMGoal"]] = relationship(back_populates="vm", cascade="all, delete-orphan")


class PlatformSettings(Base):
    __tablename__ = "platform_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)


class VMModule(Base):
    __tablename__ = "vm_modules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vm_id: Mapped[int] = mapped_column(ForeignKey("vms.id"), nullable=False)
    module_id: Mapped[str] = mapped_column(String(64), nullable=False)
    module_type: Mapped[str] = mapped_column(String(32), nullable=False)
    difficulty: Mapped[str] = mapped_column(String(8), nullable=False)
    points: Mapped[int] = mapped_column(Integer, nullable=False)
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    # "preapplied" = blue team sees+fixes, "caldera" = red team exploits.
    # None for types where stage doesn't apply (hardening, application_*, goal).
    stage: Mapped[str] = mapped_column(String(16), nullable=True)

    vm: Mapped["VM"] = relationship(back_populates="modules")


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

