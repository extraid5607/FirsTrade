"""Yahoo Finance public chart endpoint, used as a fallback for stock and index prices and charts.

Unofficial and sometimes delayed for NSE. It never supplies futures or options (those need Groww or
the NSE chain), and the hub only trusts it when it agrees with the price already on screen."""
from __future__ import annotations

import asyncio
import logging
import time
from urllib.parse import quote as urlquote

import httpx

from ..config import settings
from ..instruments import Instrument
from .base import Blocked, Candle, Provider, ProviderError, Quote
from .util import clean_candles, num

log = logging.getLogger("yahoo")
BASE = "https://query1.finance.yahoo.com/v8/finance/chart/"
RANGES = {"1m": ("1m", "1d"), "5m": ("5m", "5d"), "15m": ("15m", "1mo"), "1h": ("60m", "3mo"),
          "1d": ("1d", "2y")}


class YahooProvider(Provider):
    name = "yahoo"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._http = client or httpx.AsyncClient(timeout=10.0, headers={
            "User-Agent": "Mozilla/5.0 (compatible; FirsTrade/1.0)"})
        self.last_error: str | None = None
        self.last_ok: float | None = None
        self._blocked_until = 0.0

    async def stop(self) -> None:
        await self._http.aclose()

    async def _chart(self, symbol: str, interval: str, rng: str) -> dict:
        if time.time() < self._blocked_until:
            raise Blocked("Yahoo is cooling down after a refusal")
        try:
            r = await self._http.get(BASE + urlquote(symbol, safe="^"),
                                     params={"interval": interval, "range": rng})
        except httpx.HTTPError as exc:
            self.last_error = f"network: {type(exc).__name__}"
            raise ProviderError(self.last_error)
        if r.status_code in (401, 403, 429):
            self._blocked_until = time.time() + 300    # we do not retry harder; just wait
            self.last_error = f"HTTP {r.status_code}"
            raise Blocked(f"Yahoo refused the request (HTTP {r.status_code})")
        if r.status_code >= 400:
            self.last_error = f"HTTP {r.status_code}"
            raise ProviderError(self.last_error)
        try:
            res = r.json()["chart"]["result"][0]
        except (ValueError, KeyError, IndexError, TypeError):
            raise ProviderError("Yahoo reply unreadable")
        self.last_ok = time.time()
        self.last_error = None
        return res

    async def quotes(self, insts: list[Instrument]) -> dict[str, Quote]:
        sem = asyncio.Semaphore(6)

        async def one(i: Instrument):
            if not i.yahoo or i.kind not in ("equity", "index"):
                return i.id, None
            async with sem:
                try:
                    return i.id, parse_quote(await self._chart(i.yahoo, "1m", "1d"))
                except ProviderError as exc:
                    log.warning("yahoo %s: %s", i.yahoo, exc)
                    return i.id, None
        results = await asyncio.gather(*(one(i) for i in insts))
        return {k: v for k, v in results if v}

    async def candles(self, inst: Instrument, tf: str, limit: int) -> list[Candle]:
        if not inst.yahoo or inst.kind not in ("equity", "index"):
            raise ProviderError("Yahoo has no data for this instrument")
        interval, rng = RANGES[tf]
        return parse_candles(await self._chart(inst.yahoo, interval, rng))[-limit:]

    def status(self) -> dict:
        return {"name": "yahoo", "last_error": self.last_error, "last_ok": self.last_ok}


def parse_quote(res: dict) -> Quote | None:
    meta = res.get("meta") or {}
    px = num(meta.get("regularMarketPrice"))
    if not px or px <= 0:
        return None
    prev = num(meta.get("chartPreviousClose")) or num(meta.get("previousClose"))
    ts = num(meta.get("regularMarketTime")) or time.time()
    return Quote(price=px, ts=min(ts, time.time()), prev_close=prev, source="yahoo")


def parse_candles(res: dict) -> list[Candle]:
    stamps = res.get("timestamp") or []
    q = ((res.get("indicators") or {}).get("quote") or [{}])[0]
    rows = []
    for i, t in enumerate(stamps):
        try:
            rows.append([float(t), num(q["open"][i]), num(q["high"][i]), num(q["low"][i]),
                         num(q["close"][i]), num((q.get("volume") or [0] * len(stamps))[i]) or 0])
        except (KeyError, IndexError, TypeError):
            continue
    rows = clean_candles(rows)
    if not rows:
        raise ProviderError("Yahoo candles empty")
    return rows
