import math

import pytest

from app import bs, charges, risk
from app.config import settings


def test_delivery_charges_match_hand_calculation():
    c = charges.leg_charges("equity", "CNC", "buy", 1000.0, 100)       # turnover 1,00,000
    assert c["stt"] == pytest.approx(100.0)                              # 0.1%
    assert c["brokerage"] == 0 and c["stamp"] == pytest.approx(15.0)     # 0.015%
    assert c["exchange"] == pytest.approx(100000 * 0.00297 / 100)
    assert c["gst"] == pytest.approx((c["exchange"] + c["sebi"]) * 0.18)
    assert c["total"] == pytest.approx(sum(c[k] for k in ("brokerage", "stt", "exchange", "sebi", "stamp", "gst")))


def test_stt_applies_to_the_right_side_only():
    assert charges.leg_charges("equity", "MIS", "buy", 100, 100)["stt"] == 0
    assert charges.leg_charges("equity", "MIS", "sell", 100, 100)["stt"] == pytest.approx(2.5)
    assert charges.leg_charges("future", "NRML", "sell", 23000, 65)["stt"] == pytest.approx(23000 * 65 * 0.0005)
    assert charges.leg_charges("future", "NRML", "buy", 23000, 65)["stt"] == 0
    assert charges.leg_charges("option", "NRML", "sell", 100, 65)["stt"] == pytest.approx(100 * 65 * 0.0015)
    assert charges.leg_charges("option", "NRML", "buy", 100, 65)["stt"] == 0


def test_intraday_brokerage_is_capped():
    assert charges.leg_charges("equity", "MIS", "buy", 5000, 1000)["brokerage"] == 20.0
    assert charges.leg_charges("equity", "MIS", "buy", 100, 10)["brokerage"] == pytest.approx(0.3)


def test_settlement_charges():
    assert charges.settlement_charges("option", "long", 23100, 23000, "CE", 65) == pytest.approx(100 * 65 * 0.0015)
    assert charges.settlement_charges("option", "long", 22900, 23000, "CE", 65) == 0
    assert charges.settlement_charges("option", "short", 23100, 23000, "CE", 65) == 0
    assert charges.settlement_charges("future", "long", 23100, None, None, 65) > 0


def test_black_scholes_put_call_parity_and_greeks():
    s, k, t, v = 23000.0, 23100.0, 14 / 365, 0.15
    c, p = bs.price("CE", s, k, t, v), bs.price("PE", s, k, t, v)
    assert c - p == pytest.approx(s - k * math.exp(-bs.RATE * t), abs=1e-6)
    g = bs.greeks("CE", s, k, t, v)
    assert 0 < g["delta"] < 1 and g["gamma"] > 0 and g["theta"] < 0 and g["vega"] > 0
    assert bs.greeks("PE", s, k, t, v)["delta"] == pytest.approx(g["delta"] - 1)
    assert bs.implied_vol("CE", c, s, k, t) == pytest.approx(v, abs=1e-6)
    assert bs.implied_vol("CE", 0.0001, s, k, 0) is None
    assert bs.price("CE", 23200, 23000, 0, 0.2) == 200 and bs.intrinsic("PE", 23200, 23000) == 0


def test_product_rules(universe):
    r, n, f = universe.get("RELIANCE"), universe.get("NIFTY"), universe.get("NIFTY26OCTFUT")
    assert risk.allowed_products(r) == ["CNC", "MIS"] and risk.allowed_products(f) == ["NRML", "MIS"]
    assert risk.check_product(n, "MIS", "long")
    assert risk.check_product(r, "CNC", "short")
    assert risk.check_product(f, "NRML", "short") is None


def test_margin_rules(universe):
    r, f = universe.get("RELIANCE"), universe.get("NIFTY26OCTFUT")
    assert risk.margin_required(r, "CNC", "long", 1000, 10) == 10000
    assert risk.margin_required(r, "MIS", "long", 1000, 10) == pytest.approx(10000 / settings.mis_leverage)
    assert risk.margin_required(f, "NRML", "long", 23000, 65) == pytest.approx(23000 * 65 * 0.11)
    opt = universe.get("NIFTY26OCT23000CE")
    assert risk.margin_required(opt, "NRML", "long", 200, 65) == 200 * 65
    assert risk.margin_required(opt, "NRML", "short", 200, 65, 23000) == pytest.approx(23000 * 65 * 0.12)


def test_size_position_never_exceeds_risk_budget_and_is_maximal(universe):
    for inst_id, prod, entry, stop in (("RELIANCE", "CNC", 1211.2, 1190.0), ("RELIANCE", "MIS", 1211.2, 1195.0),
                                       ("NIFTY26OCTFUT", "NRML", 23146.0, 22500.0)):
        inst = universe.get(inst_id)
        out = risk.size_position(inst, prod, "long", 1_000_000, 1.0, entry, stop)
        step = risk.qty_step(inst)
        assert risk.risk_at_stop(inst, prod, "long", entry, stop, out["qty"]) <= 10_000 + 1e-6
        assert risk.risk_at_stop(inst, prod, "long", entry, stop, out["qty"] + step) > 10_000
        assert out["qty"] % step == 0


def test_size_position_zero_when_one_lot_is_too_risky(universe):
    out = risk.size_position(universe.get("NIFTY26OCTFUT"), "NRML", "long", 1_000_000, 1.0, 23146.0, 23000.0)
    assert out["qty"] == 0 and "smallest size" in out["warnings"][0]


def test_stop_side_and_tick_snapping():
    assert risk.check_stop_side("long", 100, 99, 101) is None
    assert risk.check_stop_side("short", 100, 99, None)
    assert risk.snap_price(1211.237, 0.05) == 1211.25 and risk.snap_price(10.0, 0.0) == 10.0


def test_liq_price_rules(universe):
    r = universe.get("RELIANCE")
    assert risk.liq_price(r, "CNC", "long", 1000, 10, 10000) is None
    assert risk.liq_price(r, "MIS", "long", 1000, 10, 2000) == pytest.approx(1000 - 0.9 * 200)
    assert risk.liq_price(r, "MIS", "short", 1000, 10, 2000) == pytest.approx(1000 + 0.9 * 200)
