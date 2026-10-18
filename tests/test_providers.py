import json
import time
from datetime import date
from pathlib import Path

import httpx
import pytest

from app.providers import groww, nse, yahoo
from app.providers.base import AuthExpired, Blocked, ProviderError, summarize_chain
from app.providers.groww import GrowwProvider
from app.providers.nse import NseProvider
from app.providers.util import num, parse_ohlc, parse_ts

FX = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FX / name).read_text())


def test_num_and_ts_helpers():
    assert num("1,234.5") == 1234.5 and num(None) is None and num("nan") is None and num(True) is None
    assert parse_ts(1758696600) == 1758696600 and parse_ts(1758696600000) == 1758696600
    assert parse_ts("2025-09-24T10:30:00") == parse_ts("2025-09-24 10:30:00")
    assert parse_ts("garbage") is None


def test_parse_ohlc_string_and_dict():
    assert parse_ohlc("{open: 149.50,high: 150.50,low: 148.50,close: 149.50}")["close"] == 149.5
    assert parse_ohlc({"open": 1, "high": 2, "low": 0.5, "close": 1.5})["high"] == 2
    assert parse_ohlc("junk") is None


def test_groww_candles_sorted_deduped_and_broken_rows_dropped():
    rows = groww.parse_candles(load("groww_candles.json"))
    times = [r[0] for r in rows]
    assert times == sorted(set(times))
    assert all(r[2] >= r[3] for r in rows)                  # the bad row (high < low) is repaired or dropped
    assert rows[0][1] == 245.95
    with pytest.raises(ProviderError):
        groww.parse_candles({"payload": {}})


def test_groww_chain_parser():
    ch = groww.parse_chain(load("groww_chain.json"), "NIFTY", date(2026, 10, 27), 65)
    assert [r["strike"] for r in ch["rows"]] == [23000, 23150]     # junk and price-less rows dropped
    ce = ch["rows"][0]["ce"]
    assert ce["id"] == "NIFTY26OCT23000CE" and ce["iv"] == 13.8 and ce["oi"] == 1250000
    assert ch["rows"][1]["ce"]["iv"] == pytest.approx(13.9)         # fraction read as percent
    assert ch["rows"][1]["pe"]["delta"] is None                      # missing Greeks stay None
    s = ch["summary"]
    assert s["atm"] == 23150 and s["pcr"] == pytest.approx((2100000 + 1800000) / (1250000 + 2000000))
    with pytest.raises(ProviderError):
        groww.parse_chain({"status": "SUCCESS"}, "NIFTY", date(2026, 10, 27), 65)


def test_nse_chain_parser_filters_expiry_and_computes_greeks():
    ch = nse.parse_chain(load("nse_chain.json"), "NIFTY", date(2026, 10, 27), 65)
    assert [r["strike"] for r in ch["rows"]] == [23000, 23150]
    leg = ch["rows"][0]["ce"]
    assert leg["ltp"] == 205.5 and leg["iv"] == 13.9 and 0 < leg["delta"] < 1 and leg["theta"] < 0
    assert ch["rows"][1]["ce"]["delta"] is None                      # IV 0 -> no Greeks
    assert ch["underlying_ltp"] == 23146.15
    with pytest.raises(ProviderError):
        nse.parse_chain({"records": {"data": []}}, "NIFTY", date(2026, 10, 27), 65)


def test_yahoo_parsers():
    res = load("yahoo_chart.json")["chart"]["result"][0]
    q = yahoo.parse_quote(res)
    assert q.price == 1211.2 and q.prev_close == 1215.0 and q.change_pct == pytest.approx(-0.3128, abs=1e-3)
    rows = yahoo.parse_candles(res)
    assert len(rows) == 2                      # null row and the high<low row dropped or repaired
    assert yahoo.parse_quote({"meta": {}}) is None


def test_summarize_chain_max_pain():
    rows = [{"strike": 100, "ce": {"oi": 10}, "pe": {"oi": 50}},
            {"strike": 110, "ce": {"oi": 40}, "pe": {"oi": 40}},
            {"strike": 120, "ce": {"oi": 50}, "pe": {"oi": 10}}]
    s = summarize_chain(rows, 109)
    assert s["atm"] == 110 and s["max_pain"] == 110 and s["pcr"] == pytest.approx(1.0)


# ------------------------------------------------------------------ HTTP behaviour (mock server)
def make_groww(universe, handler, token="tok"):
    p = GrowwProvider(universe, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    p._token = token
    return p


@pytest.mark.asyncio
async def test_groww_quotes_request_shape_chunking_and_parsing(universe):
    seen = []

    def handler(req: httpx.Request):
        seen.append(req)
        if req.url.path.endswith("/ltp"):
            syms = req.url.params["exchange_symbols"].split(",")
            return httpx.Response(200, json={"status": "SUCCESS", "payload": {s: 100.0 for s in syms}})
        return httpx.Response(200, json=load("groww_ohlc.json"))

    p = make_groww(universe, handler)
    insts = [i for i in universe.items.values() if i.kind == "equity"] + [universe.get("NIFTY")]
    got = await p.quotes(insts)
    ltp_calls = [r for r in seen if r.url.path.endswith("/ltp")]
    assert all(len(r.url.params["exchange_symbols"].split(",")) <= 50 for r in ltp_calls)
    assert len(ltp_calls) == 1 and len(got) == len(insts)                 # 49 symbols fit in one call
    r = ltp_calls[0]
    assert r.headers["authorization"] == "Bearer tok" and r.headers["x-api-version"] == "1.0"
    assert r.url.params["segment"] == "CASH" and "NSE_RELIANCE" in r.url.params["exchange_symbols"]
    assert got["RELIANCE"].prev_close == 1215.0 and got["NIFTY"].prev_close == 22997.35
    await p.stop()


@pytest.mark.asyncio
async def test_groww_bad_values_are_ignored_not_trusted(universe):
    p = make_groww(universe, lambda req: httpx.Response(200, json=load("groww_ltp_cash.json")))
    got = await p.quotes([universe.get("RELIANCE"), universe.get("NIFTY"), universe.get("TCS")])
    assert set(got) == {"RELIANCE", "NIFTY"}                              # price 0 and null dropped
    assert got["NIFTY"].price == 23146.15
    await p.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("status,exc", [(401, AuthExpired), (403, Blocked), (429, ProviderError),
                                        (500, ProviderError)])
async def test_groww_http_errors_map_to_clear_errors(universe, status, exc):
    p = make_groww(universe, lambda req: httpx.Response(status, json={}))
    with pytest.raises(exc):
        await p.quotes([universe.get("RELIANCE")])
    await p.stop()


@pytest.mark.asyncio
async def test_groww_failure_body_and_missing_token(universe):
    fail = {"status": "FAILURE", "error": {"code": "GA001", "message": "Invalid trading symbol.", "metadata": None}}
    p = make_groww(universe, lambda req: httpx.Response(200, json=fail))
    with pytest.raises(ProviderError, match="GA001"):
        await p.quotes([universe.get("RELIANCE")])
    q = make_groww(universe, lambda req: httpx.Response(200, json={}), token="")
    with pytest.raises(AuthExpired):
        await q.quotes([universe.get("RELIANCE")])
    await p.stop(); await q.stop()


@pytest.mark.asyncio
async def test_groww_candles_and_chain_requests(universe):
    seen = []

    def handler(req):
        seen.append(req)
        if "candles" in req.url.path:
            return httpx.Response(200, json=load("groww_candles.json"))
        return httpx.Response(200, json=load("groww_chain.json"))

    p = make_groww(universe, handler)
    rows = await p.candles(universe.get("RELIANCE"), "15m", 50)
    assert rows and seen[0].url.params["candle_interval"] == "15minute"
    assert seen[0].url.params["groww_symbol"] == "NSE-RELIANCE" and seen[0].url.params["segment"] == "CASH"
    ch = await p.option_chain("NIFTY", date(2026, 10, 27))
    assert seen[1].url.path.endswith("/option-chain/exchange/NSE/underlying/NIFTY")
    assert seen[1].url.params["expiry_date"] == "2026-10-27" and len(ch["rows"]) == 2
    await p.stop()


@pytest.mark.asyncio
async def test_nse_refusal_backs_off_and_never_retries_harder(universe):
    calls = []

    def handler(req):
        calls.append(str(req.url))
        return httpx.Response(403, text="Access Denied")

    p = NseProvider(universe, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(Blocked):
        await p.option_chain("NIFTY", date(2026, 10, 27))
    n = len(calls)
    with pytest.raises(Blocked):
        await p.option_chain("NIFTY", date(2026, 10, 27))
    assert len(calls) == n                                  # cooling down: no new request at all
    assert p.status()["cooling_down"]
    await p.stop()


@pytest.mark.asyncio
async def test_nse_html_reply_is_an_error_not_a_crash(universe):
    def handler(req):
        return httpx.Response(200, text="<html>nope</html>")

    p = NseProvider(universe, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(ProviderError, match="not JSON"):
        await p.option_chain("NIFTY", date(2026, 10, 27))
    await p.stop()
