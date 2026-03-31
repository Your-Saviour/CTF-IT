from pydantic import BaseModel


class UserCreate(BaseModel):
    username: str
    password: str


class UserLogin(BaseModel):
    username: str
    password: str


class ModuleResult(BaseModel):
    module_id: str
    collected: dict


class VerifyPayload(BaseModel):
    user_id: str
    results: list[ModuleResult]
