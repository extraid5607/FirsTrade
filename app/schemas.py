"""Request bodies. Strict on purpose: nothing from the browser is trusted."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

Positive = Annotated[float, Field(gt=0, lt=1e12, allow_inf_nan=False)]
Tag = Annotated[str, Field(max_length=24)]


class Credentials(BaseModel):
    username: str = Field(max_length=20)
    password: str = Field(max_length=200)
    invite: str = Field("", max_length=64)


class OrderReq(BaseModel):
    symbol: str = Field(max_length=40)
    side: Literal["long", "short"]            # long = BUY, short = SELL
    product: Literal["CNC", "MIS", "NRML"]
    type: Literal["market", "limit"] = "market"
    qty: Annotated[float, Field(gt=0, lt=1e9, allow_inf_nan=False)] | None = None
    lots: Annotated[int, Field(gt=0, le=10000)] | None = None
    risk_pct: Annotated[float, Field(gt=0, le=100, allow_inf_nan=False)] | None = None
    limit_price: Positive | None = None
    sl: Positive | None = None
    tp: Positive | None = None
    note: str = Field("", max_length=500)
    setup: str = Field("", max_length=40)
    tags: list[Tag] = Field(default_factory=list, max_length=8)


class StopsReq(BaseModel):
    """Both values are applied as sent; null removes that stop."""
    sl: Positive | None = None
    tp: Positive | None = None


class JournalReq(BaseModel):
    note: str | None = Field(None, max_length=2000)
    setup: str | None = Field(None, max_length=40)
    emotion: str | None = Field(None, max_length=20)
    lesson: str | None = Field(None, max_length=2000)
    rating: int | None = Field(None, ge=1, le=5)
    tags: list[Tag] | None = Field(None, max_length=8)


class SettingsReq(BaseModel):
    require_sl: bool | None = None
    max_risk_pct: float | None = Field(None, ge=0.1, le=100, allow_inf_nan=False)
    daily_loss_pct: float | None = Field(None, ge=0, le=100, allow_inf_nan=False)


class ResetReq(BaseModel):
    confirm: bool = False


class TokenReq(BaseModel):
    token: str = Field(min_length=10, max_length=4000)
