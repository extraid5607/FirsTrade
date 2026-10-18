"""NSE website option chain (public pages' own JSON), used when Groww's chain is unavailable.

This is NOT an official API. NSE's site only answers browser-like requests that carry the cookies
from its home page, and it very often refuses requests from cloud servers. This provider:
  * makes one normal visit to the home page for cookies (as any browser does), then asks for the chain;
  * on any refusal (401/403/429...) stops and waits 10 minutes; it never retries harder, never
    changes identity, never uses proxies or mirrors;
  * caches each chain for 30 seconds so it sends very few requests.
If NSE says no, the chain simply comes from another source or shows "unavailable".

STATUS: written from the commonly seen response shape; tested on a saved sample only, never live."""
from __future__ import annotations

import logging
import time
from datetime import date, datetime

import httpx

from .. import bs
from ..calendar_in import IST
from ..instruments import Universe
from .base import Blocked, Provider, ProviderError, summarize_chain
from .util import num

log = logging.getLogger("nse")
HOME = "https://www.nseindia.com/option-chain"
INDICES = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9",
    "Referer": HOME,
}


class NseProvider(Provider):
    name = "nse"
    COOLDOWN = 600
    CACHE = 30

    def __init__(self, universe: Universe, client: httpx.AsyncClient | None = None) -> None:
        self.u = universe
        self._http = client or httpx.AsyncClient(timeout=10.0, headers=HEADERS, follow_redirects=True)
        self._blocked_until = 0.0
        self._cookie_at = 0.0
        self._cache: dict[tuple[str, date], tuple[float, dict]] = {}
        self.last_error: str | None = None
        self.last_ok: float | None = None

    async def stop(self) -> None:
        await self._http.aclose()

    async def _cookies(self) -> None:
        if time.time() - self._cookie_at < 300:
            return
        r = await self._http.get(HOME)
        if r.status_code in (401, 403, 429):
            raise Blocked(f"NSE refused the home page (HTTP {r.status_code})")
        self._cookie_at = time.time()

    async def option_chain(self, und: str, expiry: date) -> dict:
        hit = self._cache.get((und, expiry))
        if hit and time.time() - hit[0] < self.CACHE:
            return hit[1]
        if time.time() < self._blocked_until:
            raise Blocked(self.last_error or "NSE is cooling down after a refusal")
        kind = "Indices" if und in INDICES else "Equity"
        try:
            await self._cookies()
            r = await self._http.get("https://www.nseindia.com/api/option-chain-v3",
                                     params={"type": kind, "symbol": und,
                                             "expiry": f"{expiry:%d-%b-%Y}"})
            if r.status_code == 404:        # older route
                path = "option-chain-indices" if und in INDICES else "option-chain-equities"
                r = await self._http.get(f"https://www.nseindia.com/api/{path}", params={"symbol": und})
        except httpx.HTTPError as exc:
            self.last_error = f"network: {type(exc).__name__}"
            raise ProviderError(self.last_error)
        except Blocked as exc:
            self._blocked_until = time.time() + self.COOLDOWN
            self.last_error = str(exc)
            raise
        if r.status_code in (401, 403, 429):
            self._blocked_until = time.time() + self.COOLDOWN
            self.last_error = f"NSE refused the request (HTTP {r.status_code})"
            raise Blocked(self.last_error)
        if r.status_code >= 400:
            self.last_error = f"HTTP {r.status_code}"
            raise ProviderError(self.last_error)
        try:
            body = r.json()
        except ValueError:
            self.last_error = "reply was not JSON (NSE often sends a web page instead)"
            raise ProviderError(self.last_error)
        out = parse_chain(body, und, expiry, self.u.lot_size(und))
        self.last_error, self.last_ok = None, time.time()
        self._cache[(und, expiry)] = (time.time(), out)
        return out

    def status(self) -> dict:
        return {"name": "nse", "last_error": self.last_error, "last_ok": self.last_ok,
                "cooling_down": time.time() < self._blocked_until}


def _expiry_of(text: str) -> date | None:
    try:
        return datetime.strptime(text, "%d-%b-%Y").date()
    except (TypeError, ValueError):
        return None


def parse_chain(body: dict, und: str, expiry: date, lot_size: int) -> dict:
    """Unified chain from NSE's JSON. Accepts both layouts seen: {"records": {"data": [...],
    "underlyingValue": ...}} and {"filtered": {"data": [...]}}. NSE gives IV but no Greeks, so
    delta/gamma/theta/vega are computed here from that IV with Black-Scholes."""
    rec = (body.get("records") if isinstance(body, dict) else None) or {}
    data = rec.get("data") or ((body.get("filtered") or {}).get("data") if isinstance(body, dict) else None)
    if not isinstance(data, list) or not data:
        raise ProviderError("NSE option chain: no data rows in the reply")
    spot = num(rec.get("underlyingValue"))
    if spot is None:
        for d in data:
            for leg in (d.get("CE"), d.get("PE")):
                if isinstance(leg, dict) and num(leg.get("underlyingValue")):
                    spot = num(leg["underlyingValue"])
                    break
            if spot:
                break
    if spot is None:
        raise ProviderError("NSE option chain: no underlying price")
    t = bs.years_to_expiry(expiry, datetime.now(IST))
    rows = []
    for d in data:
        if not isinstance(d, dict):
            continue
        e = _expiry_of(d.get("expiryDate") or d.get("expiryDates"))
        if e is not None and e != expiry:
            continue
        strike = num(d.get("strikePrice"))
        if strike is None:
            continue
        row = {"strike": strike}
        for key, ot in (("CE", "CE"), ("PE", "PE")):
            leg = d.get(key)
            if not isinstance(leg, dict) or num(leg.get("lastPrice")) is None:
                continue
            iv = num(leg.get("impliedVolatility"))
            g = bs.greeks(ot, spot, strike, t, iv / 100) if iv and iv > 0 and t > 0 else {}
            row[key.lower()] = {
                "id": None, "ltp": num(leg.get("lastPrice")), "bid": num(leg.get("bidprice")),
                "ask": num(leg.get("askPrice")), "oi": num(leg.get("openInterest")),
                "volume": num(leg.get("totalTradedVolume")), "iv": iv if iv else None,
                "delta": g.get("delta"), "gamma": g.get("gamma"), "theta": g.get("theta"),
                "vega": g.get("vega")}
        if "ce" in row or "pe" in row:
            rows.append(row)
    rows.sort(key=lambda r: r["strike"])
    if not rows:
        raise ProviderError("NSE option chain: nothing for this expiry")
    return {"underlying": und, "expiry": expiry.isoformat(), "underlying_ltp": spot,
            "lot_size": lot_size, "rows": rows, "source": "nse",
            "summary": summarize_chain(rows, spot)}
