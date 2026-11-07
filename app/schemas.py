"""Request models for the internal and admin APIs."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HandoffRequest(StrictModel):
    account_id: str
    service_code: str


class HandoffExchangeRequest(StrictModel):
    token: str = Field(min_length=10, max_length=4096)


class ConsumeRequest(StrictModel):
    account_id: str
    kind: str = Field(min_length=1, max_length=32)
    quantity: int = Field(default=1, ge=1, le=1000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class InitDataRequest(StrictModel):
    init_data: str = Field(min_length=10, max_length=8192)


class ServiceCreate(StrictModel):
    code: str = Field(min_length=1, max_length=32, pattern=r"^[a-z0-9_]+$")
    name: str = Field(min_length=1, max_length=128)
    description: str = ""
    emoji: str | None = None
    status: str = "draft"
    webapp_url: str | None = None
    handoff_url: str | None = None
    events_url: str | None = None
    events_secret: str | None = None
    terms_url: str | None = None
    privacy_url: str | None = None
    support_email: str | None = None
    support_url: str | None = None
    platform_fee_bps: int = Field(default=0, ge=0, le=10000)
    sort_order: int = 0
    translations: dict[str, dict] = Field(default_factory=dict)


class ServicePatch(StrictModel):
    code: str | None = Field(default=None, min_length=1, max_length=32, pattern=r"^[a-z0-9_]+$")
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None
    emoji: str | None = None
    status: str | None = None
    webapp_url: str | None = None
    handoff_url: str | None = None
    events_url: str | None = None
    events_secret: str | None = None
    terms_url: str | None = None
    privacy_url: str | None = None
    support_email: str | None = None
    support_url: str | None = None
    platform_fee_bps: int | None = Field(default=None, ge=0, le=10000)
    sort_order: int | None = None
    translations: dict[str, dict] | None = None


class ProductCreate(StrictModel):
    code: str = Field(min_length=1, max_length=32, pattern=r"^[a-z0-9_]+$")
    name: str = Field(min_length=1, max_length=128)
    description: str = ""
    price_stars: int = Field(ge=1)
    entitlement_kind: str = Field(min_length=1, max_length=32)
    entitlement_quantity: int = Field(ge=1)
    entitlement_ttl_days: int | None = Field(default=None, ge=1)
    active: bool = True
    sort_order: int = 0
    translations: dict[str, dict] = Field(default_factory=dict)


class ProductPatch(StrictModel):
    code: str | None = Field(default=None, min_length=1, max_length=32, pattern=r"^[a-z0-9_]+$")
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None
    price_stars: int | None = Field(default=None, ge=1)
    entitlement_kind: str | None = Field(default=None, min_length=1, max_length=32)
    entitlement_quantity: int | None = Field(default=None, ge=1)
    entitlement_ttl_days: int | None = Field(default=None, ge=1)
    active: bool | None = None
    sort_order: int | None = None
    translations: dict[str, dict] | None = None


class AdjustRequest(StrictModel):
    delta: int
    reason: str = "adjust"


class TokenCreate(StrictModel):
    scopes: list[str] | None = None


class AdminKeyCreate(StrictModel):
    name: str = Field(min_length=1, max_length=128)
    role: str
    service_id: str | None = None
    token: str | None = Field(default=None, min_length=16, max_length=256)


class RefundRequest(StrictModel):
    reason: str | None = None
