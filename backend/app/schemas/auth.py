"""Authentication, user, API-key and organization schemas."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import UserRole
from app.schemas.common import CurrencyCode


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    role: UserRole
    is_active: bool
    last_login_at: datetime | None
    created_at: datetime
    locked: bool = False


class CurrentUserOut(BaseModel):
    """Who the server believes the caller is, and what that role may do. The
    frontend uses `permissions` only to decide what to *show*; every request
    is authorized again server-side."""

    id: uuid.UUID
    kind: str  # "user" | "apikey"
    email: str | None
    name: str
    role: UserRole
    permissions: list[str]
    organization_id: uuid.UUID
    organization_name: str | None
    # Scenario key when the organization is a fictional demonstration, else null.
    organization_demo_scenario: str | None = None
    csrf_token: str | None = None


class LoginResponse(BaseModel):
    """`access_token` is for API clients; the browser console ignores it and
    relies on the httpOnly session cookie plus `csrf_token`."""

    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    csrf_token: str
    user: CurrentUserOut


class AuthConfigOut(BaseModel):
    signup_enabled: bool
    signup_code_required: bool


class RegisterRequest(BaseModel):
    organization_name: str = Field(min_length=1, max_length=160)
    base_currency: CurrencyCode = "USD"
    email: str = Field(min_length=3, max_length=254)
    full_name: str = Field(min_length=1, max_length=160)
    password: str = Field(min_length=1, max_length=256)
    signup_code: str | None = Field(default=None, max_length=200)


class UserCreate(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    full_name: str = Field(min_length=1, max_length=160)
    password: str = Field(min_length=1, max_length=256)
    role: UserRole


class UserUpdate(BaseModel):
    role: UserRole | None = None
    is_active: bool | None = None
    full_name: str | None = Field(default=None, min_length=1, max_length=160)


class PasswordReset(BaseModel):
    new_password: str = Field(min_length=1, max_length=256)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class ApiKeyCreate(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    role: UserRole


class ApiKeyOut(BaseModel):
    id: uuid.UUID
    label: str
    role: UserRole
    key_prefix: str | None
    created_at: datetime
    created_by: str | None
    last_used_at: datetime | None
    revoked_at: datetime | None
    is_active: bool


class ApiKeyCreatedOut(ApiKeyOut):
    """Returned exactly once, at creation - the only time the key is shown."""

    key: str


class OrganizationOut(BaseModel):
    id: uuid.UUID
    name: str
    base_currency: str
    approval_threshold_minor: int | None
    demo_scenario: str | None = None


class OrganizationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    approval_threshold_minor: int | None = Field(default=None, gt=0, le=10**15)
    clear_approval_threshold: bool = False
