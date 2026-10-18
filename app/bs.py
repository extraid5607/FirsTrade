"""Black-Scholes pricing, Greeks and implied volatility for European options.

Index options in India are European and cash-settled, which is what Black-Scholes models. Stock
options are also European-style now. Rates and dividends are simplified (one risk-free rate, no
dividends), so model prices are close to real ones but never exact.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta

from .calendar_in import CLOSE_T, IST

RATE = 0.065
SECONDS_PER_YEAR = 365.0 * 86400


def _cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def intrinsic(opt: str, spot: float, strike: float) -> float:
    return max(spot - strike, 0.0) if opt == "CE" else max(strike - spot, 0.0)


def years_to_expiry(expiry: date, now: datetime) -> float:
    """Time left until 15:30 IST on the expiry day, in years (never negative)."""
    end = datetime.combine(expiry, CLOSE_T, IST)
    secs = (end - now.astimezone(IST)).total_seconds()
    return max(secs, 0.0) / SECONDS_PER_YEAR


def _d1d2(spot: float, strike: float, t: float, vol: float, r: float) -> tuple[float, float]:
    sq = vol * math.sqrt(t)
    d1 = (math.log(spot / strike) + (r + 0.5 * vol * vol) * t) / sq
    return d1, d1 - sq


def price(opt: str, spot: float, strike: float, t: float, vol: float, r: float = RATE) -> float:
    if t <= 0 or vol <= 0 or spot <= 0 or strike <= 0:
        return intrinsic(opt, spot, strike)
    d1, d2 = _d1d2(spot, strike, t, vol, r)
    disc = math.exp(-r * t)
    if opt == "CE":
        return spot * _cdf(d1) - strike * disc * _cdf(d2)
    return strike * disc * _cdf(-d2) - spot * _cdf(-d1)


def greeks(opt: str, spot: float, strike: float, t: float, vol: float, r: float = RATE) -> dict:
    """delta, gamma, theta (per calendar day), vega (per 1 vol point)."""
    if t <= 0 or vol <= 0 or spot <= 0 or strike <= 0:
        itm = intrinsic(opt, spot, strike) > 0
        delta = (1.0 if opt == "CE" else -1.0) if itm else 0.0
        return {"delta": delta, "gamma": 0.0, "theta": 0.0, "vega": 0.0}
    d1, d2 = _d1d2(spot, strike, t, vol, r)
    disc = math.exp(-r * t)
    pdf = _pdf(d1)
    delta = _cdf(d1) if opt == "CE" else _cdf(d1) - 1.0
    gamma = pdf / (spot * vol * math.sqrt(t))
    vega = spot * pdf * math.sqrt(t) / 100.0
    first = -spot * pdf * vol / (2.0 * math.sqrt(t))
    if opt == "CE":
        theta = first - r * strike * disc * _cdf(d2)
    else:
        theta = first + r * strike * disc * _cdf(-d2)
    return {"delta": delta, "gamma": gamma, "theta": theta / 365.0, "vega": vega}


def implied_vol(opt: str, target: float, spot: float, strike: float, t: float,
                r: float = RATE) -> float | None:
    """Bisection. Returns None when the price is below intrinsic or the time is up."""
    if t <= 0 or target <= 0 or spot <= 0 or strike <= 0:
        return None
    if target < intrinsic(opt, spot, strike) * math.exp(-r * t) - 1e-9:
        return None
    lo, hi = 0.01, 5.0
    if price(opt, spot, strike, t, hi, r) < target:
        return None
    for _ in range(80):
        mid = (lo + hi) / 2
        if price(opt, spot, strike, t, mid, r) < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def smile_vol(base_vol: float, spot: float, strike: float) -> float:
    """A simple volatility smile for the demo feed: higher vol for far strikes, a bit more for puts."""
    m = math.log(strike / spot)
    return max(0.05, base_vol * (1.0 + 2.2 * m * m * 10 - 0.6 * m))
