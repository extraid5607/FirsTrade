import os
import tempfile

# Must be set before the app modules are imported.
_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["PRICE_SOURCE"] = "demo"
os.environ["MIN_TRADES_LEADERBOARD"] = "1"
os.environ["COOKIE_SECURE"] = "false"
os.environ["INVITE_CODE"] = ""
os.environ["ADMIN_USERNAME"] = "boss"

import time
from datetime import date, datetime
from types import SimpleNamespace

import pytest

from app import db as dbm
from app.calendar_in import IST, MarketCalendar, MarketClock
from app.engine import Engine
from app.instruments import build_demo_universe
from app.prices import PriceHub
from app.providers.base import Quote

TODAY = date(2026, 10, 15)          # a Thursday and a normal trading day


class FakeNow:
    """A clock the test controls (IST)."""

    def __init__(self, text="2026-10-15 10:00"):
        self.set(text)

    def set(self, text):
        self.dt = datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=IST)

    def __call__(self):
        return self.dt


@pytest.fixture()
def now():
    return FakeNow()


@pytest.fixture(scope="session")
def universe():
    return build_demo_universe(MarketCalendar(), TODAY)


@pytest.fixture()
def clock(now):
    return MarketClock(MarketCalendar(), force_open=False, squareoff="15:20", now_fn=now)


class StubHub(PriceHub):
    """The real hub (real freshness rules), with prices the test sets."""

    def set(self, iid, price, age=0.0, prev=None):
        self.quotes[iid] = Quote(price=price, ts=time.time() - age, prev_close=prev, source="test")


@pytest.fixture()
def hub(universe, clock):
    h = StubHub(universe, clock, demo=False)
    h.set("RELIANCE", 1200.0)
    h.set("TCS", 2400.0)
    h.set("NIFTY", 23000.0)
    h.set("NIFTY26OCTFUT", 23050.0)
    h.set("NIFTY26OCT23000CE", 200.0)
    h.set("NIFTY26OCT23000PE", 180.0)
    h.set("NIFTY26OCT24000CE", 20.0)
    h.set("NIFTY26OCT22000PE", 25.0)
    h.set("NIFTY26O1923000CE", 90.0)
    return h


@pytest.fixture()
def fresh_db():
    dbm.Base.metadata.drop_all(dbm.engine)
    dbm.Base.metadata.create_all(dbm.engine)
    yield
    dbm.Base.metadata.drop_all(dbm.engine)


@pytest.fixture()
def engine(hub, clock, fresh_db):
    return Engine(hub, clock)


@pytest.fixture()
def db(fresh_db):
    with dbm.SessionLocal() as s:
        yield s


@pytest.fixture()
def user(db, clock):
    from app import auth
    return auth.create_user(db, "tester", "correct horse battery", clock.today_str())


def order(**kw):
    """Build an order request without going through HTTP validation."""
    base = dict(symbol="RELIANCE", side="long", product="CNC", type="market", qty=None, lots=None,
                risk_pct=None, limit_price=None, sl=None, tp=None, note="", setup="", tags=[])
    base.update(kw)
    return SimpleNamespace(**base)
