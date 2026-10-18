"""Simulated Indian market: random-walk stocks and indices, futures with cost of carry, and
options priced by Black-Scholes with a volatility smile. Works with no internet.

Everything about a derivative is computed on demand from its underlying's current price, so the
whole universe (thousands of contracts) stays consistent without being updated every second."""
from __future__ import annotations

import math
import random
import time
from datetime import date, datetime

from .. import bs
from ..calendar_in import IST
from ..instruments import Instrument, Universe, opt_symbol
from .base import TF_SECONDS, Candle, Provider, Quote, summarize_chain


class DemoProvider(Provider):
    name = "demo"
    SPEED = 40          # simulated seconds per real second so moves are visible

    def __init__(self, universe: Universe, seed: int = 7) -> None:
        self.u = universe
        self.rng = random.Random(seed)
        self.spot = {i.id: i.base_price for i in universe.items.values()
                     if i.kind in ("equity", "index")}
        self.prev = dict(self.spot)
        self.series: dict[tuple[str, str], list[Candle]] = {}
        self._last_step = time.monotonic()

    # ---- simulation ------------------------------------------------------------
    def step(self, dt: float | None = None) -> None:
        now = time.monotonic()
        dt = (now - self._last_step) if dt is None else dt
        self._last_step = now
        for iid, p in list(self.spot.items()):
            inst = self.u.items[iid]
            sigma = inst.vol * math.sqrt(dt * self.SPEED / 31_536_000)
            pull = -0.003 * math.log(p / inst.base_price) * dt
            p *= math.exp(pull + sigma * self.rng.gauss(0, 1))
            self.spot[iid] = p
            self._touch(iid, p)

    def _touch(self, iid: str, price: float) -> None:
        now = time.time()
        for (sid, tf), rows in self.series.items():
            if sid != iid:
                continue
            secs = TF_SECONDS[tf]
            bucket = int(now // secs) * secs
            last = rows[-1]
            if last[0] < bucket:
                rows.append([bucket, last[4], price, price, price, 0])
                last = rows[-1]
            last[4] = price
            last[2] = max(last[2], price)
            last[3] = min(last[3], price)

    # ---- pricing ---------------------------------------------------------------
    def _t(self, expiry: date) -> float:
        return bs.years_to_expiry(expiry, datetime.now(IST))

    def fair(self, inst: Instrument, spot: float | None = None) -> float:
        s = spot if spot is not None else self.spot.get(inst.underlying, inst.base_price)
        if inst.kind in ("equity", "index"):
            return s
        t = self._t(inst.expiry)
        if inst.kind == "future":
            return round(s * math.exp(bs.RATE * t) / inst.tick) * inst.tick
        vol = bs.smile_vol(inst.vol, s, inst.strike)
        px = bs.price(inst.opt_type, s, inst.strike, t, vol)
        return max(round(px / inst.tick) * inst.tick, inst.tick)

    def _quote(self, inst: Instrument, spot: float | None = None) -> Quote:
        px = self.fair(inst, spot)
        if inst.kind in ("equity", "index"):
            prev = self.prev.get(inst.id)
        else:
            base = self.prev.get(inst.underlying, inst.base_price)
            prev = self.fair(inst, base)
        half = max(inst.tick, px * 0.0004) if inst.kind != "option" else max(inst.tick, px * 0.004)
        return Quote(price=px, ts=time.time(), prev_close=prev, source="demo",
                     bid=max(px - half, inst.tick), ask=px + half,
                     oi=self._oi(inst), volume=None)

    def _oi(self, inst: Instrument) -> float | None:
        if inst.kind != "option":
            return None
        s = self.spot.get(inst.underlying, inst.base_price)
        rnd = random.Random(f"{inst.id}")
        dist = abs(inst.strike - s) / s
        base = math.exp(-dist * 28) * 1_500_000
        return round(base * rnd.uniform(0.6, 1.4) / inst.lot_size) * inst.lot_size

    async def quotes(self, insts: list[Instrument]) -> dict[str, Quote]:
        return {i.id: self._quote(i) for i in insts}

    def quote(self, inst: Instrument) -> Quote:
        return self._quote(inst)

    # ---- candles ---------------------------------------------------------------
    def _generate(self, inst: Instrument, tf: str, n: int) -> list[Candle]:
        secs = TF_SECONDS[tf]
        rng = random.Random(f"{inst.id}-{tf}")
        sigma = inst.vol * math.sqrt(secs / 31_536_000) * (1.0 if tf == "1d" else 3.0)
        end_bucket = int(time.time() // secs) * secs
        close = self.spot[inst.id]
        rows: list[Candle] = []
        for i in range(n):
            t = end_bucket - i * secs
            ret = rng.gauss(0, sigma)
            open_ = close / math.exp(ret)
            wick = abs(rng.gauss(0, sigma)) * 0.5
            rows.append([t, open_, max(open_, close) * (1 + wick), min(open_, close) * (1 - wick),
                         close, rng.uniform(1e4, 5e5)])
            close = open_
        rows.reverse()
        return rows

    def _underlying_candles(self, und: str, tf: str) -> list[Candle]:
        key = (und, tf)
        if key not in self.series:
            self.series[key] = self._generate(self.u.items[und], tf, 600)
        return self.series[key]

    async def candles(self, inst: Instrument, tf: str, limit: int) -> list[Candle]:
        if tf not in TF_SECONDS:
            raise ValueError("bad timeframe")
        rows = self._underlying_candles(inst.underlying if inst.kind in ("future", "option") else inst.id, tf)
        if inst.kind in ("equity", "index"):
            return [list(r) for r in rows[-limit:]]
        out: list[Candle] = []
        for t, o, h, l, c, v in rows[-limit:]:
            po, ph, pl, pc = (self.fair(inst, x) for x in (o, h, l, c))
            lo, hi = min(po, ph, pl, pc), max(po, ph, pl, pc)
            out.append([t, po, hi, lo, pc, v])
        return out

    # ---- option chain ----------------------------------------------------------
    async def option_chain(self, und: str, expiry: date) -> dict:
        spot = self.spot.get(und)
        if spot is None:
            raise ValueError("unknown underlying")
        t = self._t(expiry)
        rows = []
        for k in self.u.strikes(und, expiry):
            row = {"strike": k}
            for ot, key in (("CE", "ce"), ("PE", "pe")):
                inst = self.u.contract(und, expiry, k, ot)
                if inst is None:
                    continue
                vol = bs.smile_vol(inst.vol, spot, k)
                g = bs.greeks(ot, spot, k, t, vol)
                q = self._quote(inst)
                row[key] = {"id": inst.id, "ltp": q.price, "bid": q.bid, "ask": q.ask, "oi": q.oi,
                            "volume": None, "iv": vol * 100, "delta": g["delta"], "gamma": g["gamma"],
                            "theta": g["theta"], "vega": g["vega"]}
            rows.append(row)
        return {"underlying": und, "expiry": expiry.isoformat(), "underlying_ltp": spot,
                "lot_size": self.u.lot_size(und), "rows": rows, "source": "demo",
                "summary": summarize_chain(rows, spot)}

    def status(self) -> dict:
        return {"name": "demo", "ok": True}
