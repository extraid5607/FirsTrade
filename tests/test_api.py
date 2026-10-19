"""End-to-end through HTTP in demo mode (market treated as always open, simulated prices)."""
import time

import pytest
from fastapi.testclient import TestClient

from app import main
from app.config import settings

H = {"x-requested-with": "zeropaper"}


@pytest.fixture()
def client(fresh_db):
    main.register_limiter.hits.clear()
    main.login_limiter.hits.clear()
    with TestClient(main.app) as c:
        time.sleep(1.2)               # let the demo feed publish its first prices
        yield c


def register(c, name="alice_1", invite=""):
    r = c.post("/api/auth/register", json={"username": name, "password": "longenoughpw", "invite": invite}, headers=H)
    assert r.status_code == 200, r.text
    return r


def test_health_and_public_pages(client):
    h = client.get("/api/health").json()
    assert h["ok"] and h["hub"]["mode"] == "demo" and h["hub"]["universe"]["option"] > 1000
    assert h["charges"]["verified"] is False
    assert client.get("/").status_code == 200
    me = client.get("/api/me").json()
    assert me["demo"] and me["user"] is None and me["market"]["open"] and me["market"]["forced"]


def test_markets_search_and_instrument(client):
    m = client.get("/api/markets").json()["markets"]
    assert m[0]["kind"] == "index" and any(x["id"] == "RELIANCE" and x["price"] for x in m)
    assert [x["id"] for x in client.get("/api/markets", params={"q": "reli"}).json()["markets"]] == ["RELIANCE"]
    i = client.get("/api/instrument/NIFTY26OCTFUT").json()
    assert i["lot_size"] == 65 and i["products"] == ["NRML", "MIS"] and i["price"] > 20000
    assert client.get("/api/instrument/NOPE").status_code == 404


def test_expiries_and_chain(client):
    e = client.get("/api/expiries", params={"underlying": "NIFTY"}).json()
    assert e["lot_size"] == 65 and len(e["options"]) >= 4 and len(e["futures"]) >= 1
    ch = client.get("/api/chain", params={"underlying": "NIFTY", "expiry": e["options"][1], "strikes": 5}).json()
    assert len(ch["rows"]) == 11 and ch["summary"]["atm"] and ch["source"] == "demo"
    atm_row = next(r for r in ch["rows"] if r["strike"] == ch["summary"]["atm"])
    assert atm_row["ce"]["ltp"] > 0 and 0 < atm_row["ce"]["delta"] < 1 and atm_row["pe"]["delta"] < 0
    assert client.get("/api/chain", params={"underlying": "NIFTY", "expiry": "2020-01-01"}).status_code == 404
    assert client.get("/api/chain", params={"underlying": "NIFTY", "expiry": "bad"}).status_code == 400


def test_candles(client):
    r = client.get("/api/candles", params={"symbol": "NIFTY", "tf": "5m", "limit": 50}).json()
    assert len(r["candles"]) == 50 and len(r["candles"][0]) == 6
    fut = client.get("/api/expiries", params={"underlying": "NIFTY"}).json()["futures"][0]["id"]
    f = client.get("/api/candles", params={"symbol": fut, "tf": "1d", "limit": 30}).json()
    assert len(f["candles"]) == 30
    assert client.get("/api/candles", params={"symbol": "NIFTY", "tf": "9x"}).status_code == 400


def _fut_and_call(client):
    e = client.get("/api/expiries", params={"underlying": "NIFTY"}).json()
    fut = e["futures"][0]["id"]
    ch = client.get("/api/chain", params={"underlying": "NIFTY", "expiry": e["futures"][0]["expiry"],
                                          "strikes": 3}).json()
    atm = next(r for r in ch["rows"] if r["strike"] == ch["summary"]["atm"])
    return fut, atm["ce"]["id"]


def test_full_trading_flow_over_http(client):
    register(client)
    assert client.get("/api/account").json()["cash"] == 1_000_000
    px = client.get("/api/instrument/RELIANCE").json()["price"]
    pv = client.get("/api/risk/size", params={"symbol": "RELIANCE", "side": "long", "product": "CNC",
                                              "risk_pct": 1, "stop": round(px * 0.98, 2)}).json()
    assert pv["qty"] > 0 and pv["risk_pct"] <= 1.0 + 1e-9
    r = client.post("/api/orders", json={"symbol": "RELIANCE", "side": "long", "product": "CNC",
                                         "qty": 10, "sl": round(px * 0.9, 2)}, headers=H)
    assert r.status_code == 200, r.text
    (pos,) = r.json()["account"]["positions"]
    assert pos["product"] == "CNC" and pos["margin"] == pytest.approx(pos["entry"] * 10)
    fut, call = _fut_and_call(client)
    for sym, side in ((fut, "long"), (call, "short")):
        r = client.post("/api/orders", json={"symbol": sym, "side": side, "product": "NRML", "lots": 1}, headers=H)
        assert r.status_code == 200, r.text
    acct = client.get("/api/account").json()
    assert len(acct["positions"]) == 3 and acct["margin_used"] > 0
    f = next(p for p in acct["positions"] if p["kind"] == "future")
    assert f["qty"] == 65 and f["lots"] == 1
    r = client.post(f"/api/trades/{pos['id']}/close", headers=H)
    assert r.status_code == 200 and r.json()["trade"]["status"] == "closed"
    assert client.patch(f"/api/trades/{pos['id']}/journal", json={"note": "test", "rating": 4}, headers=H).status_code == 200
    h = client.get("/api/trades").json()
    assert h["total"] == 1 and h["trades"][0]["note"] == "test"
    s = client.get("/api/stats").json()
    assert s["trades"] == 1 and "by_class" in s and "equity_curve" in s


def test_rule_violations_are_clear_400s(client):
    register(client)
    r = client.post("/api/orders", json={"symbol": "NIFTY", "side": "long", "product": "MIS", "qty": 1}, headers=H)
    assert r.status_code == 400 and "index" in r.json()["detail"]
    r = client.post("/api/orders", json={"symbol": "RELIANCE", "side": "short", "product": "CNC", "qty": 1}, headers=H)
    assert r.status_code == 400 and "Delivery" in r.json()["detail"]
    fut, _ = _fut_and_call(client)
    r = client.post("/api/orders", json={"symbol": fut, "side": "long", "product": "NRML", "qty": 10}, headers=H)
    assert r.status_code == 400 and "lots of 65" in r.json()["detail"]
    r = client.post("/api/orders", json={"symbol": "RELIANCE", "side": "long", "product": "XX", "qty": 1}, headers=H)
    assert r.status_code == 422
    assert client.post("/api/orders", json={}).status_code == 403              # CSRF header missing


def test_login_required_and_admin_only(client):
    assert client.get("/api/account").status_code == 401
    register(client, "alice_1")
    assert client.get("/api/admin/overview").status_code == 403
    client.post("/api/auth/logout", headers=H)
    register(client, "boss")                      # ADMIN_USERNAME=boss in the test environment
    ov = client.get("/api/admin/overview")
    assert ov.status_code == 200 and ov.json()["users"] >= 2
    assert client.post("/api/admin/groww-token", json={"token": "x" * 20}, headers=H).status_code == 400  # demo mode


def test_invite_code_gate(client):
    object.__setattr__(settings, "invite_code", "BATCH42")
    try:
        r = client.post("/api/auth/register", json={"username": "eve_1", "password": "longenoughpw", "invite": "no"}, headers=H)
        assert r.status_code == 400 and "invite code" in r.json()["detail"]
        assert client.get("/api/me").json()["invite_required"] is True
        register(client, "eve_1", "BATCH42")
    finally:
        object.__setattr__(settings, "invite_code", "")


def test_leaderboard_and_reset(client):
    register(client)
    for _ in range(2):
        t = client.post("/api/orders", json={"symbol": "TCS", "side": "long", "product": "CNC", "qty": 1}, headers=H).json()
        client.post(f"/api/trades/{t['account']['positions'][0]['id']}/close", headers=H)
    lb = client.get("/api/leaderboard").json()
    assert lb["rows"][0]["username"] == "alice_1" and lb["rows"][0]["rank"] == 1
    r = client.post("/api/account/reset", json={"confirm": True}, headers=H).json()
    assert r["user"]["season"] == 2 and r["account"]["equity"] == 1_000_000


def test_websocket_streams_prices(client):
    with client.websocket_connect("/ws/prices") as ws:
        msg = ws.receive_json()
    assert msg["open"] and "RELIANCE" in msg["q"] and "d" in msg


def test_live_mode_keeps_prices_private(client):
    """In live mode the real prices are for logged-in students only."""
    object.__setattr__(settings, "price_source", "live")
    try:
        assert client.get("/api/prices").status_code == 401
        assert client.get("/api/instrument/NIFTY").status_code == 401
        assert client.get("/api/candles", params={"symbol": "NIFTY", "tf": "5m"}).status_code == 401
        m = client.get("/api/markets").json()["markets"]
        assert m and all(r["price"] is None and r["live"] is False for r in m)     # names only
        with pytest.raises(Exception):                                             # socket refused
            with client.websocket_connect("/ws/prices") as ws:
                ws.receive_json()
        register(client, "frank_1")
        assert client.get("/api/prices").status_code == 200
        assert any(r["price"] for r in client.get("/api/markets").json()["markets"])
        with client.websocket_connect("/ws/prices") as ws:
            assert "q" in ws.receive_json()
    finally:
        object.__setattr__(settings, "price_source", "demo")


def test_order_and_preview_with_alias_symbols(client):
    register(client, "trader_alias")
    opt = next(i for i in main.hub.universe.items.values() if i.kind == "option")
    strike_val = int(opt.strike) if opt.strike.is_integer() else opt.strike
    angel_sym = f"{opt.underlying}{opt.expiry:%d%b%y}{strike_val}{opt.opt_type}".upper()

    # Risk size preview accepts Angel One symbol format
    r_size = client.get(f"/api/risk/size?symbol={angel_sym}&side=long&product=NRML&lots=1")
    assert r_size.status_code == 200

    # Order placement accepts Angel One symbol format
    r_ord = client.post("/api/orders", json={"symbol": angel_sym, "side": "long", "product": "NRML", "lots": 1}, headers=H)
    assert r_ord.status_code == 200
    pos = r_ord.json()["account"]["positions"]
    assert any(p["symbol"] == opt.id for p in pos)

    # Order placement accepts equity -EQ alias
    r_eq = client.post("/api/orders", json={"symbol": "RELIANCE-EQ", "side": "long", "product": "CNC", "qty": 1}, headers=H)
    assert r_eq.status_code == 200

