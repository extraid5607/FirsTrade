"""Groww Trade API provider (live prices, candles, option chain).

What this talks to (from Groww's public documentation, https://groww.in/trade-api/docs/curl):
  GET /v1/live-data/ltp            bulk last price, up to 50 symbols, segment CASH or FNO
  GET /v1/live-data/ohlc           bulk open/high/low/close (used for the previous close)
  GET /v1/historical/candles       candles
  GET /v1/option-chain/exchange/NSE/underlying/{und}?expiry_date=YYYY-MM-DD
Needs an access token (GROWW_ACCESS_TOKEN, expires daily at 06:00 IST) or an API key + secret.
Limits per the docs: 10 requests/second and 300/minute for live data. We stay well below.

STATUS: written from the docs and tested against saved sample replies only. It has NOT been run
against the real Groww servers (the build computer could not reach them). Field names that the
docs left vague are read tolerantly; anything unreadable raises ProviderError, never a bad price.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from datetime import date, datetime, timedelta

import httpx

from ..calendar_in import IST
from ..config import settings
from ..instruments import Instrument, Universe
from .base import (TF_SECONDS, AuthExpired, Blocked, Candle, Provider, ProviderError, Quote,
                   summarize_chain)
from .util import clean_candles, num, parse_ohlc, parse_ts

log = logging.getLogger("groww")
BASE = "https://api.groww.in"
INSTRUMENT_CSV = "https://growwapi-assets.groww.in/instruments/instrument.csv"
INTERVAL = {"1m": "1minute", "5m": "5minute", "15m": "15minute", "1h": "1hour", "1d": "1day"}
# Longest span one request may cover (days), from the docs.
MAX_SPAN_DAYS = {"1m": 30, "5m": 30, "15m": 90, "1h": 180, "1d": 180}
# Calendar days of history to ask for, to get roughly 300 candles back.
SPAN_FOR_300 = {"1m": 4, "5m": 10, "15m": 30, "1h": 120, "1d": 180}


class _Limiter:
    """At most `per_sec` calls per second and `per_min` per minute."""

    def __init__(self, per_sec: float = 6, per_min: int = 200):
        self.gap = 1.0 / per_sec
        self.per_min = per_min
        self.hits: list[float] = []
        self._next = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            self.hits = [h for h in self.hits if now - h < 60]
            if len(self.hits) >= self.per_min:
                await asyncio.sleep(max(0.0, 60 - (now - self.hits[0])))
            delay = self._next - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            self._next = time.monotonic() + self.gap
            self.hits.append(time.monotonic())


class GrowwProvider(Provider):
    name = "groww"

    def __init__(self, universe: Universe, client: httpx.AsyncClient | None = None) -> None:
        self.u = universe
        self._http = client or httpx.AsyncClient(timeout=10.0)
        self._token = settings.groww_access_token
        self._token_try_at = 0.0
        self._limit = _Limiter()
        self._prev: dict[str, tuple[float, float]] = {}       # id -> (prev close, fetched at)
        self._prev_at = 0.0
        self.last_error: str | None = None
        self.last_ok: float | None = None

    @property
    def configured(self) -> bool:
        return bool(self._token or (settings.groww_api_key and settings.groww_api_secret))

    async def stop(self) -> None:
        await self._http.aclose()

    def set_token(self, token: str) -> None:
        """Install today's token without a restart (admin page). Kept in memory only."""
        self._token = token.strip()
        self._token_try_at = 0.0
        self.last_error = None

    # ---- plumbing --------------------------------------------------------------
    async def _ensure_token(self) -> None:
        if self._token:
            return
        if not (settings.groww_api_key and settings.groww_api_secret):
            raise AuthExpired("No Groww token. Set GROWW_ACCESS_TOKEN (see README).")
        if time.time() < self._token_try_at:
            raise AuthExpired("Groww token request failed recently; waiting before trying again.")
        self._token_try_at = time.time() + 1800     # the token endpoint is limited to 150/day
        ts = str(int(time.time()))
        checksum = hashlib.sha256((settings.groww_api_secret + ts).encode()).hexdigest()
        try:
            r = await self._http.post(
                f"{BASE}/v1/token/api/access",
                headers={"Authorization": f"Bearer {settings.groww_api_key}",
                         "Content-Type": "application/json", "Accept": "application/json"},
                json={"key_type": "approval", "checksum": checksum, "timestamp": ts})
            body = r.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise AuthExpired(f"Groww token request failed: {type(exc).__name__}")
        token = (body.get("token") or (body.get("payload") or {}).get("token")) if isinstance(body, dict) else None
        if not token:
            raise AuthExpired("Groww did not give a token. The API key must be approved each day "
                              "on the Groww Cloud API Keys page.")
        self._token = token

    async def _get(self, path: str, params: dict | None = None) -> dict:
        await self._ensure_token()
        await self._limit.wait()
        try:
            r = await self._http.get(BASE + path, params=params, headers={
                "Accept": "application/json", "Authorization": f"Bearer {self._token}",
                "X-API-VERSION": "1.0"})
        except httpx.HTTPError as exc:
            self.last_error = f"network: {type(exc).__name__}"
            raise ProviderError(self.last_error)
        if r.status_code == 401:
            self._token = ""
            self.last_error = "token rejected (expired?)"
            raise AuthExpired("Groww rejected the token. Tokens expire daily at 06:00 IST.")
        if r.status_code in (403, 451):
            self.last_error = f"blocked HTTP {r.status_code}"
            raise Blocked(f"Groww refused the request (HTTP {r.status_code}).")
        if r.status_code == 429:
            self.last_error = "rate limited"
            raise ProviderError("Groww rate limit reached; backing off.")
        if r.status_code >= 400:
            self.last_error = f"HTTP {r.status_code}"
            raise ProviderError(f"Groww HTTP {r.status_code}")
        try:
            body = r.json()
        except ValueError:
            raise ProviderError("Groww reply was not JSON")
        if not isinstance(body, dict) or body.get("status") not in (None, "SUCCESS"):
            err = (body.get("error") or {}) if isinstance(body, dict) else {}
            self.last_error = f"{err.get('code', '?')}: {err.get('message', 'failure')}"[:120]
            raise ProviderError(f"Groww error {self.last_error}")
        self.last_error = None
        self.last_ok = time.time()
        return body

    @staticmethod
    def exchange_symbol(inst: Instrument) -> str:
        if inst.groww_symbol and inst.segment == "CASH":
            return inst.groww_symbol.replace("-", "_")
        return f"NSE_{inst.id}"

    # ---- quotes ----------------------------------------------------------------
    async def quotes(self, insts: list[Instrument]) -> dict[str, Quote]:
        out: dict[str, Quote] = {}
        for seg in ("CASH", "FNO"):
            group = [i for i in insts if i.segment == seg]
            for k in range(0, len(group), 50):
                chunk = group[k:k + 50]
                by_sym = {self.exchange_symbol(i): i for i in chunk}
                body = await self._get("/v1/live-data/ltp", {
                    "segment": seg, "exchange_symbols": ",".join(by_sym)})
                payload = body.get("payload") or {}
                now = time.time()
                for sym, raw in payload.items():
                    inst = by_sym.get(sym)
                    px = num(raw.get("ltp") or raw.get("last_price")) if isinstance(raw, dict) else num(raw)
                    if inst is None or px is None or px <= 0:
                        continue
                    prev = self._prev.get(inst.id)
                    out[inst.id] = Quote(price=px, ts=now, source="groww",
                                         prev_close=prev[0] if prev else None)
        await self._refresh_prev([i for i in insts if i.segment == "CASH"])
        for iid, q in out.items():
            prev = self._prev.get(iid)
            if prev and q.prev_close is None:
                q.prev_close = prev[0]
        return out

    async def _refresh_prev(self, cash: list[Instrument]) -> None:
        """Previous close for the day-change % (one bulk OHLC call per 50, once a minute)."""
        if not cash or time.time() - self._prev_at < 60:
            return
        self._prev_at = time.time()
        for k in range(0, len(cash), 50):
            chunk = cash[k:k + 50]
            by_sym = {self.exchange_symbol(i): i for i in chunk}
            try:
                body = await self._get("/v1/live-data/ohlc", {
                    "segment": "CASH", "exchange_symbols": ",".join(by_sym)})
            except ProviderError:
                return
            for sym, raw in (body.get("payload") or {}).items():
                o = parse_ohlc(raw)
                inst = by_sym.get(sym)
                if o and inst:
                    self._prev[inst.id] = (o["close"], time.time())

    # ---- candles ---------------------------------------------------------------
    async def candles(self, inst: Instrument, tf: str, limit: int) -> list[Candle]:
        if tf not in INTERVAL:
            raise ProviderError("unsupported timeframe")
        end = datetime.now(IST)
        span = min(MAX_SPAN_DAYS[tf], max(1, int(SPAN_FOR_300[tf] * max(limit, 50) / 300) + 1))
        start = end - timedelta(days=span)
        body = await self._get("/v1/historical/candles", {
            "exchange": "NSE", "segment": inst.segment,
            "groww_symbol": inst.groww_symbol or f"NSE-{inst.id}",
            "start_time": start.strftime("%Y-%m-%d %H:%M:%S"),
            "end_time": end.strftime("%Y-%m-%d %H:%M:%S"),
            "candle_interval": INTERVAL[tf]})
        return parse_candles(body)[-limit:]

    # ---- option chain ----------------------------------------------------------
    async def option_chain(self, und: str, expiry: date) -> dict:
        body = await self._get(f"/v1/option-chain/exchange/NSE/underlying/{und}",
                               {"expiry_date": expiry.isoformat()})
        return parse_chain(body, und, expiry, self.u.lot_size(und))

    def status(self) -> dict:
        return {"name": "groww", "configured": self.configured, "has_token": bool(self._token),
                "last_error": self.last_error, "last_ok": self.last_ok}


# ---------------------------------------------------------------------- pure parsers (tested)
def parse_candles(body: dict) -> list[Candle]:
    payload = body.get("payload", body) if isinstance(body, dict) else {}
    raw = payload.get("candles") if isinstance(payload, dict) else None
    if not isinstance(raw, list):
        raise ProviderError("Groww candles: no 'candles' list in the reply")
    rows = []
    for c in raw:
        if not isinstance(c, (list, tuple)) or len(c) < 5:
            continue
        rows.append([parse_ts(c[0]), num(c[1]), num(c[2]), num(c[3]), num(c[4]),
                     num(c[5]) if len(c) > 5 else 0])
    rows = clean_candles(rows)
    if not rows:
        raise ProviderError("Groww candles: empty")
    return rows


def _leg(raw: dict | None) -> dict | None:
    if not isinstance(raw, dict):
        return None
    g = raw.get("greeks") if isinstance(raw.get("greeks"), dict) else {}
    ltp = num(raw.get("ltp") if raw.get("ltp") is not None else raw.get("last_price"))
    return {"id": raw.get("trading_symbol"), "ltp": ltp,
            "bid": num(raw.get("bid_price")), "ask": num(raw.get("ask_price")),
            "oi": num(raw.get("open_interest")), "volume": num(raw.get("volume")),
            "iv": _iv(num(g.get("iv"))), "delta": num(g.get("delta")), "gamma": num(g.get("gamma")),
            "theta": num(g.get("theta")), "vega": num(g.get("vega"))}


def _iv(v: float | None) -> float | None:
    """Percent. Groww's unit is not documented: a value below 3 is read as a fraction (0.14)."""
    if v is None:
        return None
    return v * 100 if v < 3 else v


def parse_chain(body: dict, und: str, expiry: date, lot_size: int) -> dict:
    payload = body.get("payload", body) if isinstance(body, dict) else {}
    strikes = payload.get("strikes") if isinstance(payload, dict) else None
    if not isinstance(strikes, dict) or not strikes:
        raise ProviderError("Groww option chain: no 'strikes' in the reply")
    ltp = num(payload.get("underlying_ltp"))
    rows = []
    for k, legs in strikes.items():
        strike = num(k)
        if strike is None or not isinstance(legs, dict):
            continue
        row = {"strike": strike}
        for key in ("CE", "PE"):
            leg = _leg(legs.get(key))
            if leg and leg["ltp"] is not None:
                row[key.lower()] = leg
        if "ce" in row or "pe" in row:
            rows.append(row)
    rows.sort(key=lambda r: r["strike"])
    if not rows or ltp is None:
        raise ProviderError("Groww option chain: could not read strikes or underlying price")
    return {"underlying": und, "expiry": expiry.isoformat(), "underlying_ltp": ltp,
            "lot_size": lot_size, "rows": rows, "source": "groww",
            "summary": summarize_chain(rows, ltp)}
