"""Trade-journal statistics. Pure functions: they take plain objects, never touch the DB."""
from __future__ import annotations

from datetime import datetime
from typing import Iterable

from .calendar_in import IST

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def max_drawdown_pct(equities: Iterable[float]) -> float:
    """Largest peak-to-trough fall of an equity series, in percent (positive number)."""
    peak = None
    worst = 0.0
    for e in equities:
        if peak is None or e > peak:
            peak = e
        if peak and peak > 0:
            worst = max(worst, (peak - e) / peak * 100)
    return worst


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def _group(trades: list, key) -> list[dict]:
    buckets: dict[str, list] = {}
    for t in trades:
        k = key(t)
        if k:
            buckets.setdefault(k, []).append(t)
    rows = []
    for k, ts in buckets.items():
        wins = sum(1 for t in ts if t.pnl > 0)
        rows.append({"key": k, "trades": len(ts), "pnl": sum(t.pnl for t in ts),
                     "win_rate": wins / len(ts) * 100})
    rows.sort(key=lambda r: r["pnl"], reverse=True)
    return rows


def _streaks(trades: list) -> tuple[int, int, int]:
    """(longest win streak, longest loss streak, current streak: + wins / - losses)."""
    best_w = best_l = run = 0
    for t in sorted(trades, key=lambda x: x.closed_at):
        if t.pnl > 0:
            run = run + 1 if run > 0 else 1
        elif t.pnl < 0:
            run = run - 1 if run < 0 else -1
        else:
            run = 0
        best_w = max(best_w, run)
        best_l = max(best_l, -run)
    return best_w, best_l, run


def _excursions(t) -> tuple[float, float]:
    """Max favourable / adverse excursion as % of entry price."""
    if t.side == "long":
        mfe = (t.peak_price - t.entry_price) / t.entry_price * 100
        mae = (t.entry_price - t.trough_price) / t.entry_price * 100
    else:
        mfe = (t.entry_price - t.trough_price) / t.entry_price * 100
        mae = (t.peak_price - t.entry_price) / t.entry_price * 100
    return max(mfe, 0.0), max(mae, 0.0)


def compute_stats(trades: list, snapshots: list[tuple[float, float]], start_balance: float) -> dict:
    """`trades` are CLOSED trades. `snapshots` are (timestamp, equity) pairs, oldest first."""
    n = len(trades)
    curve = [[ts, eq] for ts, eq in snapshots]
    if len(curve) > 400:  # keep the payload small
        step = len(curve) / 400
        curve = [curve[int(i * step)] for i in range(400)] + [curve[-1]]
    out: dict = {
        "trades": n, "equity_curve": curve,
        "max_drawdown_pct": max_drawdown_pct(e for _, e in snapshots),
    }
    if n == 0:
        return out

    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl < 0]
    gross_win = sum(t.pnl for t in wins)
    gross_loss = -sum(t.pnl for t in losses)
    avg_win = _mean([t.pnl for t in wins])
    avg_loss = _mean([t.pnl for t in losses])
    rs = [t.r_multiple for t in trades if t.r_multiple is not None]
    holds = [t.closed_at - t.opened_at for t in trades if t.closed_at]
    best_w, best_l, current = _streaks(trades)
    exc = [_excursions(t) for t in trades]

    out.update({
        "wins": len(wins), "losses": len(losses),
        "win_rate": len(wins) / n * 100,
        "total_pnl": sum(t.pnl for t in trades),
        "total_fees": sum(t.fee_open + t.fee_close for t in trades),
        "avg_win": avg_win, "avg_loss": avg_loss,
        "payoff_ratio": (avg_win / -avg_loss) if avg_win and avg_loss else None,
        # None when there are no losing trades yet (the UI shows an infinity sign)
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else None,
        "expectancy": sum(t.pnl for t in trades) / n,
        "expectancy_r": _mean(rs),
        "avg_r_win": _mean([r for r in rs if r > 0]),
        "avg_r_loss": _mean([r for r in rs if r < 0]),
        "best_trade": max(t.pnl for t in trades),
        "worst_trade": min(t.pnl for t in trades),
        "avg_hold_seconds": _mean(holds),
        "longest_win_streak": best_w, "longest_loss_streak": best_l, "current_streak": current,
        "with_stop_pct": sum(1 for t in trades if t.initial_risk) / n * 100,
        "avg_mfe_pct": _mean([e[0] for e in exc]),
        "avg_mae_pct": _mean([e[1] for e in exc]),
        "by_symbol": _group(trades, lambda t: t.underlying or t.symbol),
        "by_class": _group(trades, lambda t: {"equity": "Stocks", "future": "Futures",
                                              "option": "Options"}.get(t.kind, t.kind)),
        "by_product": _group(trades, lambda t: t.product),
        "by_side": _group(trades, lambda t: t.side),
        "by_setup": _group(trades, lambda t: t.setup),
        "by_emotion": _group(trades, lambda t: t.emotion),
        "by_exit": _group(trades, lambda t: t.close_reason),
        "by_weekday": _group(
            trades, lambda t: WEEKDAYS[datetime.fromtimestamp(t.opened_at, IST).weekday()]),
    })
    return out
