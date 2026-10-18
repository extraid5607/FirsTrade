"""Tradable instruments: stocks, index/stock futures and index/stock options.

Two ways to fill the universe:
* build_demo_universe(): offline, built from the calendar and the base prices below.
* parse_groww_csv(): the real list from Groww's public instrument file (live mode).

An instrument's `id` is always the exchange trading symbol (RELIANCE, NIFTY26OCTFUT,
NIFTY26O2723000CE ...), the same text Groww uses in its price API.
Indices (NIFTY, BANKNIFTY ...) are for charts and as the underlying of futures/options; you
cannot buy an index directly.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime

from .calendar_in import MarketCalendar

INSTRUMENT_CSV_URL = "https://growwapi-assets.groww.in/instruments/instrument.csv"
MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
WEEK_CODE = {1: "1", 2: "2", 3: "3", 4: "4", 5: "5", 6: "6", 7: "7", 8: "8", 9: "9",
             10: "O", 11: "N", 12: "D"}

# symbol, name, close on 16 Oct 2026 (from NSE bhavcopy), typical yearly volatility
_STOCKS = [
    ("RELIANCE", "Reliance Industries", 1211.2, .24), ("HDFCBANK", "HDFC Bank", 728.85, .20),
    ("ICICIBANK", "ICICI Bank", 1397.0, .22), ("SBIN", "State Bank of India", 962.6, .26),
    ("TCS", "Tata Consultancy Services", 2465.0, .24), ("INFY", "Infosys", 1161.2, .26),
    ("AXISBANK", "Axis Bank", 1290.0, .26), ("ITC", "ITC", 263.5, .20),
    ("BHARTIARTL", "Bharti Airtel", 1905.0, .22), ("LT", "Larsen & Toubro", 3627.1, .24),
    ("KOTAKBANK", "Kotak Mahindra Bank", 447.4, .22), ("HINDUNILVR", "Hindustan Unilever", 1893.8, .20),
    ("BAJFINANCE", "Bajaj Finance", 982.1, .30), ("MARUTI", "Maruti Suzuki", 11489.0, .24),
    ("SUNPHARMA", "Sun Pharma", 1758.8, .24), ("TATASTEEL", "Tata Steel", 175.5, .30),
    ("NTPC", "NTPC", 315.15, .24), ("POWERGRID", "Power Grid", 250.45, .22),
    ("ONGC", "ONGC", 222.0, .26), ("ASIANPAINT", "Asian Paints", 2331.9, .24),
    ("TITAN", "Titan", 4444.8, .26), ("ULTRACEMCO", "UltraTech Cement", 10907.0, .24),
    ("WIPRO", "Wipro", 184.4, .26), ("HCLTECH", "HCL Technologies", 1380.0, .26),
    ("TECHM", "Tech Mahindra", 1790.0, .30), ("M&M", "Mahindra & Mahindra", 2920.9, .26),
    ("COALINDIA", "Coal India", 417.2, .24), ("ADANIENT", "Adani Enterprises", 2692.0, .36),
    ("ADANIPORTS", "Adani Ports", 1750.8, .28), ("JSWSTEEL", "JSW Steel", 1216.3, .28),
    ("HINDALCO", "Hindalco", 908.0, .28), ("BAJAJFINSV", "Bajaj Finserv", 1729.0, .26),
    ("NESTLEIND", "Nestle India", 1265.5, .20), ("DRREDDY", "Dr. Reddy's", 1233.0, .24),
    ("CIPLA", "Cipla", 1325.1, .22), ("EICHERMOT", "Eicher Motors", 7284.5, .26),
    ("GRASIM", "Grasim", 2920.0, .24), ("BPCL", "BPCL", 293.5, .28),
    ("HEROMOTOCO", "Hero MotoCorp", 5225.0, .26), ("BRITANNIA", "Britannia", 4764.0, .22),
    ("APOLLOHOSP", "Apollo Hospitals", 7890.0, .26), ("INDUSINDBK", "IndusInd Bank", 877.0, .32),
    ("SBILIFE", "SBI Life", 1656.8, .24), ("HDFCLIFE", "HDFC Life", 553.0, .24),
    ("TRENT", "Trent", 2958.7, .34), ("BEL", "Bharat Electronics", 376.85, .30),
    ("ETERNAL", "Eternal (Zomato)", 325.3, .36), ("JIOFIN", "Jio Financial", 216.25, .30),
]
# symbol, name, close 16 Oct 2026, lot size, weekly?, strike step, Yahoo symbol
_INDICES = [
    ("NIFTY", "Nifty 50", 23146.15, 65, True, 50, "^NSEI"),
    ("BANKNIFTY", "Nifty Bank", 55844.0, 30, False, 100, "^NSEBANK"),
    ("FINNIFTY", "Nifty Financial Services", 25387.1, 60, False, 50, "NIFTY_FIN_SERVICE.NS"),
    ("MIDCPNIFTY", "Nifty Midcap Select", 13717.4, 120, False, 25, "NIFTY_MID_SELECT.NS"),
]
# Stock-F&O lot sizes for demo and fallback. Verified against Groww FNO instruments.
_STOCK_LOTS = {"RELIANCE": 500, "HDFCBANK": 650, "ICICIBANK": 700, "SBIN": 750, "TCS": 225,
               "INFY": 400, "AXISBANK": 625, "ITC": 1725}
_INDEX_ALIASES = {"NIFTYMIDSELECT": "MIDCPNIFTY"}


def fut_symbol(und: str, expiry: date) -> str:
    return f"{und}{expiry:%y}{MONTHS[expiry.month - 1]}FUT"


def opt_symbol(und: str, expiry: date, strike: float, opt: str, weekly: bool) -> str:
    st = strike_text(strike)
    if weekly:
        return f"{und}{expiry:%y}{WEEK_CODE[expiry.month]}{expiry.day:02d}{st}{opt}"
    return f"{und}{expiry:%y}{MONTHS[expiry.month - 1]}{st}{opt}"


def strike_text(strike: float) -> str:
    return str(int(strike)) if float(strike).is_integer() else f"{strike:g}"


def groww_fut(und: str, expiry: date) -> str:
    return f"NSE-{und}-{expiry:%d}{expiry.strftime('%b')}{expiry:%y}-FUT"


def groww_opt(und: str, expiry: date, strike: float, opt: str) -> str:
    return f"NSE-{und}-{expiry:%d}{expiry.strftime('%b')}{expiry:%y}-{strike_text(strike)}-{opt}"


def strike_step_for(price: float) -> float:
    for limit, step in ((100, 2.5), (200, 5), (500, 10), (1000, 20), (2500, 50), (5000, 100)):
        if price < limit:
            return step
    return 200.0


@dataclass(slots=True)
class Instrument:
    id: str
    kind: str                       # equity | index | future | option
    name: str
    underlying: str
    expiry: date | None = None
    strike: float | None = None
    opt_type: str | None = None     # CE | PE
    lot_size: int = 1
    tick: float = 0.05
    groww_symbol: str = ""
    base_price: float = 0.0
    vol: float = 0.2
    yahoo: str = ""
    weekly: bool = False

    @property
    def tradable(self) -> bool:
        return self.kind != "index"

    @property
    def segment(self) -> str:
        return "CASH" if self.kind in ("equity", "index") else "FNO"

    @property
    def label(self) -> str:
        if self.kind == "future":
            return f"{self.underlying} {self.expiry:%d %b %Y} FUT"
        if self.kind == "option":
            return f"{self.underlying} {self.expiry:%d %b %Y} {strike_text(self.strike)} {self.opt_type}"
        return self.name

    def public(self) -> dict:
        return {"id": self.id, "kind": self.kind, "name": self.name, "label": self.label,
                "underlying": self.underlying, "expiry": self.expiry.isoformat() if self.expiry else None,
                "strike": self.strike, "opt_type": self.opt_type, "lot_size": self.lot_size,
                "tick": self.tick, "tradable": self.tradable, "segment": self.segment}


class Universe:
    def __init__(self) -> None:
        self.items: dict[str, Instrument] = {}
        self._opts: dict[tuple[str, date], dict[float, dict[str, str]]] = {}
        self._futs: dict[str, list[Instrument]] = {}
        self.skipped: dict[str, int] = {}

    def add(self, i: Instrument) -> None:
        self.items[i.id] = i
        if i.kind == "option" and i.expiry and i.strike is not None and i.opt_type:
            self._opts.setdefault((i.underlying, i.expiry), {}).setdefault(i.strike, {})[i.opt_type] = i.id
        elif i.kind == "future":
            self._futs.setdefault(i.underlying, []).append(i)
            self._futs[i.underlying].sort(key=lambda x: x.expiry)

    def get(self, iid: str) -> Instrument | None:
        return self.items.get(iid)

    def __contains__(self, iid: str) -> bool:
        return iid in self.items

    def underlyings(self) -> list[str]:
        return sorted({u for (u, _e) in self._opts} | set(self._futs))

    def expiries(self, und: str, kind: str = "option") -> list[date]:
        if kind == "future":
            return [f.expiry for f in self._futs.get(und, [])]
        return sorted(e for (u, e) in self._opts if u == und)

    def strikes(self, und: str, expiry: date) -> list[float]:
        return sorted(self._opts.get((und, expiry), {}))

    def contract(self, und: str, expiry: date, strike: float, opt: str) -> Instrument | None:
        iid = self._opts.get((und, expiry), {}).get(strike, {}).get(opt)
        return self.items.get(iid) if iid else None

    def futures(self, und: str) -> list[Instrument]:
        return list(self._futs.get(und, []))

    def lot_size(self, und: str) -> int:
        inst = self.items.get(und)
        if inst and inst.lot_size > 1:
            return inst.lot_size
        for f in self._futs.get(und, []):
            return f.lot_size
        for (u, _e), strikes in self._opts.items():
            if u == und:
                for s in strikes.values():
                    for iid in s.values():
                        return self.items[iid].lot_size
        return 1

    def of_kind(self, *kinds: str) -> list[Instrument]:
        return [i for i in self.items.values() if i.kind in kinds]

    def watchlist(self) -> list[Instrument]:
        """What the market list shows: indices, then stocks. Derivatives are reached from them."""
        idx = [i for i in self.items.values() if i.kind == "index"]
        eq = [i for i in self.items.values() if i.kind == "equity"]
        return idx + eq

    def search(self, q: str, limit: int = 30) -> list[Instrument]:
        q = q.strip().upper()
        if not q:
            return []
        hits = [i for i in self.items.values()
                if i.kind in ("equity", "index") and (q in i.id or q in i.name.upper())]
        hits.sort(key=lambda i: (not i.id.startswith(q), len(i.id)))
        return hits[:limit]

    def counts(self) -> dict:
        c: dict[str, int] = {}
        for i in self.items.values():
            c[i.kind] = c.get(i.kind, 0) + 1
        return c


def build_demo_universe(cal: MarketCalendar, today: date, derivatives: bool = True) -> Universe:
    """Stocks and indices with the built-in base prices; derivatives too unless told not to."""
    u = Universe()
    for sym, name, px, vol in _STOCKS:
        u.add(Instrument(sym, "equity", name, sym, base_price=px, vol=vol, groww_symbol=f"NSE-{sym}",
                         yahoo=f"{sym}.NS"))
    monthly = cal.monthly_expiries(today, 3)
    for sym, name, px, lot, weekly, step, ysym in _INDICES:
        u.add(Instrument(sym, "index", name, sym, base_price=px, vol=0.14, lot_size=lot,
                         groww_symbol=f"NSE-{sym}", yahoo=ysym))
        if derivatives:
            _add_derivs(u, cal, sym, px, 0.14, lot, step, monthly,
                        cal.weekly_expiries(today, 4) if weekly else [], span=40)
    if not derivatives:
        return u
    from .config import settings
    for sym, name, px, vol in _STOCKS:
        if sym in settings.fno_stock_underlyings:
            _add_derivs(u, cal, sym, px, vol, _STOCK_LOTS.get(sym, 1), strike_step_for(px), monthly, [],
                        span=15)
    return u


def _add_derivs(u: Universe, cal: MarketCalendar, und: str, px: float, vol: float, lot: int,
                step: float, monthly: list[date], weekly: list[date], span: int) -> None:
    for e in monthly:
        f = fut_symbol(und, e)
        u.add(Instrument(f, "future", f"{und} future", und, expiry=e, lot_size=lot,
                         groww_symbol=groww_fut(und, e), base_price=px, vol=vol))
    atm = round(px / step) * step
    for e in sorted(set(monthly) | set(weekly)):
        is_weekly = e in weekly and e not in monthly
        for k in range(-span, span + 1):
            strike = atm + k * step
            if strike <= 0:
                continue
            for ot in ("CE", "PE"):
                sid = opt_symbol(und, e, strike, ot, is_weekly)
                u.add(Instrument(sid, "option", f"{und} option", und, expiry=e, strike=strike,
                                 opt_type=ot, lot_size=lot, groww_symbol=groww_opt(und, e, strike, ot),
                                 base_price=px, vol=vol, weekly=is_weekly))


# ---------------------------------------------------------------------- Groww CSV (live mode)
def _num(s: str | None) -> float | None:
    try:
        v = float(str(s).strip())
    except (TypeError, ValueError):
        return None
    return v if v == v else None


def parse_groww_csv(text: str, today: date, window_days: int = 75,
                    stock_underlyings: tuple[str, ...] = ()) -> Universe:
    """Build the universe from Groww's public instrument.csv.

    The column names come from Groww's documentation; they have NOT been checked against a real
    download (the build computer could not reach Groww). The parser is tolerant: a row it cannot
    read is counted in `Universe.skipped` by reason, never a crash."""
    u = Universe()
    sk = u.skipped

    def skip(why: str) -> None:
        sk[why] = sk.get(why, 0) + 1

    index_names = {i[0]: i for i in _INDICES}
    stocks = {s[0]: s for s in _STOCKS}
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        try:
            exch = (row.get("exchange") or "").strip().upper()
            seg = (row.get("segment") or "").strip().upper()
            typ = (row.get("instrument_type") or "").strip().upper()
            ts = (row.get("trading_symbol") or "").strip()
            gs = (row.get("groww_symbol") or "").strip()
            if exch != "NSE" or not ts:
                skip("not_nse")
                continue
            lot = int(_num(row.get("lot_size")) or 1)
            tick = _num(row.get("tick_size")) or 0.05
            if tick >= 1:       # some files give the tick in paise
                tick = tick / 100
            if row.get("is_reserved", "").strip() in ("1", "1.0", "true", "True"):
                skip("reserved")
                continue
            if seg == "CASH":
                sym = _INDEX_ALIASES.get(ts, ts)
                if sym in index_names and typ in ("IDX", "INDEX", ""):
                    _s, name, px, ilot, _w, _st, ysym = index_names[sym]
                    u.add(Instrument(sym, "index", name, sym, base_price=px, vol=0.14, lot_size=ilot,
                                     groww_symbol=gs or f"NSE-{ts}", yahoo=ysym))
                elif typ == "EQ" and (row.get("series") or "EQ").strip().upper() in ("EQ", ""):
                    base = stocks.get(ts)
                    u.add(Instrument(ts, "equity", (row.get("name") or ts).strip().title()
                                     if not base else base[1], ts, tick=tick,
                                     groww_symbol=gs or f"NSE-{ts}",
                                     base_price=base[2] if base else 0.0, vol=base[3] if base else 0.25,
                                     yahoo=f"{ts}.NS"))
                else:
                    skip("cash_other")
            elif seg == "FNO":
                und = (row.get("underlying_symbol") or "").strip()
                exp_raw = (row.get("expiry_date") or "").strip()
                if not und or not exp_raw:
                    skip("fno_missing_fields")
                    continue
                exp = datetime.strptime(exp_raw[:10], "%Y-%m-%d").date()
                if exp < today or (exp - today).days > window_days:
                    skip("fno_outside_window")
                    continue
                if und not in index_names and und not in stock_underlyings:
                    skip("fno_underlying_not_wanted")
                    continue
                if typ == "FUT":
                    u.add(Instrument(ts, "future", f"{und} future", und, expiry=exp, lot_size=lot,
                                     tick=tick, groww_symbol=gs))
                elif typ in ("CE", "PE"):
                    strike = _num(row.get("strike_price"))
                    if strike is None:
                        skip("option_no_strike")
                        continue
                    # NSE stores some strikes multiplied by 100 in raw feeds; Groww's file is in rupees.
                    u.add(Instrument(ts, "option", f"{und} option", und, expiry=exp, strike=strike,
                                     opt_type=typ, lot_size=lot, tick=tick, groww_symbol=gs))
                else:
                    skip("fno_other_type")
            else:
                skip("other_segment")
        except Exception:
            skip("unreadable_row")
    return u
