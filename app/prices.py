"""Price hub: one place the engine and the website read prices from.

LIVE mode: stocks and indices are polled from Groww (fallback Yahoo). Futures and options are
polled from Groww only for contracts somebody holds, has an order on, or is looking at, plus
prices read from the option chain (Groww, fallback NSE). DEMO mode: everything is simulated.

Safety nets (a stop-loss or target must never fire on a wrong print):
  * a quote from a DIFFERENT source than the one in use must be close to the price we already
    have, otherwise it is rejected;
  * a big one-step jump from the SAME source is held until a second reading agrees;
  * an impossible price (zero, or 30% away from yesterday's close for stocks) is dropped.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from datetime import date, datetime

from . import bs
from .calendar_in import IST, MarketClock
from .config import settings
from .instruments import Instrument, Universe
from .providers.base import TF_SECONDS, Blocked, Provider, ProviderError, Quote
from .providers.demo import DemoProvider

log = logging.getLogger("prices")

MAX_SOURCE_GAP = {"equity": 0.015, "index": 0.008, "future": 0.012, "option": 2.0}
MAX_JUMP = {"equity": 0.10, "index": 0.04, "future": 0.06, "option": 2.0}
MAX_FROM_PREV = {"equity": 0.30, "index": 0.15, "future": 0.30}
CONFIRM_WITHIN = 60


class PriceHub:
    COOLDOWN = 45
    FAILS_BEFORE_SKIP = 2

    def __init__(self, universe: Universe, clock: MarketClock, demo: bool | None = None,
                 providers: dict[str, Provider] | None = None,
                 chain_providers: dict[str, Provider] | None = None):
        self.universe = universe
        self.clock = clock
        self.demo = settings.price_source == "demo" if demo is None else demo
        self.demo_feed: DemoProvider | None = DemoProvider(universe) if self.demo else None
        self.providers = providers or {}
        self.chain_providers = chain_providers or {}
        self.quotes: dict[str, Quote] = {}
        self.errors: dict[str, str] = {}
        self._origin: dict[str, str] = {}
        self._pending: dict[str, tuple[float, float]] = {}
        self._fails: dict[str, int] = {}
        self._skip_until: dict[str, float] = {}
        self._wanted: dict[str, float] = {}
        self._candle_cache: dict[tuple[str, str], tuple[float, list, int]] = {}
        self._chain_cache: dict[tuple[str, date], tuple[float, dict]] = {}
        self._tasks: list[asyncio.Task] = []
        self.universe_source = "demo" if self.demo else "unknown"

    # ---- lifecycle ----------------------------------------------------------------
    async def start(self) -> None:
        if self.demo:
            self._seed_demo()
            self._tasks = [asyncio.create_task(self._demo_loop())]
        else:
            self._tasks = [asyncio.create_task(self._cash_loop()),
                           asyncio.create_task(self._deriv_loop())]

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        for p in list(self.providers.values()) + list(self.chain_providers.values()):
            try:
                await p.stop()
            except Exception:  # pragma: no cover
                pass

    def set_universe(self, universe: Universe) -> None:
        self.universe = universe
        if self.demo_feed:
            self.demo_feed.u = universe
        for p in list(self.providers.values()) + list(self.chain_providers.values()):
            if hasattr(p, "u"):
                p.u = universe

    # ---- reading ------------------------------------------------------------------
    def want(self, ids, ttl: float = 90.0) -> None:
        """Ask the live feed to keep these (derivative) contracts' prices coming."""
        until = time.time() + ttl
        for i in ids:
            self._wanted[i] = max(self._wanted.get(i, 0), until)

    def last(self, iid: str) -> Quote | None:
        if not iid:
            return None
        if self.demo_feed:
            inst = self.universe.get(iid)
            if inst is None:
                return None
            if inst.kind in ("equity", "index"):
                return self.quotes.get(inst.id) or self.quotes.get(iid)
            return self.demo_feed.quote(inst)
        q = self.quotes.get(iid)
        if q is not None:
            return q
        inst = self.universe.get(iid)
        if inst and inst.id != iid:
            return self.quotes.get(inst.id)
        return None

    def fresh(self, iid: str) -> Quote | None:
        """The latest quote, or None when the market is closed or the feed is stale."""
        q = self.last(iid)
        if q is None:
            return None
        if not self.clock.is_open():
            return None
        if self.demo:
            return q
        return q if (time.time() - q.ts) <= settings.stale_after else None

    def underlying_price(self, inst: Instrument) -> float | None:
        q = self.last(inst.underlying)
        return q.price if q else None

    def snapshot(self) -> dict:
        out = {}
        for inst in self.universe.watchlist():
            q = self.last(inst.id)
            if q:
                out[inst.id] = {"p": q.price, "c": q.change_pct, "f": 1 if self.fresh(inst.id) else 0}
        return out

    def quote_view(self, ids) -> dict:
        out = {}
        for iid in ids:
            q = self.last(iid)
            if q:
                out[iid] = {"p": q.price, "c": q.change_pct, "f": 1 if self.fresh(iid) else 0,
                            "bid": q.bid, "ask": q.ask, "oi": q.oi}
        return out

    def status(self) -> dict:
        now = time.time()
        feeds = {name: p.status() for name, p in {**self.providers, **self.chain_providers}.items()}
        live_ids = [i.id for i in self.universe.watchlist()]
        fresh = sum(1 for i in live_ids if self.last(i) and (self.demo or now - self.last(i).ts <= settings.stale_after))
        return {"mode": "demo" if self.demo else "live", "feeds": feeds,
                "watchlist": len(live_ids), "with_price": sum(1 for i in live_ids if self.last(i)),
                "fresh": fresh, "universe": self.universe.counts(),
                "universe_source": self.universe_source,
                "universe_skipped": self.universe.skipped, "errors": dict(list(self.errors.items())[:10]),
                "sources": _count_sources(self.quotes)}

    # ---- guards -------------------------------------------------------------------
    def _accept(self, inst: Instrument, label: str, q: Quote) -> str:
        """"ok" (use it), "hold" (wait for a confirming second reading) or "reject"."""
        now = time.time()
        if q.price is None or q.price <= 0:
            return "reject"
        lim = MAX_FROM_PREV.get(inst.kind)
        if lim and q.prev_close and abs(q.price / q.prev_close - 1) > lim:
            log.warning("%s: rejected price %.2f, %.0f%% away from previous close %.2f",
                        inst.id, q.price, abs(q.price / q.prev_close - 1) * 100, q.prev_close)
            return "reject"
        cur = self.quotes.get(inst.id)
        if cur is None or now - cur.ts > settings.stale_after:
            self._take(inst, label, q)
            return "ok"
        move = abs(q.price / cur.price - 1)
        if self._origin.get(inst.id) != label and label not in ("angel", "nse", "groww"):
            if move > MAX_SOURCE_GAP[inst.kind]:
                log.warning("%s: rejected %s price %.2f, %.2f%% away from %s price %.2f", inst.id,
                            label, q.price, move * 100, self._origin.get(inst.id), cur.price)
                return "reject"
        elif move > MAX_JUMP[inst.kind]:
            held = self._pending.get(inst.id)
            if held and now - held[1] <= CONFIRM_WITHIN \
                    and abs(q.price / held[0] - 1) <= MAX_JUMP[inst.kind] / 4:
                log.warning("%s: %s jump to %.2f confirmed by a second reading", inst.id, label, q.price)
            else:
                self._pending[inst.id] = (q.price, now)
                log.warning("%s: holding %s reading %.2f (%.1f%% from %.2f)", inst.id, label,
                            q.price, move * 100, cur.price)
                return "hold"
        self._take(inst, label, q)
        return "ok"

    def _take(self, inst: Instrument, label: str, q: Quote) -> None:
        q.source = label
        if q.prev_close is None and inst.id in self.quotes:
            q.prev_close = self.quotes[inst.id].prev_close
        self.quotes[inst.id] = q
        self._origin[inst.id] = label
        self._pending.pop(inst.id, None)

    # ---- live polling -------------------------------------------------------------
    def _usable(self, name: str, p: Provider) -> bool:
        if self._skip_until.get(name, 0) > time.time():
            return False
        return bool(getattr(p, "configured", True))

    def _note(self, name: str, ok: bool, err: str = "") -> None:
        if ok:
            self._fails.pop(name, None)
            self._skip_until.pop(name, None)
            self.errors.pop(name, None)
            return
        n = self._fails.get(name, 0) + 1
        self._fails[name] = n
        self.errors[name] = err[:160]
        if n >= self.FAILS_BEFORE_SKIP:
            self._skip_until[name] = time.time() + self.COOLDOWN

    async def _poll_into(self, insts: list[Instrument], names: list[str]) -> None:
        """Fetch quotes for `insts`; each instrument uses the first source that gives a good price."""
        todo = list(insts)
        for name in names:
            p = self.providers.get(name)
            if not todo or p is None or not self._usable(name, p):
                continue
            try:
                got = await p.quotes(todo)
                self._note(name, True)
            except asyncio.CancelledError:
                raise
            except (Blocked, ProviderError) as exc:
                self._note(name, False, f"{name}: {exc}")
                continue
            except Exception as exc:  # never let a bug kill the feed
                log.exception("%s crashed", name)
                self._note(name, False, f"{name}: {type(exc).__name__}")
                continue
            rest = []
            for inst in todo:
                q = got.get(inst.id)
                if q is None:
                    rest.append(inst)
                    continue
                if self._accept(inst, name, q) == "reject":
                    rest.append(inst)
            todo = rest

    async def _cash_loop(self) -> None:
        while True:
            started = time.monotonic()
            try:
                cash = [i for i in self.universe.watchlist()]
                await self._poll_into(cash, list(settings.providers))
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("cash poll crashed")
            every = 2.0 if self.clock.is_open() else 30.0
            await asyncio.sleep(max(0.5, every - (time.monotonic() - started)))

    async def _deriv_loop(self) -> None:
        last_chain_fetch: dict[tuple[str, date], float] = {}
        while True:
            started = time.monotonic()
            try:
                now = time.time()
                self._wanted = {k: v for k, v in self._wanted.items() if v > now}
                insts = [self.universe.get(k) for k in self._wanted]
                insts = [i for i in insts if i and i.kind in ("future", "option")]

                # 1. Direct exchange provider poll (Angel One first, then Groww)
                direct_providers = [p for p in ["angel", "groww"] if p in self.providers and getattr(self.providers[p], "configured", False)]
                if insts and direct_providers:
                    await self._poll_into(insts, direct_providers)

                # 2. For option contracts, group by (underlying, expiry) and poll exchange option chain
                option_pairs = set(
                    (i.underlying, i.expiry)
                    for i in insts
                    if i.kind == "option" and i.underlying and i.expiry
                )
                for und, exp in option_pairs:
                    # Poll chain at most once every 10 seconds per expiry to respect exchange rate limits
                    if now - last_chain_fetch.get((und, exp), 0.0) >= 10.0:
                        last_chain_fetch[(und, exp)] = now
                        try:
                            await self.chain(und, exp)
                        except Exception as exc:
                            log.debug("deriv_loop chain %s %s failed: %s", und, exp, exc)

                # 3. Dynamic Underlying-Tracking (Fallback when no exchange quote is available)
                # Keep option and futures prices moving live in sync with the underlying's real-time spot
                for inst in insts:
                    spot = self.underlying_price(inst)
                    if spot is None or spot <= 0:
                        continue
                    cur_q = self.quotes.get(inst.id)
                    # Real exchange quote is authoritative: never overwrite with synthetic model
                    if cur_q and cur_q.source in ("angel", "nse", "groww") and (now - cur_q.ts) < settings.stale_after:
                        continue

                    t = bs.years_to_expiry(inst.expiry, datetime.now(IST)) if inst.expiry else 0.0
                    if inst.kind == "option" and inst.opt_type and inst.strike:
                        vol = getattr(cur_q, "iv", None) if cur_q else None
                        if vol and vol > 1.0:
                            vol = vol / 100.0
                        if not vol or vol <= 0:
                            if cur_q and cur_q.price > 0 and t > 0:
                                vol = bs.implied_vol(inst.opt_type, cur_q.price, spot, inst.strike, t)
                        if not vol or vol <= 0:
                            base_vol = getattr(inst, "vol", 0.15) or 0.15
                            vol = bs.smile_vol(base_vol, spot, inst.strike)

                        px = bs.price(inst.opt_type, spot, inst.strike, t, vol)
                        px = max(round(px / inst.tick) * inst.tick, inst.tick)
                        half = max(inst.tick, round(px * 0.01 / inst.tick) * inst.tick)
                        self.quotes[inst.id] = Quote(
                            price=px, ts=now,
                            prev_close=cur_q.prev_close if cur_q else None,
                            source="live_deriv",
                            bid=max(px - half, inst.tick), ask=px + half,
                            oi=cur_q.oi if cur_q else None
                        )
                        self._origin[inst.id] = "live_deriv"
                    elif inst.kind == "future":
                        px = round(spot * math.exp(bs.RATE * t) / inst.tick) * inst.tick
                        half = max(inst.tick, round(px * 0.0005 / inst.tick) * inst.tick)
                        self.quotes[inst.id] = Quote(
                            price=px, ts=now,
                            prev_close=cur_q.prev_close if cur_q else None,
                            source="live_deriv",
                            bid=px - half, ask=px + half
                        )
                        self._origin[inst.id] = "live_deriv"
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("derivative poll crashed")
            every = 1.5 if self.clock.is_open() else 30.0
            await asyncio.sleep(max(0.5, every - (time.monotonic() - started)))

    # ---- demo ---------------------------------------------------------------------
    def _seed_demo(self) -> None:
        now = time.time()
        for inst in self.universe.watchlist():
            self.quotes[inst.id] = self.demo_feed._quote(inst)
            self.quotes[inst.id].ts = now

    async def _demo_loop(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            try:
                self.demo_feed.step()
                for inst in self.universe.watchlist():
                    self.quotes[inst.id] = self.demo_feed._quote(inst)
            except Exception:
                log.exception("demo step failed")

    # ---- candles & option chain -----------------------------------------------------
    async def candles(self, iid: str, tf: str, limit: int = 300) -> list:
        inst = self.universe.get(iid)
        if inst is None:
            raise ValueError("unknown instrument")
        if tf not in TF_SECONDS:
            raise ValueError("bad timeframe")
        key = (iid, tf)
        hit = self._candle_cache.get(key)
        if hit and time.time() - hit[0] < 8 and hit[2] >= limit:
            return hit[1][-limit:]
        if self.demo_feed:
            rows = await self.demo_feed.candles(inst, tf, limit)
        else:
            rows, last_err = None, "no sources configured"
            names = list(settings.providers) if inst.kind in ("equity", "index") else ["groww"]
            for name in names:
                p = self.providers.get(name)
                if p is None or not getattr(p, "configured", True):
                    continue
                try:
                    rows = await p.candles(inst, tf, limit)
                    if rows:
                        break
                except ProviderError as exc:
                    last_err = f"{name}: {exc}"
            if not rows:
                raise RuntimeError(last_err)
        self._candle_cache[key] = (time.time(), rows, limit)
        return rows[-limit:]

    async def chain(self, und: str, expiry: date) -> dict:
        hit = self._chain_cache.get((und, expiry))
        if hit and time.time() - hit[0] < 4:
            return hit[1]
        if self.demo_feed:
            data = await self.demo_feed.option_chain(und, expiry)
        else:
            data, last_err = None, "no option-chain source is configured"
            for name in settings.chain_providers:
                p = self.chain_providers.get(name)
                if p is None or not getattr(p, "configured", True):
                    continue
                try:
                    data = await p.option_chain(und, expiry)
                    break
                except (Blocked, ProviderError) as exc:
                    last_err = f"{name}: {exc}"
            if data is None:
                raise RuntimeError(last_err)
            self._ingest_chain(data, expiry)
        self._fill_ids(data, und, expiry)
        self._chain_cache[(und, expiry)] = (time.time(), data)
        return data

    def _fill_ids(self, data: dict, und: str, expiry: date) -> None:
        for row in data.get("rows", []):
            for key, ot in (("ce", "CE"), ("pe", "PE")):
                leg = row.get(key)
                if leg is not None:
                    inst = self.universe.contract(und, expiry, row["strike"], ot)
                    if inst:
                        leg["id"] = inst.id

    def _ingest_chain(self, data: dict, expiry: date) -> None:
        """Use the chain's prices for contracts the direct feed is not giving us."""
        now = time.time()
        for row in data["rows"]:
            for key, ot in (("ce", "CE"), ("pe", "PE")):
                leg = row.get(key)
                if not leg or leg.get("ltp") is None:
                    continue
                inst = self.universe.contract(data["underlying"], expiry, row["strike"], ot)
                if inst is None:
                    continue
                cur = self.quotes.get(inst.id)
                if cur and now - cur.ts < 4 and self._origin.get(inst.id) == "groww":
                    continue
                q = Quote(price=leg["ltp"], ts=now, bid=leg.get("bid"),
                          ask=leg.get("ask"), oi=leg.get("oi"),
                          source=data["source"])
                if leg.get("iv"):
                    q.iv = leg["iv"]
                self._accept(inst, data["source"], q)


def _count_sources(quotes: dict[str, Quote]) -> dict:
    out: dict[str, int] = {}
    for q in quotes.values():
        out[q.source] = out.get(q.source, 0) + 1
    return out
