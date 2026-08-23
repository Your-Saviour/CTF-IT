from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Phase(StrictModel):
    number: int = Field(ge=0)
    time_range: str = Field(min_length=1, max_length=200)
    current: bool = False


class InboxMessage(StrictModel):
    id: str = Field(min_length=1, max_length=100)
    sender: str = Field(min_length=1, max_length=200)
    subject: str = Field(min_length=1, max_length=300)
    preview: str = Field(min_length=1, max_length=1000)
    body: list[str] = Field(min_length=1)
    sent_at: str
    display_time: str = Field(min_length=1, max_length=100)
    unread: bool

    @field_validator("sent_at")
    @classmethod
    def timestamp(cls, value: str) -> str:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value


class ScoringTeam(StrictModel):
    team: str = Field(min_length=1, max_length=100)
    defense: float = Field(ge=0)
    usability: float = Field(ge=0)
    availability: float = Field(ge=0)
    reverts: float = Field(ge=0)
    ctirep: float = Field(ge=0)
    sitrep: float = Field(ge=0)
    forensics: float = Field(ge=0)
    legal: float = Field(ge=0)
    stratcom: float = Field(ge=0)
    stratex: float = Field(ge=0)
    xpoints: float = Field(ge=0)
    collaboration: float = Field(ge=0)


class SpotReport(StrictModel):
    expo_id: str = Field(min_length=1, max_length=100)
    capability: str = Field(min_length=1, max_length=300)
    phase: str = Field(pattern=r"^phase-\d+$")
    change: Literal["WORSE", "UNCHANGED", "BETTER"]
    status: Literal["NORMAL", "AT RISK"]
    updated_time: str = Field(min_length=1, max_length=100)


class Network(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    ipv4: str | None = None
    ipv6: str | None = None

    @model_validator(mode="after")
    def address_required(self):
        if not self.ipv4 and not self.ipv6:
            raise ValueError("a network requires ipv4 or ipv6")
        return self


class AvailabilityCheck(StrictModel):
    interface: str
    name: str = Field(min_length=1, max_length=300)
    status: Literal["OK", "WARNING", "CRITICAL"]
    availability: str
    downtime: str
    team: str
    checked: str
    relative_change: str


class Availability(StrictModel):
    zone: str = Field(min_length=1, max_length=100)
    status: Literal["OK", "WARNING", "CRITICAL"]
    sla: str
    downtime: str
    changed: str
    relative_change: str
    interfaces: list[AvailabilityCheck] = Field(min_length=1)


class System(StrictModel):
    expo_id: str = Field(min_length=1, max_length=255)
    zones: list[str] = Field(default_factory=list)
    networks: list[Network] = Field(default_factory=list)
    os: str | None = Field(default=None, max_length=200)
    team: str | None = Field(default=None, max_length=100)
    team_name: str | None = Field(default=None, max_length=200)
    role: str | None = Field(default=None, max_length=100)
    system_aliases: list[str] = Field(default_factory=list)
    availability: Availability | None = None
    credential_ids: list[int] = Field(default_factory=list)


class Reply(StrictModel):
    id: int = Field(ge=1)
    author: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=2000)
    created_time: str
    actor_type: Literal["blue_team", "simulated_user", "exercise_staff"] = "exercise_staff"


class Ticket(StrictModel):
    ticket_id: str = Field(min_length=1, max_length=100)
    priority: Literal["Critical", "High", "Medium", "Low"]
    status: Literal["Open", "Investigating", "Resolved"]
    category: str = Field(min_length=1, max_length=200)
    subject: str = Field(min_length=1, max_length=300)
    reporter: str = Field(min_length=1, max_length=200)
    system_expo_id: str = Field(min_length=1, max_length=255)
    team: str = Field(min_length=1, max_length=100)
    opened: str
    age: str = Field(min_length=1, max_length=100)
    summary: str = Field(min_length=1, max_length=2000)
    replies: list[Reply]


class Service(StrictModel):
    name: str = Field(min_length=1, max_length=500)
    description: str = Field(min_length=1, max_length=1000)


class Credential(StrictModel):
    id: int = Field(ge=1)
    realm: str = Field(min_length=1, max_length=255)
    username: str = Field(min_length=1, max_length=255)
    password: str | None = Field(default=None, min_length=1, max_length=128, exclude=True)
    original_password: str | None = Field(default=None, min_length=1, max_length=128, exclude=True)
    updated_time: str
    services: list[Service] = Field(min_length=1)


class Infrastructure(StrictModel):
    systems: list[System]
    credentials: list[Credential]


class Collaboration(StrictModel):
    id: int = Field(ge=1)
    team_from: str
    team_to: str
    points: int = Field(ge=1, le=25, multiple_of=5)
    phase: str = Field(pattern=r"^phase-\d+$")
    reason: str = Field(min_length=1, max_length=2000)
    created_time: str = Field(min_length=1, max_length=100)


class ExpoData(StrictModel):
    phases: list[Phase]
    inbox: list[InboxMessage]
    scoring: list[ScoringTeam]
    spot_reports: list[SpotReport]
    ust: list[Ticket]
    collaboration_points: list[Collaboration]
    infrastructure: Infrastructure
