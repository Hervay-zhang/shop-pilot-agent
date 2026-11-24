from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class BuyerChatIn(BaseModel):
    message: str = Field(min_length=2, max_length=500)
    conversation_id: str | None = None


class OrderConfirmIn(BaseModel):
    draft_id: str
    confirmed: bool = True
    request_id: str = Field(min_length=4, max_length=64)
    shipping_address: str = Field(min_length=6, max_length=240)
    proof_token: str | None = None


class ServiceRequestIn(BaseModel):
    request_type: Literal["refund", "change_address", "agent_order"]
    order_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ServiceConfirmIn(BaseModel):
    confirmed: bool = True


class ChangeOrderIn(BaseModel):
    kind: Literal["price_change", "campaign", "budget_adjustment"]
    sku: str
    new_price_cents: int | None = Field(default=None, gt=0)
    campaign_budget_cents: int | None = Field(default=None, gt=0)
    effective_from: str | None = None
    effective_to: str | None = None


class ToolCallIn(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolSearchArgs(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    category: str | None = None
    max_price_cents: int | None = Field(default=None, gt=0)
    limit: int = Field(default=8, ge=1, le=20)


class ToolProductArgs(BaseModel):
    sku: str


class ToolCartArgs(BaseModel):
    sku: str
    quantity: int = Field(default=1, ge=1, le=20)
    idempotency_key: str | None = None


class ToolAfterSalesArgs(BaseModel):
    order_id: str
    reason: str = Field(min_length=2, max_length=300)


class ToolMetricsArgs(BaseModel):
    sku: str | None = None
    days: int = Field(default=7, ge=1, le=30)
    grain: Literal["day", "week"] = "day"


class ToolActionArgs(BaseModel):
    kind: Literal["price_change", "campaign", "budget_adjustment"]
    sku: str
    new_price_cents: int | None = Field(default=None, gt=0)
    campaign_budget_cents: int | None = Field(default=None, gt=0)


class ToolChangeArgs(BaseModel):
    change_order_id: str
    action: Literal["approve", "execute", "rollback"]
