from types import SimpleNamespace as NS

import pytest

from app import auth
from app.stats import compute_stats, max_drawdown_pct


def trade(pnl, r=None, t0=0, t1=100, symbol="NIFTY", side="long", setup="", emotion="", reason="manual",
          entry=100.0, peak=110.0, trough=95.0, risk=None, kind="option", product="NRML"):
    return NS(pnl=pnl, r_multiple=r, opened_at=t0, closed_at=t1, symbol=symbol, underlying=symbol,
              kind=kind, product=product, side=side, setup=setup, emotion=emotion,
              close_reason=reason, fee_open=0.5, fee_close=0.5, entry_price=entry,
              peak_price=peak, trough_price=trough, initial_risk=risk)


def test_drawdown():
    assert max_drawdown_pct([100, 120, 90, 130, 100]) == pytest.approx(25.0)
    assert max_drawdown_pct([100, 101, 102]) == 0.0
    assert max_drawdown_pct([]) == 0.0


def test_core_stats():
    ts = [trade(100, 2.0, t1=10, risk=50), trade(-50, -1.0, t1=20, risk=50), trade(50, 1.0, t1=30, risk=50),
          trade(-50, -1.0, t1=40, risk=50), trade(0, 0.0, t1=50)]
    s = compute_stats(ts, [(0, 1000), (10, 1100), (20, 1050), (30, 1100), (40, 1050)], 1000)
    assert s["trades"] == 5 and s["wins"] == 2 and s["losses"] == 2
    assert s["win_rate"] == pytest.approx(40.0)
    assert s["profit_factor"] == pytest.approx(150 / 100)
    assert s["payoff_ratio"] == pytest.approx(75 / 50)
    assert s["expectancy"] == pytest.approx(10.0)
    assert s["expectancy_r"] == pytest.approx(0.2)
    assert s["with_stop_pct"] == pytest.approx(80.0)
    assert s["longest_win_streak"] == 1 and s["longest_loss_streak"] == 1
    assert s["max_drawdown_pct"] == pytest.approx(50 / 1100 * 100)
    assert s["by_symbol"][0]["key"] == "NIFTY"


def test_profit_factor_undefined_without_losses_and_empty_case():
    s = compute_stats([trade(10), trade(5)], [], 1000)
    assert s["profit_factor"] is None and s["win_rate"] == 100.0
    assert compute_stats([], [], 1000)["trades"] == 0


def test_streak_and_excursions():
    ts = [trade(1, t1=1), trade(1, t1=2), trade(1, t1=3), trade(-1, t1=4), trade(-1, t1=5)]
    s = compute_stats(ts, [], 1000)
    assert s["longest_win_streak"] == 3 and s["current_streak"] == -2
    assert s["avg_mfe_pct"] == pytest.approx(10.0) and s["avg_mae_pct"] == pytest.approx(5.0)


def test_india_groupings():
    ts = [trade(10, kind="option", product="NRML"), trade(-5, kind="equity", product="MIS", symbol="TCS"),
          trade(7, kind="future", product="NRML")]
    s = compute_stats(ts, [], 1000)
    assert {r["key"] for r in s["by_class"]} == {"Options", "Stocks", "Futures"}
    assert {r["key"] for r in s["by_product"]} == {"NRML", "MIS"}
    assert {r["key"] for r in s["by_symbol"]} == {"NIFTY", "TCS"}


def test_passwords_and_users(db):
    h = auth.hash_password("hunter2hunter2")
    assert auth.verify_password("hunter2hunter2", h) and not auth.verify_password("nope", h)
    assert not auth.verify_password("x", "garbage")
    auth.create_user(db, "Alice_1", "longenoughpw", "2000-01-01")
    with pytest.raises(auth.AuthError, match="taken"):
        auth.create_user(db, "alice_1", "longenoughpw", "2000-01-01")
    with pytest.raises(auth.AuthError, match="Username"):
        auth.create_user(db, "a b", "longenoughpw", "2000-01-01")
    with pytest.raises(auth.AuthError, match="Password"):
        auth.create_user(db, "bob_1", "short", "2000-01-01")
    assert auth.authenticate(db, "ALICE_1", "longenoughpw").username == "Alice_1"
    with pytest.raises(auth.AuthError):
        auth.authenticate(db, "alice_1", "wrong password")
    with pytest.raises(auth.AuthError):
        auth.authenticate(db, "nobody_here", "whatever password")


def test_sessions_and_rate_limiter(db):
    u = auth.create_user(db, "carol_1", "longenoughpw", "2000-01-01")
    tok = auth.new_session(db, u.id)
    assert auth.user_for_token(db, tok).id == u.id
    auth.end_session(db, tok)
    assert auth.user_for_token(db, tok) is None and auth.user_for_token(db, None) is None
    rl = auth.RateLimiter(2, 60)
    assert rl.allow("k") and rl.allow("k") and not rl.allow("k") and rl.allow("other")


def test_invite_code_gate(db):
    object.__setattr__(auth.settings, "invite_code", "SECRET1")
    try:
        with pytest.raises(auth.AuthError, match="invite"):
            auth.create_user(db, "dave_1", "longenoughpw", "2000-01-01", "wrong")
        assert auth.create_user(db, "dave_1", "longenoughpw", "2000-01-01", " SECRET1 ").username == "dave_1"
    finally:
        object.__setattr__(auth.settings, "invite_code", "")
