from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class AgentSession(Base):
    __tablename__ = "agent_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    ctf_event_id: Mapped[int] = mapped_column(Integer, nullable=False)
    ctf_vm_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    attack_tree_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_step: Mapped[int] = mapped_column(Integer, default=0)
    max_steps: Mapped[int] = mapped_column(Integer, default=100)
    approval_required: Mapped[bool] = mapped_column(Boolean, default=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    actions: Mapped[list["AgentAction"]] = relationship(back_populates="session", cascade="all, delete-orphan")
    logs: Mapped[list["AgentLog"]] = relationship(back_populates="session", cascade="all, delete-orphan")
    state_entities: Mapped[list["StateEntity"]] = relationship(back_populates="session", cascade="all, delete-orphan")


class AgentAction(Base):
    __tablename__ = "agent_actions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("agent_sessions.id"), nullable=False)
    action_type: Mapped[str] = mapped_column(String(32), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(16), default="medium")
    description: Mapped[str] = mapped_column(Text, nullable=False)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False)
    target: Mapped[str | None] = mapped_column(String(256), nullable=True)
    tool: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tool_args_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_node_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    session: Mapped["AgentSession"] = relationship(back_populates="actions")


class AgentLog(Base):
    __tablename__ = "agent_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("agent_sessions.id"), nullable=False)
    level: Mapped[str] = mapped_column(String(16), default="info")
    component: Mapped[str] = mapped_column(String(32), default="agent")
    message: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    session: Mapped["AgentSession"] = relationship(back_populates="logs")


class StateEntity(Base):
    __tablename__ = "state_entities"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("agent_sessions.id"), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    data_json: Mapped[str] = mapped_column(Text, nullable=False)
    source_node_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    session: Mapped["AgentSession"] = relationship(back_populates="state_entities")


class RateLimitRecord(Base):
    __tablename__ = "rate_limit_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    request_count: Mapped[int] = mapped_column(Integer, default=0)
    last_request_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "key": self.key,
            "request_count": self.request_count,
            "last_request_at": self.last_request_at.isoformat() if self.last_request_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class SessionError(Base):
    """Track errors for better error feedback."""
    __tablename__ = "session_errors"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("agent_sessions.id"), nullable=False)
    error_type: Mapped[str] = mapped_column(String(32), nullable=False)
    error_message: Mapped[str] = mapped_column(Text, nullable=False)
    error_context: Mapped[str | None] = mapped_column(Text, nullable=True)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    operation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "error_type": self.error_type,
            "error_message": self.error_message,
            "error_context": self.error_context,
            "severity": self.severity,
            "operation_id": self.operation_id,
            "action_id": self.action_id,
            "resolved": self.resolved,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
