"""Checks the live data sources end to end and prints PASS / FAIL for each step.

Run it on a computer that can reach the internet (NOT the build sandbox):

    export GROWW_ACCESS_TOKEN=...          # today's Groww token (or GROWW_API_KEY + GROWW_API_SECRET)
    python scripts/check_live.py

It uses the app's own parsers, so a PASS here means the live mode of the app can read that source.
It never raises: every step is reported on its own. It makes a few dozen requests in total, well
inside the published limits, and it never retries a refused request harder.
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("PRICE_SOURCE", "live")

import httpx  # noqa: E402

from app.config import settings  # noqa: E402
from app.instruments import INSTRUMENT_CSV_URL, parse_groww_csv  # noqa: E402
from app.providers.groww import GrowwProvider  # noqa: E402
from app.providers.nse import NseProvider  # noqa: E402
from app.providers.yahoo import YahooProvider  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def report(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""), flush=True)


async def step(name: str, fn):
    try:
        ok, detail = await fn()
        report(name, ok, detail)
    except Exception as exc:  # noqa: BLE001 - a check must never crash the run
        report(name, False, f"{type(exc).__name__}: {str(exc)[:160]}")


async def main() -> int:
    today = datetime.now().date()
    uni = None

    async def csv_step():
        nonlocal uni
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as c:
            r = await c.get(INSTRUMENT_CSV_URL)
        r.raise_for_status()
        head = r.text.splitlines()[0]
        uni = parse_groww_csv(r.text, today, settings.fno_expiry_window_days, settings.fno_stock_underlyings)
        kinds: dict[str, int] = {}
        for i in uni.items.values():
            kinds[i.kind] = kinds.get(i.kind, 0) + 1
        print("      CSV header:", head[:300])
        print("      skipped rows by reason:", uni.skipped)
        return len(uni.items) > 100 and kinds.get("equity", 0) > 100, f"counts={kinds}"

    await step("Groww instrument CSV downloads and parses", csv_step)
    if uni is None:
        print("\nWithout the instrument list nothing else can be checked. Look at the CSV header printed above\n"
              "and compare it with the column names in app/instruments.py (parse_groww_csv).")
        return 1

    g = GrowwProvider(uni)
    if not g.configured:
        report("Groww token present", False, "set GROWW_ACCESS_TOKEN (or GROWW_API_KEY + GROWW_API_SECRET)")
    else:
        async def ltp_cash():
            insts = [uni.get(x) for x in ("NIFTY", "RELIANCE", "TCS") if uni.get(x)]
            q = await g.quotes(insts)
            return bool(q), ", ".join(f"{k}={v.price}" for k, v in q.items()) or "no prices came back"
        await step("Groww LTP for NIFTY / RELIANCE / TCS", ltp_cash)

        async def ltp_fno():
            futs = uni.futures("NIFTY")
            if not futs:
                return False, "no NIFTY future in the instrument list"
            fut = futs[0]
            q = await g.quotes([fut])
            got = q.get(fut.id)
            return bool(got), f"{fut.id}: {got.price if got else 'no price'}"
        await step("Groww LTP for a NIFTY future (symbol format)", ltp_fno)

        async def candles():
            c = await g.candles(uni.get("RELIANCE"), "5m", 100)
            return len(c) > 10, f"{len(c)} candles; last={c[-1] if c else None}"
        await step("Groww 5-minute candles for RELIANCE", candles)

        async def candles_day():
            c = await g.candles(uni.get("NIFTY"), "1d", 100)
            return len(c) > 10, f"{len(c)} candles"
        await step("Groww daily candles for NIFTY", candles_day)

        async def chain():
            exps = uni.expiries("NIFTY")
            if not exps:
                return False, "no NIFTY expiries in the instrument list"
            ch = await g.option_chain("NIFTY", exps[0])
            n = len(ch["rows"])
            sample = next((r for r in ch["rows"] if r.get("ce") and r["ce"].get("iv") is not None), None)
            print("      sample row:", sample)
            return n > 10, f"{n} strikes for {exps[0]}; summary={ch['summary']}"
        await step("Groww option chain NIFTY (nearest expiry)", chain)

    y = YahooProvider()

    async def yahoo_step():
        q = await y.quotes([i for i in (uni.get("NIFTY"), uni.get("RELIANCE")) if i])
        return bool(q), ", ".join(f"{k}={v.price}" for k, v in q.items()) or "nothing returned"
    await step("Yahoo price for NIFTY / RELIANCE (fallback source)", yahoo_step)

    n = NseProvider(uni)

    async def nse_step():
        exps = uni.expiries("NIFTY")
        ch = await n.option_chain("NIFTY", exps[0])
        return len(ch["rows"]) > 10, f"{len(ch['rows'])} strikes for {exps[0]}"
    await step("NSE website option chain NIFTY (unofficial; often blocked from cloud hosts)", nse_step)

    for p in (g, y):
        try:
            await p.stop()
        except Exception:  # noqa: BLE001
            pass
    bad = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(bad)} passed, {len(bad)} failed.")
    if bad:
        print("Paste this whole output back to the developer. A FAIL tells exactly which assumption was wrong.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
