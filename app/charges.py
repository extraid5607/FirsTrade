"""Indian trading charges: brokerage, STT, exchange, SEBI, stamp duty and GST.

Values live in data/charges.json (typical figures, not verified against a real contract note).
`leg_charges` returns the total for ONE order leg (an entry or an exit).
"""
from __future__ import annotations

import json
from pathlib import Path

FILE = Path(__file__).resolve().parent.parent / "data" / "charges.json"

DEFAULTS = {
    "stt_pct": {"delivery_buy": 0.1, "delivery_sell": 0.1, "intraday_sell": 0.025,
                "future_sell": 0.05, "option_sell_premium": 0.15, "option_exercise_intrinsic": 0.15},
    "brokerage": {"delivery_flat": 0.0, "intraday_pct": 0.03, "intraday_cap": 20.0,
                  "future_flat": 20.0, "option_flat": 20.0},
    "exchange_txn_pct": {"equity": 0.00297, "future": 0.00183, "option_premium": 0.03553},
    "sebi_per_crore": 10.0,
    "stamp_buy_pct": {"delivery": 0.015, "intraday": 0.003, "future": 0.002, "option": 0.003},
    "gst_pct": 18.0,
}


def _load() -> dict:
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
    try:
        raw = json.loads(FILE.read_text())
    except (OSError, ValueError):
        return out
    for k, v in raw.items():
        if k in out and isinstance(v, dict) and isinstance(out[k], dict):
            out[k].update({a: float(b) for a, b in v.items() if isinstance(b, (int, float))})
        elif k in out and isinstance(v, (int, float)):
            out[k] = float(v)
    return out


TABLE = _load()


def leg_charges(kind: str, product: str, txn: str, price: float, qty: float) -> dict:
    """kind: equity|future|option. product: CNC|MIS|NRML. txn: buy|sell."""
    t = TABLE
    turnover = price * qty
    stt_p = t["stt_pct"]
    br = t["brokerage"]
    delivery = kind == "equity" and product == "CNC"
    stt = brokerage = 0.0
    if kind == "equity":
        if delivery:
            stt = turnover * stt_p["delivery_buy" if txn == "buy" else "delivery_sell"] / 100
            brokerage = br["delivery_flat"]
        else:
            stt = turnover * stt_p["intraday_sell"] / 100 if txn == "sell" else 0.0
            brokerage = min(turnover * br["intraday_pct"] / 100, br["intraday_cap"])
        exch = turnover * t["exchange_txn_pct"]["equity"] / 100
        stamp_key = "delivery" if delivery else "intraday"
    elif kind == "future":
        stt = turnover * stt_p["future_sell"] / 100 if txn == "sell" else 0.0
        brokerage = br["future_flat"]
        exch = turnover * t["exchange_txn_pct"]["future"] / 100
        stamp_key = "future"
    else:
        stt = turnover * stt_p["option_sell_premium"] / 100 if txn == "sell" else 0.0
        brokerage = br["option_flat"]
        exch = turnover * t["exchange_txn_pct"]["option_premium"] / 100
        stamp_key = "option"
    sebi = turnover / 1e7 * t["sebi_per_crore"]
    stamp = turnover * t["stamp_buy_pct"][stamp_key] / 100 if txn == "buy" else 0.0
    gst = (brokerage + exch + sebi) * t["gst_pct"] / 100
    total = brokerage + stt + exch + sebi + stamp + gst
    return {"brokerage": brokerage, "stt": stt, "exchange": exch, "sebi": sebi,
            "stamp": stamp, "gst": gst, "total": total}


def open_txn(side: str) -> str:
    return "buy" if side == "long" else "sell"


def close_txn(side: str) -> str:
    return "sell" if side == "long" else "buy"


def settlement_charges(kind: str, side: str, settle_price: float, strike: float | None,
                       opt_type: str | None, qty: float) -> float:
    """Charges when a contract expires. Futures are closed like a normal exit. A long option that
    expires in the money pays STT on its intrinsic value; one that expires worthless has no exit."""
    if kind == "future":
        return leg_charges("future", "NRML", close_txn(side), settle_price, qty)["total"]
    if kind == "option" and side == "long" and strike is not None and opt_type:
        intrinsic = max(settle_price - strike, 0.0) if opt_type == "CE" else max(strike - settle_price, 0.0)
        if intrinsic > 0:
            return intrinsic * qty * TABLE["stt_pct"]["option_exercise_intrinsic"] / 100
    return 0.0


def describe() -> dict:
    return {"source": "data/charges.json", "verified": False, "table": TABLE}
