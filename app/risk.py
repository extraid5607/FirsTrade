"""Margin, charges and position-size maths for Indian cash and F&O (shared by the ticket and engine).

Margins here are APPROXIMATIONS of what a broker blocks (a flat percentage), not the exchange's
SPAN calculation. They are good enough to teach sizing and risk; they are not exact.
"""
from __future__ import annotations

import math

from .charges import close_txn, leg_charges, open_txn
from .config import settings
from .instruments import Instrument

RMS_LOSS_FRACTION = 0.9     # a leveraged position is squared off after losing this share of its margin
PRODUCTS = ("CNC", "MIS", "NRML")


def direction(side: str) -> int:
    return 1 if side == "long" else -1


def kind_of(inst: Instrument) -> str:
    return {"equity": "equity", "future": "future", "option": "option"}.get(inst.kind, "equity")


def qty_step(inst: Instrument) -> int:
    return inst.lot_size if inst.kind in ("future", "option") else 1


def floor_to_step(x: float, step: float) -> float:
    return math.floor(x / step + 1e-9) * step


def snap_price(price: float, tick: float) -> float:
    """Round a price to the instrument's tick size (e.g. 0.05)."""
    if tick <= 0:
        return price
    return round(round(price / tick) * tick, 6)


def allowed_products(inst: Instrument) -> list[str]:
    if inst.kind == "equity":
        return ["CNC", "MIS"]
    if inst.kind in ("future", "option"):
        return ["NRML", "MIS"]
    return []


def check_product(inst: Instrument, product: str, side: str) -> str | None:
    if not inst.tradable:
        return f"{inst.name} is an index. Trade its futures or options instead."
    if product not in allowed_products(inst):
        names = " or ".join(allowed_products(inst))
        what = "stocks" if inst.kind == "equity" else "futures and options"
        return f"For {what} choose {names}."
    if product == "CNC" and side == "short":
        return "Delivery (CNC) can only be bought. To sell a stock you do not own, use MIS (intraday)."
    return None


def notional(price: float, qty: float) -> float:
    return price * qty


def pnl(side: str, entry: float, exit_price: float, qty: float) -> float:
    return (exit_price - entry) * qty * direction(side)


def margin_required(inst: Instrument, product: str, side: str, price: float, qty: float,
                    underlying_price: float | None = None) -> float:
    value = price * qty
    if inst.kind == "equity":
        return value if product == "CNC" else value / settings.mis_leverage
    if inst.kind == "future":
        return value * settings.futures_margin_pct / 100
    if inst.kind == "option":
        if side == "long":
            return value
        base = (underlying_price or inst.base_price or price) * qty
        return max(base * settings.option_sell_margin_pct / 100, value)
    return value


def liq_price(inst: Instrument, product: str, side: str, entry: float, qty: float,
              margin: float) -> float | None:
    """Price where the broker would square the position off automatically. None = fully paid."""
    if inst.kind == "equity" and product == "CNC":
        return None
    if inst.kind == "option" and side == "long":
        return None
    if qty <= 0:
        return None
    move = RMS_LOSS_FRACTION * margin / qty
    px = entry - move if side == "long" else entry + move
    return max(px, inst.tick) if side == "long" else px


def kind_charge(inst: Instrument, product: str, txn: str, price: float, qty: float) -> float:
    return leg_charges(kind_of(inst), product, txn, price, qty)["total"]


def fee_open(inst: Instrument, product: str, side: str, price: float, qty: float) -> float:
    return kind_charge(inst, product, open_txn(side), price, qty)


def fee_close(inst: Instrument, product: str, side: str, price: float, qty: float) -> float:
    return kind_charge(inst, product, close_txn(side), price, qty)


def risk_at_stop(inst: Instrument, product: str, side: str, entry: float, stop: float,
                 qty: float) -> float:
    """Worst-case loss in rupees if the stop fills: price loss plus entry and exit charges."""
    loss = -pnl(side, entry, stop, qty)
    return max(loss, 0.0) + fee_open(inst, product, side, entry, qty) + fee_close(inst, product, side, stop, qty)


def check_stop_side(side: str, ref: float, sl: float | None, tp: float | None) -> str | None:
    if side == "long":
        if sl is not None and not sl < ref:
            return "For a buy, the stop-loss must be below the entry price."
        if tp is not None and not tp > ref:
            return "For a buy, the target must be above the entry price."
    else:
        if sl is not None and not sl > ref:
            return "For a sell, the stop-loss must be above the entry price."
        if tp is not None and not tp < ref:
            return "For a sell, the target must be below the entry price."
    return None


def preview_position(inst: Instrument, product: str, side: str, equity: float, entry: float,
                     qty: float, stop: float | None = None, tp: float | None = None,
                     available: float | None = None, max_risk_pct: float | None = None,
                     underlying_price: float | None = None) -> dict:
    err = check_product(inst, product, side) or check_stop_side(side, entry, stop, tp)
    if err:
        raise ValueError(err)
    if equity <= 0:
        raise ValueError("Account equity is zero.")
    value = entry * qty
    margin = margin_required(inst, product, side, entry, qty, underlying_price)
    liq = liq_price(inst, product, side, entry, qty, margin)
    out = {
        "qty": qty, "lots": qty / inst.lot_size if inst.kind in ("future", "option") else None,
        "value": value, "margin": margin, "risk": None, "risk_pct": None,
        "liq_price": liq, "fees_open": fee_open(inst, product, side, entry, qty),
        "fees_round_trip": None, "reward": None, "rr": None, "warnings": [],
    }
    w = out["warnings"]
    if stop is not None:
        risk = risk_at_stop(inst, product, side, entry, stop, qty)
        out["risk"] = risk
        out["risk_pct"] = risk / equity * 100
        out["fees_round_trip"] = fee_open(inst, product, side, entry, qty) + \
            fee_close(inst, product, side, stop, qty)
        if tp is not None and qty > 0:
            reward = pnl(side, entry, tp, qty) - fee_open(inst, product, side, entry, qty) \
                - fee_close(inst, product, side, tp, qty)
            out["reward"] = reward
            out["rr"] = reward / risk if risk > 0 else None
        if liq is not None and ((side == "long" and stop <= liq) or (side == "short" and stop >= liq)):
            w.append(f"Your stop is beyond the auto square-off price (about {liq:,.2f}). "
                     f"The broker would close you first.")
        if max_risk_pct is not None and out["risk_pct"] > max_risk_pct + 1e-9:
            w.append(f"Above your own limit of {max_risk_pct:g}% risk per trade.")
        if out["rr"] is not None and out["rr"] < 1:
            w.append("Reward is smaller than risk (R:R below 1).")
    if qty < qty_step(inst):
        w.append(f"Minimum size is {qty_step(inst)} ({'1 lot' if qty_step(inst) > 1 else '1 share'}).")
    if available is not None and margin > available:
        w.append(f"Not enough free margin: needs about ₹{margin:,.0f}, you have ₹{max(available, 0):,.0f}.")
    return out


def size_position(inst: Instrument, product: str, side: str, equity: float, risk_pct: float,
                  entry: float, stop: float, tp: float | None = None, available: float | None = None,
                  max_risk_pct: float | None = None, underlying_price: float | None = None) -> dict:
    """Largest whole-share / whole-lot size whose loss at the stop (charges included) stays
    within risk_pct of equity. Risk grows with size, so a binary search over lots is exact."""
    err = check_product(inst, product, side) or check_stop_side(side, entry, stop, tp)
    if err:
        raise ValueError(err)
    if equity <= 0:
        raise ValueError("Account equity is zero.")
    step = qty_step(inst)
    budget = equity * risk_pct / 100.0
    per_unit = abs(entry - stop)
    lo, hi = 0, int(budget / (per_unit * step)) + 1 if per_unit > 0 else 0
    while lo < hi:                      # largest n with risk(n steps) <= budget
        mid = (lo + hi + 1) // 2
        if risk_at_stop(inst, product, side, entry, stop, mid * step) <= budget:
            lo = mid
        else:
            hi = mid - 1
    qty = lo * step
    out = preview_position(inst, product, side, equity, entry, max(qty, 0), stop, tp, available,
                           max_risk_pct, underlying_price) if qty > 0 else \
        preview_position(inst, product, side, equity, entry, step, stop, tp, available,
                         max_risk_pct, underlying_price)
    if qty == 0:
        out["qty"] = 0
        out["lots"] = 0 if inst.kind in ("future", "option") else None
        out["warnings"].insert(0, "Even the smallest size risks more than your risk %. "
                                  "Raise the risk % or tighten the stop.")
    return out
