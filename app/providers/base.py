"""Shared types for price providers (demo, Groww, Yahoo, NSE)."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..instruments import Instrument

TF_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "1d": 86400}
Candle = list      # [epoch seconds, open, high, low, close, volume]


class ProviderError(Exception):
    """The source answered badly or not at all."""


class AuthExpired(ProviderError):
    """The Groww token is missing or expired (they expire daily at 06:00 IST)."""


class Blocked(ProviderError):
    """The site refused us (HTTP 401/403/429...). We never try to get around that."""


@dataclass
class Quote:
    price: float
    ts: float                       # when the price was observed (epoch seconds)
    prev_close: float | None = None
    source: str = ""
    bid: float | None = None
    ask: float | None = None
    oi: float | None = None
    volume: float | None = None

    @property
    def change_pct(self) -> float | None:
        if self.prev_close:
            return (self.price / self.prev_close - 1) * 100
        return None


class Provider:
    name = "base"

    async def start(self) -> None:  # pragma: no cover - trivial
        pass

    async def stop(self) -> None:  # pragma: no cover - trivial
        pass

    async def quotes(self, insts: list[Instrument]) -> dict[str, Quote]:
        raise NotImplementedError

    async def candles(self, inst: Instrument, tf: str, limit: int) -> list[Candle]:
        raise NotImplementedError

    async def option_chain(self, und: str, expiry) -> dict:
        raise NotImplementedError

    def status(self) -> dict:
        return {"name": self.name}


def summarize_chain(rows: list[dict], underlying_ltp: float) -> dict:
    """ATM strike, put/call ratio and max-pain strike from chain rows.

    rows: [{"strike": K, "ce": {"oi":..}, "pe": {"oi":..}}]. Max pain = the strike where option
    writers would pay out the least at expiry."""
    if not rows:
        return {"atm": None, "pcr": None, "max_pain": None, "ce_oi": 0, "pe_oi": 0}
    strikes = [r["strike"] for r in rows]
    atm = min(strikes, key=lambda k: abs(k - underlying_ltp))
    ce_oi = sum((r.get("ce") or {}).get("oi") or 0 for r in rows)
    pe_oi = sum((r.get("pe") or {}).get("oi") or 0 for r in rows)
    best, best_pain = None, None
    for k in strikes:
        pain = 0.0
        for r in rows:
            s = r["strike"]
            pain += max(k - s, 0) * ((r.get("ce") or {}).get("oi") or 0)
            pain += max(s - k, 0) * ((r.get("pe") or {}).get("oi") or 0)
        if best_pain is None or pain < best_pain:
            best, best_pain = k, pain
    return {"atm": atm, "pcr": (pe_oi / ce_oi) if ce_oi else None, "max_pain": best,
            "ce_oi": ce_oi, "pe_oi": pe_oi}
