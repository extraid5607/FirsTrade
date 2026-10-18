import time

import pytest
from sqlalchemy import select

from app.db import Order, Trade
from app.engine import TradeError
from tests.conftest import order


def place(engine, db, user, **kw):
    return engine.place_order(db, user, order(**kw))


def open_trade(db, user):
    return db.scalars(select(Trade).where(Trade.user_id == user.id, Trade.status == "open")).all()


# ---------------------------------------------------------------- opening & margin
def test_cnc_buy_blocks_full_value_and_charges_fees(engine, db, user):
    place(engine, db, user, qty=100, product="CNC")
    (t,) = open_trade(db, user)
    assert t.margin == pytest.approx(120_000)
    assert t.liq_price is None                               # fully paid, never auto-closed
    assert t.fee_open > 100                                  # STT 0.1% on 1.2 lakh ~ 120 + others
    st = engine.state(db, user)
    assert st["available"] == pytest.approx(1_000_000 - t.fee_open - 120_000)


def test_cnc_cannot_be_sold_short(engine, db, user):
    with pytest.raises(TradeError, match="Delivery"):
        place(engine, db, user, side="short", qty=10, product="CNC")


def test_mis_equity_uses_leverage(engine, db, user):
    place(engine, db, user, qty=100, product="MIS")
    (t,) = open_trade(db, user)
    assert t.margin == pytest.approx(120_000 / 5)
    assert t.liq_price == pytest.approx(1200 - 0.9 * t.margin / 100)


def test_index_is_not_tradable_and_equity_cannot_use_nrml(engine, db, user):
    with pytest.raises(TradeError, match="index"):
        place(engine, db, user, symbol="NIFTY", qty=1, product="MIS")
    with pytest.raises(TradeError, match="CNC or MIS"):
        place(engine, db, user, qty=1, product="NRML")


def test_futures_are_lot_based(engine, db, user):
    place(engine, db, user, symbol="NIFTY26OCTFUT", lots=2, product="NRML")
    (t,) = open_trade(db, user)
    assert t.qty == 130 and t.lot_size == 65
    assert t.margin == pytest.approx(23050 * 130 * 0.11)
    with pytest.raises(TradeError, match="lots of 65"):
        place(engine, db, user, symbol="NIFTY26OCTFUT", qty=100, product="NRML")


def test_long_option_margin_is_premium_short_option_is_percent_of_underlying(engine, db, user):
    place(engine, db, user, symbol="NIFTY26OCT23000CE", lots=1, product="NRML")
    place(engine, db, user, symbol="NIFTY26OCT23000PE", side="short", lots=1, product="NRML")
    long_, short_ = sorted(open_trade(db, user), key=lambda t: t.side == "short")
    assert long_.margin == pytest.approx(200 * 65) and long_.liq_price is None
    assert short_.margin == pytest.approx(23000 * 65 * 0.12)
    assert short_.liq_price is not None and short_.liq_price > 180


def test_not_enough_margin_message_is_helpful(engine, db, user):
    with pytest.raises(TradeError, match="Not enough free margin"):
        place(engine, db, user, qty=1000, product="CNC")           # 12 lakh > 10 lakh


# ---------------------------------------------------------------- market hours
def test_orders_rejected_when_market_closed(engine, db, user, now):
    now.set("2026-10-15 15:31")
    with pytest.raises(TradeError, match="market is closed"):
        place(engine, db, user, qty=1)
    now.set("2026-10-17 11:00")                      # Saturday
    with pytest.raises(TradeError, match="market is closed"):
        place(engine, db, user, qty=1)


def test_no_new_mis_after_squareoff_time(engine, db, user, now):
    now.set("2026-10-15 15:21")
    with pytest.raises(TradeError, match="MIS"):
        place(engine, db, user, qty=1, product="MIS")
    place(engine, db, user, qty=1, product="CNC")     # delivery is still fine until close


def test_stale_price_blocks_trading(engine, db, user, hub):
    hub.set("RELIANCE", 1200.0, age=120)
    with pytest.raises(TradeError, match="no live price"):
        place(engine, db, user, qty=1)


def test_stops_do_not_fire_while_market_closed(engine, db, user, hub, now):
    place(engine, db, user, qty=10, sl=1190.0)
    now.set("2026-10-15 15:35")
    hub.set("RELIANCE", 1100.0)
    engine.process_tick(db)
    assert open_trade(db, user)


# ---------------------------------------------------------------- stops, targets, auto square-off
def test_stop_loss_and_target(engine, db, user, hub):
    place(engine, db, user, qty=100, product="CNC", sl=1190.0, tp=1230.0)
    place(engine, db, user, symbol="TCS", qty=10, product="CNC", sl=2380.0, tp=2450.0)
    hub.set("RELIANCE", 1189.0)
    hub.set("TCS", 2455.0)
    assert engine.process_tick(db) == 2
    rows = {t.symbol: t for t in db.scalars(select(Trade))}
    assert rows["RELIANCE"].close_reason == "sl" and rows["RELIANCE"].exit_price == 1189.0
    assert rows["TCS"].close_reason == "tp" and rows["TCS"].exit_price == 2450.0   # targets fill at target
    assert rows["RELIANCE"].pnl < -1100 and rows["TCS"].pnl > 0


def test_sell_short_mis_stop_above(engine, db, user, hub):
    place(engine, db, user, side="short", qty=50, product="MIS", sl=1210.0, tp=1180.0)
    hub.set("RELIANCE", 1211.0)
    engine.process_tick(db)
    (t,) = db.scalars(select(Trade)).all()
    assert t.close_reason == "sl" and t.pnl < 0


def test_stop_side_validation(engine, db, user):
    with pytest.raises(TradeError, match="below the entry"):
        place(engine, db, user, qty=1, sl=1300.0)
    with pytest.raises(TradeError, match="above the entry"):
        place(engine, db, user, side="short", qty=1, product="MIS", sl=1100.0)


def test_mis_squareoff_at_1520(engine, db, user, hub, now):
    place(engine, db, user, qty=10, product="MIS")
    place(engine, db, user, symbol="TCS", qty=1, product="CNC")
    now.set("2026-10-15 15:19")
    engine.process_tick(db)
    assert len(open_trade(db, user)) == 2
    now.set("2026-10-15 15:20")
    hub.set("RELIANCE", 1195.0)
    engine.process_tick(db)
    closed = db.scalars(select(Trade).where(Trade.status == "closed")).all()
    assert [(t.symbol, t.close_reason, t.exit_price) for t in closed] == [("RELIANCE", "squareoff", 1195.0)]
    assert [t.symbol for t in open_trade(db, user)] == ["TCS"]       # delivery stays


def test_stale_mis_from_earlier_day_closes_next_morning(engine, db, user, now):
    place(engine, db, user, qty=10, product="MIS")
    t = open_trade(db, user)[0]
    t.opened_at -= 86400                     # pretend it was opened yesterday
    db.commit()
    now.set("2026-10-16 09:20")
    engine.process_tick(db)
    assert db.scalars(select(Trade)).one().close_reason == "squareoff"


def test_risk_system_closes_leveraged_loser_at_liq_price(engine, db, user, hub):
    place(engine, db, user, symbol="NIFTY26OCTFUT", lots=1, product="NRML")
    (t,) = open_trade(db, user)
    liq = t.liq_price
    hub.set("NIFTY26OCTFUT", liq - 5)
    engine.process_tick(db)
    t = db.scalars(select(Trade)).one()
    assert t.close_reason == "rms" and t.exit_price == liq
    assert t.gross_pnl == pytest.approx(-0.9 * t.margin)


# ---------------------------------------------------------------- limit orders
def test_limit_order_fills_on_touch_and_lapses_at_close(engine, db, user, hub, now):
    place(engine, db, user, qty=10, product="CNC", type="limit", limit_price=1190.0)
    assert db.scalars(select(Order)).one().status == "pending"
    hub.set("RELIANCE", 1195.0)
    engine.process_tick(db)
    assert not open_trade(db, user)
    hub.set("RELIANCE", 1189.5)
    engine.process_tick(db)
    (t,) = open_trade(db, user)
    assert t.entry_price == 1190.0 and t.entry_type == "limit"
    place(engine, db, user, symbol="TCS", qty=1, product="CNC", type="limit", limit_price=2300.0)
    now.set("2026-10-15 15:31")
    engine.process_tick(db)
    assert [o.status for o in db.scalars(select(Order).where(Order.symbol == "TCS"))] == ["cancelled"]


def test_limit_price_must_be_on_the_right_side(engine, db, user):
    with pytest.raises(TradeError, match="buy limit must be below"):
        place(engine, db, user, qty=1, type="limit", limit_price=1250.0)
    with pytest.raises(TradeError, match="sell limit must be above"):
        place(engine, db, user, side="short", qty=1, product="MIS", type="limit", limit_price=1150.0)


# ---------------------------------------------------------------- expiry settlement
def test_future_settles_at_underlying_price_after_expiry(engine, db, user, hub, now):
    place(engine, db, user, symbol="NIFTY26OCTFUT", lots=1, product="NRML")
    now.set("2026-10-27 15:29")
    engine.process_tick(db)
    assert open_trade(db, user)
    now.set("2026-10-27 15:31")
    hub.set("NIFTY", 23200.0)
    engine.process_tick(db)
    t = db.scalars(select(Trade)).one()
    assert t.close_reason == "expiry" and t.exit_price == 23200.0
    assert t.gross_pnl == pytest.approx((23200 - 23050) * 65)


def test_options_settle_at_intrinsic_value(engine, db, user, hub, now):
    place(engine, db, user, symbol="NIFTY26OCT23000CE", lots=1, product="NRML")
    place(engine, db, user, symbol="NIFTY26OCT23000PE", side="short", lots=1, product="NRML")
    now.set("2026-10-27 15:31")
    hub.set("NIFTY", 23100.0)
    engine.process_tick(db)
    rows = {t.symbol: t for t in db.scalars(select(Trade))}
    ce, pe = rows["NIFTY26OCT23000CE"], rows["NIFTY26OCT23000PE"]
    assert ce.exit_price == pytest.approx(100.0) and ce.gross_pnl == pytest.approx((100 - 200) * 65)
    assert pe.exit_price == 0.0 and pe.gross_pnl == pytest.approx(180 * 65)    # writer keeps the premium
    assert ce.fee_close > 0 and pe.fee_close == 0           # exercise STT on the ITM long only


def test_settlement_waits_for_an_underlying_price(engine, db, user, hub, now):
    place(engine, db, user, symbol="NIFTY26OCTFUT", lots=1, product="NRML")
    now.set("2026-10-28 09:00")
    del hub.quotes["NIFTY"]
    engine.process_tick(db)
    assert open_trade(db, user)
    hub.set("NIFTY", 23000.0)
    engine.process_tick(db)
    assert not open_trade(db, user)


def test_expired_contract_cannot_be_traded(engine, db, user, now):
    now.set("2026-10-19 15:31")                       # weekly expiry day, after close
    with pytest.raises(TradeError):                   # market is closed anyway; still must not slip through
        place(engine, db, user, symbol="NIFTY26O1923000CE", lots=1, product="NRML")


# ---------------------------------------------------------------- risk rules
def test_max_risk_rule(engine, db, user):
    user.max_risk_pct = 1.0
    db.commit()
    with pytest.raises(TradeError, match="above your 1% limit"):
        place(engine, db, user, qty=500, product="CNC", sl=1100.0)       # ~₹50k risk = 5%


def test_require_stop_loss(engine, db, user):
    user.require_sl = True
    db.commit()
    with pytest.raises(TradeError, match="require a stop-loss"):
        place(engine, db, user, qty=1)


def test_size_by_risk_percent(engine, db, user):
    place(engine, db, user, risk_pct=1.0, sl=1190.0, product="CNC")
    (t,) = open_trade(db, user)
    assert 0.9 < t.initial_risk / 1_000_000 * 100 <= 1.0 or t.initial_risk <= 10_000
    assert t.qty == int(t.qty) and t.qty > 0


def test_daily_loss_lock(engine, db, user, hub):
    user.daily_loss_pct = 1.0
    user.max_risk_pct = 100
    db.commit()
    place(engine, db, user, symbol="NIFTY26OCTFUT", lots=5, product="NRML", sl=22900.0)
    hub.set("NIFTY26OCTFUT", 22899.0)
    engine.process_tick(db)                       # stop hit, loses ~₹49k = 4.9% > 1%
    assert engine.state(db, user)["locked"]
    with pytest.raises(TradeError, match="Daily loss limit"):
        place(engine, db, user, qty=1)


def test_open_position_limit(engine, db, user, monkeypatch):
    from app import engine as eng
    monkeypatch.setattr(eng.settings, "max_open_positions", 2, raising=False) if False else None
    from app.config import settings
    object.__setattr__(settings, "max_open_positions", 2)
    try:
        place(engine, db, user, qty=1)
        place(engine, db, user, qty=1)
        with pytest.raises(TradeError, match="at most 2"):
            place(engine, db, user, qty=1)
    finally:
        object.__setattr__(settings, "max_open_positions", 15)


# ---------------------------------------------------------------- close, edit, reset
def test_manual_close_updates_cash_and_journal_fields(engine, db, user, hub):
    place(engine, db, user, qty=100, product="CNC", sl=1190.0)
    (t,) = open_trade(db, user)
    hub.set("RELIANCE", 1220.0)
    engine.close_trade(db, user, t.id)
    t = db.get(Trade, t.id)
    assert t.status == "closed" and t.close_reason == "manual"
    assert t.gross_pnl == pytest.approx(2000)
    assert t.pnl == pytest.approx(2000 - t.fee_open - t.fee_close)
    assert t.r_multiple == pytest.approx(t.pnl / t.initial_risk)
    assert user.cash == pytest.approx(1_000_000 + 2000 - t.fee_open - t.fee_close)
    engine.update_journal(db, user, t.id, {"note": "n", "setup": "breakout", "tags": ["a", "a", "b"], "rating": 4})
    assert db.get(Trade, t.id).tags == "a,b"


def test_set_stops_validates_side(engine, db, user):
    place(engine, db, user, qty=10)
    (t,) = open_trade(db, user)
    with pytest.raises(TradeError):
        engine.set_stops(db, user, t.id, 1300.0, None)
    engine.set_stops(db, user, t.id, 1150.0, 1300.0)
    assert db.get(Trade, t.id).sl == 1150.0 and db.get(Trade, t.id).initial_risk is not None


def test_reset_starts_new_season(engine, db, user):
    place(engine, db, user, qty=10)
    engine.reset_account(db, user)
    assert user.season == 2 and user.cash == 1_000_000
    assert not open_trade(db, user)
    assert engine.state(db, user)["equity"] == 1_000_000


# ---------------------------------------------------------------- accounting invariant
def test_accounting_invariant_under_random_trading(engine, db, user, hub, now):
    """cash always equals start + realised P&L - charges, whatever sequence of events happens."""
    import random
    rng = random.Random(3)
    symbols = [("RELIANCE", "CNC"), ("RELIANCE", "MIS"), ("TCS", "MIS"),
               ("NIFTY26OCTFUT", "NRML"), ("NIFTY26OCT23000CE", "NRML")]
    base = {"RELIANCE": 1200.0, "TCS": 2400.0, "NIFTY26OCTFUT": 23050.0, "NIFTY26OCT23000CE": 200.0,
            "NIFTY": 23000.0}
    for step in range(300):
        for s, b in base.items():
            hub.set(s, max(b * (1 + rng.uniform(-0.06, 0.06)), 1.0))
        sym, prod = rng.choice(symbols)
        try:
            kw = dict(symbol=sym, product=prod, side=rng.choice(["long", "short"]) if prod != "CNC" else "long")
            if sym.startswith("NIFTY"):
                kw["lots"] = rng.randint(1, 2)
            else:
                kw["qty"] = rng.randint(1, 40)
            if rng.random() < 0.5:
                q = hub.last(sym).price
                kw["sl"] = q * (0.97 if kw["side"] == "long" else 1.03)
            place(engine, db, user, **kw)
        except TradeError:
            pass
        engine.process_tick(db)
        if rng.random() < 0.2:
            for t in open_trade(db, user)[:1]:
                try:
                    engine.close_trade(db, user, t.id)
                except TradeError:
                    pass
        if step == 150:
            now.set("2026-10-15 15:21")
        if step == 160:
            now.set("2026-10-15 10:00")
    trades = db.scalars(select(Trade)).all()
    realised = sum(t.gross_pnl - t.fee_close for t in trades if t.status == "closed")
    fees_open = sum(t.fee_open for t in trades)
    db.refresh(user)
    assert user.cash == pytest.approx(1_000_000 + realised - fees_open, abs=1e-6)
    st = engine.state(db, user)
    assert st["available"] == pytest.approx(user.cash - st["margin_used"] - st["reserved"])
    assert st["equity"] == pytest.approx(user.cash + st["unrealized"])
