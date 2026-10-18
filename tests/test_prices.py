import time

import pytest

from app.prices import PriceHub
from app.providers.base import Provider, ProviderError, Quote
from tests.conftest import StubHub


class Scripted(Provider):
    """A provider whose answers the test scripts."""

    def __init__(self, name, prices=None, error=None):
        self.name, self.prices, self.error, self.calls = name, prices or {}, error, 0

    async def quotes(self, insts):
        self.calls += 1
        if self.error:
            raise ProviderError(self.error)
        return {i.id: Quote(price=self.prices[i.id], ts=time.time(), prev_close=self.prices.get("prev")) 
                for i in insts if i.id in self.prices}


def inst(hub, iid):
    return hub.universe.get(iid)


async def test_failover_to_next_source_when_first_fails(universe, clock):
    a, b = Scripted("groww", error="boom"), Scripted("yahoo", {"RELIANCE": 1201.0})
    hub = PriceHub(universe, clock, demo=False, providers={"groww": a, "yahoo": b})
    await hub._poll_into([inst(hub, "RELIANCE")], ["groww", "yahoo"])
    assert hub.quotes["RELIANCE"].price == 1201.0 and hub.quotes["RELIANCE"].source == "yahoo"
    assert "groww" in hub.errors


async def test_failing_source_is_skipped_for_a_while(universe, clock):
    a, b = Scripted("groww", error="boom"), Scripted("yahoo", {"RELIANCE": 1201.0})
    hub = PriceHub(universe, clock, demo=False, providers={"groww": a, "yahoo": b})
    for _ in range(4):
        await hub._poll_into([inst(hub, "RELIANCE")], ["groww", "yahoo"])
    assert a.calls == hub.FAILS_BEFORE_SKIP


async def test_a_different_source_that_disagrees_is_rejected(universe, clock):
    hub = StubHub(universe, clock, demo=False)
    hub.quotes["RELIANCE"] = Quote(1200.0, time.time(), source="groww")
    hub._origin["RELIANCE"] = "groww"
    assert hub._accept(inst(hub, "RELIANCE"), "yahoo", Quote(1260.0, time.time())) == "reject"   # 5% off
    assert hub._accept(inst(hub, "RELIANCE"), "yahoo", Quote(1203.0, time.time())) == "ok"
    assert hub.quotes["RELIANCE"].source == "yahoo"


async def test_big_jump_from_same_source_is_held_until_confirmed(universe, clock):
    hub = StubHub(universe, clock, demo=False)
    hub.quotes["RELIANCE"] = Quote(1200.0, time.time(), source="groww")
    hub._origin["RELIANCE"] = "groww"
    i = inst(hub, "RELIANCE")
    assert hub._accept(i, "groww", Quote(1400.0, time.time())) == "hold"
    assert hub.quotes["RELIANCE"].price == 1200.0                       # unchanged while held
    assert hub._accept(i, "groww", Quote(1401.0, time.time())) == "ok"   # a second reading agrees
    assert hub.quotes["RELIANCE"].price == 1401.0


async def test_impossible_prices_are_dropped(universe, clock):
    hub = StubHub(universe, clock, demo=False)
    i = inst(hub, "RELIANCE")
    assert hub._accept(i, "groww", Quote(0.0, time.time())) == "reject"
    assert hub._accept(i, "groww", Quote(1500.0, time.time(), prev_close=1000.0)) == "reject"  # +50% in a day
    assert hub._accept(i, "groww", Quote(1010.0, time.time(), prev_close=1000.0)) == "ok"


async def test_fresh_requires_open_market_and_recent_quote(hub, now):
    assert hub.fresh("RELIANCE")
    hub.set("RELIANCE", 1200.0, age=300)
    assert hub.fresh("RELIANCE") is None and hub.last("RELIANCE")        # stale: shown but not tradable
    hub.set("RELIANCE", 1200.0)
    now.set("2026-10-15 16:00")
    assert hub.fresh("RELIANCE") is None and hub.last("RELIANCE")


async def test_futures_never_fall_back_to_a_cash_price(universe, clock):
    """Only Groww may price derivatives; Yahoo has nothing for them."""
    hub = PriceHub(universe, clock, demo=False, providers={"groww": Scripted("groww", error="x"),
                                                           "yahoo": Scripted("yahoo", {"NIFTY26OCTFUT": 1.0})})
    hub.want(["NIFTY26OCTFUT"])
    await hub._poll_into([inst(hub, "NIFTY26OCTFUT")], ["groww"])
    assert "NIFTY26OCTFUT" not in hub.quotes


async def test_chain_falls_back_to_second_source(universe, clock):
    from datetime import date

    class Chain(Provider):
        def __init__(self, name, fail):
            self.name, self.fail = name, fail

        async def option_chain(self, und, expiry):
            if self.fail:
                raise ProviderError("down")
            return {"underlying": und, "expiry": expiry.isoformat(), "underlying_ltp": 23000.0,
                    "lot_size": 65, "source": self.name, "summary": {"atm": 23000},
                    "rows": [{"strike": 23000, "ce": {"id": None, "ltp": 200.0}, "pe": {"id": None, "ltp": 180.0}}]}

    hub = PriceHub(universe, clock, demo=False,
                   chain_providers={"groww": Chain("groww", True), "nse": Chain("nse", False)})
    hub.want(["NIFTY26OCT23000CE"])
    data = await hub.chain("NIFTY", date(2026, 10, 27))
    assert data["source"] == "nse" and data["rows"][0]["ce"]["id"] == "NIFTY26OCT23000CE"
    assert hub.quotes["NIFTY26OCT23000CE"].price == 200.0 and hub.quotes["NIFTY26OCT23000CE"].source == "nse"


async def test_chain_error_is_reported_when_every_source_fails(universe, clock):
    from datetime import date
    hub = PriceHub(universe, clock, demo=False)
    with pytest.raises(RuntimeError):
        await hub.chain("NIFTY", date(2026, 10, 27))
