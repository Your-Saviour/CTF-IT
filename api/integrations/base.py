from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ConnectionTestResult:
    ok: bool
    code: str
    message: str


@dataclass(frozen=True)
class SyncResult:
    ok: bool
    code: str
    message: str
    http_status: int | None = None
    retryable: bool = False


class IntegrationAdapter(Protocol):
    key: str

    def validate_destination(self, destination) -> list[str]: ...

    async def test_connection(self, destination, secret: str) -> ConnectionTestResult: ...

    async def synchronize(self, binding, destination, secret: str) -> SyncResult: ...
