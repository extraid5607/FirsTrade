"""Small tolerant parsing helpers shared by the live providers."""
from __future__ import annotations

import re
from datetime import datetime

from ..calendar_in import IST


def num(x) -> float | None:
    """Float from a number or numeric string; None for anything else (incl. NaN, empty)."""
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(str(x).replace(",", "").strip())
    except ValueError:
        return None
    return v if v == v and abs(v) != float("inf") else None


def parse_ts(x) -> float | None:
    """Epoch seconds from an epoch number (seconds or milliseconds) or an ISO string (IST if naive)."""
    n = num(x) if not isinstance(x, str) or x.strip().replace(".", "", 1).isdigit() else None
    if n is not None:
        return n / 1000.0 if n > 1e11 else n
    if isinstance(x, str):
        try:
            d = datetime.fromisoformat(x.strip().replace("Z", "+00:00").replace(" ", "T"))
        except ValueError:
            return None
        if d.tzinfo is None:
            d = d.replace(tzinfo=IST)
        return d.timestamp()
    return None


_OHLC_KEYS = ("open", "high", "low", "close")


def parse_ohlc(x) -> dict | None:
    """Groww's OHLC value is a string like "{open: 149.50,high: 150.50,low: 148.50,close: 149.50}"
    (docs); a normal JSON object is accepted too."""
    if isinstance(x, dict):
        d = {k: num(x.get(k)) for k in _OHLC_KEYS}
        return d if all(v is not None for v in d.values()) else None
    if isinstance(x, str):
        found = dict(re.findall(r"(\w+)\s*:\s*(-?[\d.]+)", x))
        d = {k: num(found.get(k)) for k in _OHLC_KEYS}
        return d if all(v is not None for v in d.values()) else None
    return None


def clean_candles(rows: list) -> list[list]:
    """Sort, de-duplicate and drop impossible candles (high < low, non-positive prices)."""
    out: dict[float, list] = {}
    for r in rows:
        t, o, h, l, c = r[0], r[1], r[2], r[3], r[4]
        v = r[5] if len(r) > 5 and r[5] is not None else 0
        if None in (t, o, h, l, c) or min(o, h, l, c) <= 0 or h < l:
            continue
        h = max(h, o, c)
        l = min(l, o, c)
        out[t] = [t, o, h, l, c, v]
    return [out[k] for k in sorted(out)]
