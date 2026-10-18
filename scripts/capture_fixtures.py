"""Saves REAL sample replies from the data sources so the tests can use them.

Run on a computer that can reach the internet (not the build sandbox):

    export GROWW_ACCESS_TOKEN=...
    python scripts/capture_fixtures.py

Writes tests/fixtures/real/*.json (the token is never written). Then compare each file with the
hand-written one in tests/fixtures/ and fix app/providers/*.py if the shape differs.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tests" / "fixtures" / "real"
OUT.mkdir(parents=True, exist_ok=True)
TOKEN = os.getenv("GROWW_ACCESS_TOKEN", "").strip()
IST = timezone(timedelta(hours=5, minutes=30))
BASE = "https://api.groww.in"


def save(name: str, resp: httpx.Response) -> None:
    try:
        body = resp.json()
    except ValueError:
        body = {"_not_json": resp.text[:2000]}
    (OUT / f"{name}.json").write_text(json.dumps({"status": resp.status_code, "body": body}, indent=2)[:400_000])
    print(f"saved {name}.json  (HTTP {resp.status_code})")
    time.sleep(0.4)          # stay far below the published rate limits


def main() -> None:
    now = datetime.now(IST)
    with httpx.Client(timeout=30, follow_redirects=True) as c:
        r = c.get("https://growwapi-assets.groww.in/instruments/instrument.csv")
        (OUT / "instrument_head.csv").write_text("\n".join(r.text.splitlines()[:60]))
        print("saved instrument_head.csv (first 60 lines; check the header row against parse_groww_csv)")

        if TOKEN:
            h = {"Accept": "application/json", "Authorization": f"Bearer {TOKEN}", "X-API-VERSION": "1.0"}
            save("groww_ltp_cash", c.get(f"{BASE}/v1/live-data/ltp", headers=h, params={
                "segment": "CASH", "exchange_symbols": "NSE_NIFTY,NSE_RELIANCE,NSE_TCS"}))
            save("groww_ohlc_cash", c.get(f"{BASE}/v1/live-data/ohlc", headers=h, params={
                "segment": "CASH", "exchange_symbols": "NSE_NIFTY,NSE_RELIANCE"}))
            start = (now - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
            end = now.strftime("%Y-%m-%d %H:%M:%S")
            save("groww_candles_5m", c.get(f"{BASE}/v1/historical/candles", headers=h, params={
                "exchange": "NSE", "segment": "CASH", "groww_symbol": "NSE-RELIANCE",
                "start_time": start, "end_time": end, "candle_interval": "5minute"}))
            # next Tuesday (NIFTY weekly expiry); adjust by hand if it falls on a holiday
            d = now.date()
            while d.weekday() != 1:
                d += timedelta(days=1)
            save("groww_option_chain", c.get(f"{BASE}/v1/option-chain/exchange/NSE/underlying/NIFTY",
                                              headers=h, params={"expiry_date": d.isoformat()}))
        else:
            print("GROWW_ACCESS_TOKEN not set: skipped the Groww calls.")

        save("yahoo_nifty", c.get("https://query1.finance.yahoo.com/v8/finance/chart/%5ENSEI",
                                  params={"interval": "1m", "range": "1d"},
                                  headers={"User-Agent": "Mozilla/5.0 (compatible; ZeroPaperIndia/1.0)"}))
        print("NSE website chain is NOT captured here: it needs a cookie handshake and is unofficial. "
              "Use scripts/check_live.py to see whether it works from your machine.")


if __name__ == "__main__":
    sys.exit(main())
