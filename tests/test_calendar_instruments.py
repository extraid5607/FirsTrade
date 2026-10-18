from datetime import date, datetime

from app.calendar_in import IST, MarketCalendar, MarketClock
from app.instruments import (build_demo_universe, fut_symbol, opt_symbol, parse_groww_csv,
                             strike_text)


def test_holiday_expiry_moves_to_previous_trading_day():
    cal = MarketCalendar()
    # Tuesday 20 Oct 2026 is a holiday -> Monday 19th
    assert cal.weekly_expiries(date(2026, 10, 15), 2)[0] == date(2026, 10, 19)
    # last Tuesday of Nov (24th) is a holiday -> Monday 23rd
    assert date(2026, 11, 23) in cal.monthly_expiries(date(2026, 10, 15), 3)


def test_monthly_is_last_tuesday():
    cal = MarketCalendar()
    assert cal.monthly_expiries(date(2026, 10, 15), 1) == [date(2026, 10, 27)]


def test_trading_day_rules():
    cal = MarketCalendar()
    assert not cal.is_trading_day(date(2026, 10, 17))       # Saturday
    assert not cal.is_trading_day(date(2026, 10, 20))       # holiday
    assert cal.is_trading_day(date(2026, 10, 15))
    assert cal.next_trading_day(date(2026, 10, 19)) == date(2026, 10, 21)


def _clock(text, force=False):
    dt = datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=IST)
    return MarketClock(MarketCalendar(), force_open=force, now_fn=lambda: dt)


def test_market_hours():
    assert not _clock("2026-10-15 09:14").is_open()
    assert _clock("2026-10-15 09:15").is_open()
    assert _clock("2026-10-15 15:29").is_open()
    assert not _clock("2026-10-15 15:30").is_open()
    assert not _clock("2026-10-17 11:00").is_open()
    assert _clock("2026-10-17 11:00", force=True).is_open()


def test_squareoff_due_rules():
    from datetime import timezone
    opened = datetime(2026, 10, 15, 10, 0, tzinfo=IST).timestamp()
    assert not _clock("2026-10-15 15:19").squareoff_due(opened)
    assert _clock("2026-10-15 15:20").squareoff_due(opened)
    # a position from an earlier day is squared off as soon as the market opens
    assert _clock("2026-10-16 09:16").squareoff_due(opened)
    assert not _clock("2026-10-15 15:20", force=True).squareoff_due(opened)


def test_expired_after_close_on_expiry_day():
    c = _clock("2026-10-27 15:29")
    assert not c.expired(date(2026, 10, 27))
    assert _clock("2026-10-27 15:30").expired(date(2026, 10, 27))
    assert _clock("2026-10-28 09:00").expired(date(2026, 10, 27))


def test_symbol_formats():
    assert fut_symbol("NIFTY", date(2026, 10, 27)) == "NIFTY26OCTFUT"
    assert opt_symbol("NIFTY", date(2026, 10, 27), 23000, "CE", False) == "NIFTY26OCT23000CE"
    assert opt_symbol("NIFTY", date(2026, 10, 19), 23000, "PE", True) == "NIFTY26O1923000PE"
    assert opt_symbol("NIFTY", date(2026, 11, 3), 23000, "PE", True) == "NIFTY26N0323000PE"
    assert strike_text(2.5) == "2.5" and strike_text(23000.0) == "23000"


def test_demo_universe_shape(universe):
    c = universe.counts()
    assert c["index"] == 4 and c["equity"] >= 40 and c["future"] > 0 and c["option"] > 1000
    assert universe.lot_size("NIFTY") == 65
    assert universe.get("NIFTY").tradable is False
    assert universe.get("RELIANCE").tradable
    # the monthly expiry that is also a weekly one uses the monthly symbol
    assert "NIFTY26OCT23000CE" in universe
    assert universe.contract("NIFTY", date(2026, 10, 19), 23000, "CE").id == "NIFTY26O1923000CE"


CSV = """exchange,exchange_token,trading_symbol,groww_symbol,name,instrument_type,segment,series,isin,underlying_symbol,underlying_exchange_token,expiry_date,strike_price,lot_size,tick_size,freeze_quantity,is_reserved,buy_allowed,sell_allowed
NSE,2885,RELIANCE,NSE-RELIANCE,RELIANCE INDUSTRIES,EQ,CASH,EQ,INE002A01018,,,,,1,5,0,0,1,1
NSE,26000,NIFTY,NSE-NIFTY,NIFTY 50,IDX,CASH,,,,,,,1,0.05,0,0,1,1
NSE,60103,NIFTY26OCT23000CE,NSE-NIFTY-27Oct26-23000-CE,,CE,FNO,,,NIFTY,26000,2026-10-27,23000,65,0.05,1800,0,1,1
NSE,60104,NIFTY26OCT23000PE,NSE-NIFTY-27Oct26-23000-PE,,PE,FNO,,,NIFTY,26000,2026-10-27,23000,65,0.05,1800,0,1,1
NSE,6,NIFTY26OCTFUT,NSE-NIFTY-27Oct26-FUT,,FUT,FNO,,,NIFTY,26000,2026-10-27,,65,0.05,1800,0,1,1
NSE,7,NIFTY25JANFUT,NSE-NIFTY-30Jan25-FUT,,FUT,FNO,,,NIFTY,26000,2025-01-30,,75,0.05,1800,0,1,1
NSE,8,NIFTY27MARFUT,NSE-NIFTY-30Mar27-FUT,,FUT,FNO,,,NIFTY,26000,2027-03-30,,65,0.05,1800,0,1,1
NSE,9,ZZZ26OCTFUT,NSE-ZZZ-27Oct26-FUT,,FUT,FNO,,,ZZZ,1,2026-10-27,,100,0.05,0,0,1,1
NSE,10,BAD,NSE-BAD,,XX,FNO,,,NIFTY,1,2026-10-27,,1,0.05,0,0,1,1
BSE,11,SENSEX,BSE-SENSEX,,IDX,CASH,,,,,,,1,0.05,0,0,1,1
"""


def test_parse_groww_csv_counts_and_skips():
    u = parse_groww_csv(CSV, date(2026, 10, 15), 75, ("RELIANCE",))
    assert u.counts() == {"equity": 1, "index": 1, "option": 2, "future": 1}
    assert u.lot_size("NIFTY") == 65
    assert u.get("RELIANCE").tick == 0.05          # tick given in paise is converted
    s = u.skipped
    assert s["fno_outside_window"] == 2             # expired and too far away
    assert s["fno_underlying_not_wanted"] == 1
    assert s["fno_other_type"] == 1 and s["not_nse"] == 1


def test_parse_groww_real_instrument_sample():
    from pathlib import Path
    sample_path = Path(__file__).parent / "fixtures" / "instrument_sample.csv"
    assert sample_path.exists(), "instrument_sample.csv fixture must exist"
    text = sample_path.read_text(encoding="utf-8")
    today = date(2026, 10, 18)
    stocks = ("RELIANCE", "HDFCBANK", "ICICIBANK", "SBIN", "TCS", "INFY", "AXISBANK", "ITC")
    u = parse_groww_csv(text, today, 75, stocks)
    c = u.counts()
    assert c["index"] == 4
    assert c["equity"] == 8
    assert c["future"] >= 10
    assert c["option"] >= 50
    assert u.lot_size("NIFTY") == 65
    assert u.lot_size("BANKNIFTY") == 30
    assert u.lot_size("MIDCPNIFTY") == 120
    assert u.lot_size("RELIANCE") == 500
    assert u.get("MIDCPNIFTY").name == "Nifty Midcap Select"
    assert "reserved" in u.skipped and "not_nse" in u.skipped
