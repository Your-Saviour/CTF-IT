from pydantic import BaseModel


class UserCreate(BaseModel):
    username: str
    password: str


class UserLogin(BaseModel):
    username: str
    password: str


class SnapshotPayload(BaseModel):
    user_id: str
    flag: str
    build_state: dict
    file_permissions: dict
    file_contents: dict
    services: dict
    packages: list[str]
    listening_ports: list[int]
    shadow_hashes: dict
