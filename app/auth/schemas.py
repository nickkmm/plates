"""The request and response bodies of the JSON auth API (app/auth/api.py)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class LoginRequest(BaseModel):
    # strict: a number or a list is not a username or a password
    model_config = ConfigDict(strict=True)

    username: str
    password: str


class UserOut(BaseModel):
    id: int
    username: str
    roles: list[str]


class LoginResponse(BaseModel):
    user: UserOut
    permissions: list[str]


class MeUser(BaseModel):
    id: int
    username: str


class MeResponse(BaseModel):
    user: MeUser
    permissions: list[str]
