"""Indian market calendar and clock (all times IST).

Cash and F&O trade Monday to Friday 09:15-15:30 IST except exchange holidays. Index and stock
derivatives expire on Tuesdays (weekly NIFTY, monthly = last Tuesday); when that day is a
holiday the expiry moves to the PREVIOUS trading day.
"""
from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Callable

IST = timezone(timedelta(hours=5, minutes=30))
OPEN_T = time(9, 15)
CLOSE_T = time(15, 30)
TUESDAY = 1
HOLIDAY_FILE = Path(__file__).resolve().parent.parent / "data" / "holidays.json"


def load_holidays(path: Path = HOLIDAY_FILE) -> set[date]:
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError):
        return set()
    out: set[date] = set()
    for key, val in raw.items():
        if key.startswith("_") or not isinstance(val, list):
            continue
        for s in val:
            try:
                out.add(date.fromisoformat(s))
            except ValueError:
                continue
    return out


def now_ist() -> datetime:
    return datetime.now(IST)


class MarketCalendar:
    def __init__(self, holidays: set[date] | None = None):
        self.holidays = holidays if holidays is not None else load_holidays()

    def is_trading_day(self, d: date) -> bool:
        return d.weekday() < 5 and d not in self.holidays

    def next_trading_day(self, d: date) -> date:
        d += timedelta(days=1)
        while not self.is_trading_day(d):
            d += timedelta(days=1)
        return d

    def prev_trading_day(self, d: date) -> date:
        d -= timedelta(days=1)
        while not self.is_trading_day(d):
            d -= timedelta(days=1)
        return d

    def adjust_expiry(self, d: date) -> date:
        """A holiday expiry moves to the previous trading day."""
        while not self.is_trading_day(d):
            d -= timedelta(days=1)
        return d

    @staticmethod
    def last_weekday_of_month(year: int, month: int, weekday: int = TUESDAY) -> date:
        nxt = date(year + (month == 12), month % 12 + 1, 1)
        d = nxt - timedelta(days=1)
        while d.weekday() != weekday:
            d -= timedelta(days=1)
        return d

    def weekly_expiries(self, today: date, n: int = 4) -> list[date]:
        out: list[date] = []
        d = today
        for _ in range(n * 7 + 14):
            if d.weekday() == TUESDAY:
                e = self.adjust_expiry(d)
                if e >= today and e not in out:
                    out.append(e)
                    if len(out) == n:
                        break
            d += timedelta(days=1)
        return out

    def monthly_expiries(self, today: date, n: int = 3) -> list[date]:
        out: list[date] = []
        y, m = today.year, today.month
        while len(out) < n:
            e = self.adjust_expiry(self.last_weekday_of_month(y, m))
            if e >= today:
                out.append(e)
            y, m = y + (m == 12), m % 12 + 1
        return out


class MarketClock:
    def __init__(self, calendar: MarketCalendar, force_open: bool = False,
                 squareoff: str = "15:20", now_fn: Callable[[], datetime] = now_ist):
        self.cal = calendar
        self.force_open = force_open
        h, m = squareoff.split(":")
        self.squareoff_t = time(int(h), int(m))
        self._now = now_fn

    def now(self) -> datetime:
        return self._now()

    def today(self) -> date:
        return self.now().date()

    def today_str(self) -> str:
        return self.today().isoformat()

    def is_open(self) -> bool:
        if self.force_open:
            return True
        n = self.now()
        return self.cal.is_trading_day(n.date()) and OPEN_T <= n.time() < CLOSE_T

    def squareoff_due(self, opened_ts: float) -> bool:
        """True when an intraday (MIS) position must be closed now. Off in demo "always open"
        mode, because the real clock would square positions off at odd moments there."""
        if self.force_open or not self.is_open():
            return False
        n = self.now()
        opened = datetime.fromtimestamp(opened_ts, IST).date()
        return opened < n.date() or n.time() >= self.squareoff_t

    def day_over(self) -> bool:
        n = self.now()
        return n.time() >= CLOSE_T or not self.cal.is_trading_day(n.date())

    def expired(self, expiry: date) -> bool:
        """A contract is dead once its expiry day has closed."""
        n = self.now()
        return expiry < n.date() or (expiry == n.date() and n.time() >= CLOSE_T)

    def status(self) -> dict:
        n = self.now()
        is_open = self.is_open()
        if self.force_open:
            label = "Demo hours: market treated as always open"
        elif is_open:
            label = "Market open"
        elif self.cal.is_trading_day(n.date()) and n.time() < OPEN_T:
            label = "Pre-open: market opens 09:15 IST"
        else:
            label = "Market closed"
        d = n.date()
        if not is_open:
            nxt = d if (self.cal.is_trading_day(d) and n.time() < OPEN_T) else self.cal.next_trading_day(d)
            next_open = datetime.combine(nxt, OPEN_T, IST).isoformat()
        else:
            next_open = None
        return {"open": is_open, "label": label, "forced": self.force_open,
                "now": n.isoformat(timespec="seconds"), "next_open": next_open,
                "squareoff": self.squareoff_t.strftime("%H:%M"),
                "squareoff_active": not self.force_open}
