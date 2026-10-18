"""Paper-trading engine for the Indian market: stocks (CNC delivery / MIS intraday) and
futures & options (NRML carry / MIS intraday), lot-based, with Indian charges.

Model (close to how a broker's risk system behaves, so the lessons transfer):

* Every position is its own trade (no netting or averaging) with its own blocked margin.
* `cash` changes only when a trade closes (realised P&L) or charges are paid.
* equity    = cash + unrealised P&L
* available = cash - margin of open positions - margin reserved by pending limit orders
* Market orders fill at the last price. Limit orders fill when the price touches them (valid for
  the trading day). Stop-losses fill at the price seen when they trigger. Targets fill at the target.
* MIS positions are squared off automatically at the square-off time (15:20 IST by default).
* Leveraged positions (MIS, futures, short options) are closed by the "risk system" after losing
  90% of their margin.
* Futures and options are cash-settled on their expiry day at 15:30 IST using the underlying's price.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import bs
from .calendar_in import MarketClock
from .charges import settlement_charges
from .config import settings
from .db import Order, Snapshot, Trade, User
from .instruments import Instrument
from .prices import PriceHub
from .risk import (check_product, check_stop_side, fee_close, fee_open, liq_price, margin_required,
                   pnl, qty_step, risk_at_stop, size_position, snap_price)
from .stats import max_drawdown_pct

log = logging.getLogger("engine")
LOCK = threading.RLock()


class TradeError(Exception):
    """A user-facing problem with a trade request (shown verbatim in the UI)."""


def split_tags(raw: str) -> list[str]:
    return [t for t in (raw or "").split(",") if t]


def join_tags(tags: list[str]) -> str:
    clean: list[str] = []
    for t in tags[:8]:
        t = t.strip().replace(",", " ")[:24]
        if t and t not in clean:
            clean.append(t)
    return ",".join(clean)


def inr(x: float) -> str:
    return f"₹{x:,.2f}"


class Engine:
    def __init__(self, hub: PriceHub, clock: MarketClock):
        self.hub = hub
        self.clock = clock

    # ------------------------------------------------------------------ instruments
    def inst_of(self, t: Trade) -> Instrument:
        """The instrument a trade is on, rebuilt from the trade itself if the list dropped it."""
        inst = self.hub.universe.get(t.symbol)
        if inst is not None:
            return inst
        return Instrument(t.symbol, t.kind, t.label or t.symbol, t.underlying or t.symbol,
                          expiry=date.fromisoformat(t.expiry) if t.expiry else None,
                          strike=t.strike, opt_type=t.opt_type or None, lot_size=t.lot_size or 1)

    def _last(self, t: Trade) -> float:
        q = self.hub.last(t.symbol)
        if q is not None:
            return q.price
        return t.last_price if t.last_price is not None else t.entry_price

    # ------------------------------------------------------------------ views
    def trade_view(self, t: Trade, price: float | None = None) -> dict:
        base = {
            "id": t.id, "symbol": t.symbol, "label": t.label or t.symbol, "kind": t.kind,
            "underlying": t.underlying, "expiry": t.expiry or None, "strike": t.strike,
            "opt_type": t.opt_type or None, "lot_size": t.lot_size, "product": t.product,
            "side": t.side, "qty": t.qty, "lots": t.qty / t.lot_size if t.kind != "equity" else None,
            "entry": t.entry_price, "margin": t.margin, "liq": t.liq_price, "sl": t.sl, "tp": t.tp,
            "opened_at": t.opened_at, "entry_type": t.entry_type, "value": t.entry_price * t.qty,
            "fee_open": t.fee_open, "initial_risk": t.initial_risk, "status": t.status,
            "note": t.note, "setup": t.setup, "tags": split_tags(t.tags),
            "emotion": t.emotion, "rating": t.rating, "lesson": t.lesson,
        }
        if t.status == "open":
            p = price if price is not None else self._last(t)
            upnl = pnl(t.side, t.entry_price, p, t.qty)
            base.update({
                "price": p, "upnl": upnl,
                "upnl_pct": upnl / t.margin * 100 if t.margin else 0.0,
                "r_now": upnl / t.initial_risk if t.initial_risk else None,
                "liq_distance_pct": abs(p - t.liq_price) / p * 100 if (t.liq_price and p) else None,
            })
        else:
            if t.side == "long":
                mfe = (t.peak_price - t.entry_price) / t.entry_price * 100
                mae = (t.entry_price - t.trough_price) / t.entry_price * 100
            else:
                mfe = (t.entry_price - t.trough_price) / t.entry_price * 100
                mae = (t.peak_price - t.entry_price) / t.entry_price * 100
            base.update({
                "exit": t.exit_price, "closed_at": t.closed_at, "reason": t.close_reason,
                "fee_close": t.fee_close, "gross_pnl": t.gross_pnl, "pnl": t.pnl,
                "r": t.r_multiple, "mfe_pct": max(mfe, 0.0), "mae_pct": max(mae, 0.0),
            })
        return base

    def order_view(self, o: Order) -> dict:
        inst = self.hub.universe.get(o.symbol)
        return {"id": o.id, "symbol": o.symbol, "label": inst.label if inst else o.symbol,
                "kind": inst.kind if inst else "equity", "lot_size": inst.lot_size if inst else 1,
                "side": o.side, "product": o.product, "qty": o.qty, "limit_price": o.limit_price,
                "sl": o.sl, "tp": o.tp, "reserved": o.reserved, "created_at": o.created_at}

    # ------------------------------------------------------------------ account
    def _open_trades(self, db: Session, user: User) -> list[Trade]:
        return list(db.scalars(select(Trade).where(Trade.user_id == user.id, Trade.status == "open")))

    def _pending_orders(self, db: Session, user: User) -> list[Order]:
        return list(db.scalars(select(Order).where(Order.user_id == user.id, Order.status == "pending")))

    def equity(self, db: Session, user: User) -> float:
        return user.cash + sum(pnl(t.side, t.entry_price, self._last(t), t.qty)
                               for t in self._open_trades(db, user))

    def state(self, db: Session, user: User) -> dict:
        trades = self._open_trades(db, user)
        orders = self._pending_orders(db, user)
        self.hub.want([t.symbol for t in trades] + [o.symbol for o in orders])
        positions, upnl = [], 0.0
        for t in trades:
            view = self.trade_view(t, self._last(t))
            upnl += view["upnl"]
            positions.append(view)
        margin_used = sum(t.margin for t in trades)
        reserved = sum(o.reserved for o in orders)
        equity = user.cash + upnl
        self._roll_day(db, user, equity)
        day_pnl = equity - user.day_start_equity
        limit_pct = user.daily_loss_pct
        locked = bool(limit_pct > 0 and day_pnl <= -user.day_start_equity * limit_pct / 100)
        return {
            "cash": user.cash, "equity": equity, "unrealized": upnl,
            "margin_used": margin_used, "reserved": reserved,
            "available": user.cash - margin_used - reserved,
            "start_balance": user.start_balance, "season": user.season,
            "return_pct": (equity / user.start_balance - 1) * 100,
            "day_pnl": day_pnl, "day_start_equity": user.day_start_equity, "locked": locked,
            "positions": positions, "orders": [self.order_view(o) for o in orders],
        }

    def _roll_day(self, db: Session, user: User, equity: float) -> None:
        today = self.clock.today_str()
        if user.day_start_date != today:
            user.day_start_date = today
            user.day_start_equity = equity
            db.commit()

    def snapshot(self, db: Session, user: User, equity: float | None = None) -> None:
        eq = self.equity(db, user) if equity is None else equity
        db.add(Snapshot(user_id=user.id, season=user.season, ts=time.time(), equity=eq))

    # ------------------------------------------------------------------ opening
    def _get_inst(self, symbol: str) -> Instrument:
        inst = self.hub.universe.get(symbol)
        if inst is None:
            raise TradeError("Unknown instrument.")
        return inst

    def _market_gate(self, inst: Instrument, product: str) -> None:
        if not self.clock.is_open():
            st = self.clock.status()
            nxt = f" Next open: {st['next_open'][:16].replace('T', ' ')} IST." if st["next_open"] else ""
            raise TradeError(f"The market is closed.{nxt}")
        if not self.clock.force_open and product == "MIS" and \
                self.clock.now().time() >= self.clock.squareoff_t:
            raise TradeError(f"New intraday (MIS) orders stop at {self.clock.squareoff_t:%H:%M} IST, "
                             f"when open MIS positions are squared off.")
        if inst.expiry and self.clock.expired(inst.expiry):
            raise TradeError(f"{inst.label} has expired.")

    def place_order(self, db: Session, user: User, req) -> dict:
        inst = self._get_inst(req.symbol)
        perr = check_product(inst, req.product, req.side)
        if perr:
            raise TradeError(perr)
        self._market_gate(inst, req.product)
        q = self.hub.fresh(inst.id)
        if q is None:
            raise TradeError(f"{inst.label} has no live price right now, so it cannot be traded.")
        st = self.state(db, user)
        if st["equity"] <= 0:
            raise TradeError("Your account balance is empty. Reset the account to start again.")
        if st["locked"]:
            raise TradeError("Daily loss limit reached: new trades are locked until tomorrow's session. "
                             "You can still close positions.")
        if len(st["positions"]) + len(st["orders"]) >= settings.max_open_positions:
            raise TradeError(f"You can have at most {settings.max_open_positions} open positions "
                             f"and orders at once.")

        is_limit = req.type == "limit"
        if is_limit:
            if req.limit_price is None:
                raise TradeError("Enter a limit price.")
            if req.side == "long" and req.limit_price >= q.price:
                raise TradeError(f"A buy limit must be below the current price ({q.price:,.2f}). "
                                 f"Use a market order to buy now.")
            if req.side == "short" and req.limit_price <= q.price:
                raise TradeError(f"A sell limit must be above the current price ({q.price:,.2f}). "
                                 f"Use a market order to sell now.")
            ref = snap_price(req.limit_price, inst.tick)
        else:
            ref = q.price
        sl = snap_price(req.sl, inst.tick) if req.sl is not None else None
        tp = snap_price(req.tp, inst.tick) if req.tp is not None else None
        serr = check_stop_side(req.side, ref, sl, tp)
        if serr:
            raise TradeError(serr)
        if user.require_sl and sl is None:
            raise TradeError("Your risk rules require a stop-loss on every trade.")

        und_px = self.hub.underlying_price(inst)
        step = qty_step(inst)
        if req.lots is not None and inst.kind != "equity":
            qty = float(req.lots * inst.lot_size)
        elif req.qty is not None:
            qty = float(req.qty)
        elif req.risk_pct is not None and sl is not None:
            qty = size_position(inst, req.product, req.side, st["equity"], req.risk_pct, ref, sl,
                                underlying_price=und_px)["qty"]
        else:
            raise TradeError("Enter a quantity, or a risk % together with a stop-loss.")
        if inst.kind != "equity" and abs(qty / step - round(qty / step)) > 1e-9:
            raise TradeError(f"{inst.underlying} trades in lots of {step}. Enter a whole number of lots.")
        if inst.kind == "equity" and abs(qty - round(qty)) > 1e-9:
            raise TradeError("Shares must be a whole number.")
        if qty < step:
            raise TradeError(f"Size is too small. The minimum is {step} "
                             f"({'1 lot' if step > 1 else '1 share'}).")

        margin = margin_required(inst, req.product, req.side, ref, qty, und_px)
        fee = fee_open(inst, req.product, req.side, ref, qty)
        if sl is not None:
            risk = risk_at_stop(inst, req.product, req.side, ref, sl, qty)
            risk_pct = risk / st["equity"] * 100
            if risk_pct > user.max_risk_pct + 1e-9:
                raise TradeError(
                    f"This trade risks {risk_pct:.1f}% of your equity ({inr(risk)}) if the stop hits, "
                    f"above your {user.max_risk_pct:g}% limit. Reduce the size or tighten the stop.")
        if margin + fee > st["available"] + 1e-9:
            raise TradeError(f"Not enough free margin: this needs {inr(margin + fee)} but you have "
                             f"{inr(max(st['available'], 0))}. Lower the size, or use MIS for intraday leverage.")

        tags = join_tags(req.tags)
        if is_limit:
            order = Order(user_id=user.id, season=user.season, symbol=inst.id, side=req.side,
                          product=req.product, qty=qty, limit_price=ref, sl=sl, tp=tp,
                          reserved=margin + fee, day=self.clock.today_str(), created_at=time.time(),
                          note=req.note, setup=req.setup, tags=tags)
            db.add(order)
            db.commit()
            return {"kind": "order", "id": order.id}
        trade = self._open_trade(db, user, inst, req.side, req.product, qty, q.price, sl, tp,
                                 "market", req.note, req.setup, tags)
        return {"kind": "position", "id": trade.id}

    def _open_trade(self, db: Session, user: User, inst: Instrument, side: str, product: str,
                    qty: float, price: float, sl: float | None, tp: float | None, entry_type: str,
                    note: str, setup: str, tags: str) -> Trade:
        und_px = self.hub.underlying_price(inst)
        margin = margin_required(inst, product, side, price, qty, und_px)
        fee = fee_open(inst, product, side, price, qty)
        user.cash -= fee
        trade = Trade(
            user_id=user.id, season=user.season, symbol=inst.id, kind=inst.kind,
            underlying=inst.underlying, expiry=inst.expiry.isoformat() if inst.expiry else "",
            strike=inst.strike, opt_type=inst.opt_type or "", lot_size=inst.lot_size,
            label=inst.label, product=product, side=side, qty=qty, entry_price=price,
            last_price=price, margin=margin, liq_price=liq_price(inst, product, side, price, qty, margin),
            entry_type=entry_type, sl=sl, tp=tp, initial_sl=sl,
            initial_risk=abs(pnl(side, price, sl, qty)) if sl is not None else None,
            fee_open=fee, opened_at=self.clock.now().timestamp(), peak_price=price, trough_price=price,
            status="open", note=note, setup=setup, tags=tags)
        db.add(trade)
        db.commit()
        self.hub.want([inst.id])
        return trade

    # ------------------------------------------------------------------ editing / closing
    def _get_trade(self, db: Session, user: User, trade_id: int, status: str | None = None) -> Trade:
        t = db.get(Trade, trade_id)
        if t is None or t.user_id != user.id or (status and t.status != status):
            raise TradeError("Trade not found.")
        return t

    def set_stops(self, db: Session, user: User, trade_id: int, sl: float | None,
                  tp: float | None) -> Trade:
        t = self._get_trade(db, user, trade_id, "open")
        inst = self.inst_of(t)
        q = self.hub.fresh(t.symbol)
        if q is None:
            raise TradeError(f"{t.label} has no live price right now (market closed or feed down).")
        sl = snap_price(sl, inst.tick) if sl is not None else None
        tp = snap_price(tp, inst.tick) if tp is not None else None
        err = check_stop_side(t.side, q.price, sl, tp)
        if err:
            raise TradeError(err.replace("entry price", "current price"))
        if sl is not None and t.liq_price is not None and (
                (t.side == "long" and sl <= t.liq_price) or (t.side == "short" and sl >= t.liq_price)):
            raise TradeError(f"Your stop-loss is beyond the auto square-off price ({t.liq_price:,.2f}).")
        t.sl, t.tp = sl, tp
        if sl is not None and t.initial_risk is None:
            t.initial_sl = sl
            t.initial_risk = abs(pnl(t.side, t.entry_price, sl, t.qty))
        db.commit()
        return t

    def close_trade(self, db: Session, user: User, trade_id: int) -> Trade:
        t = self._get_trade(db, user, trade_id, "open")
        if not self.clock.is_open():
            raise TradeError("The market is closed, so positions cannot be closed right now.")
        q = self.hub.fresh(t.symbol)
        if q is None:
            raise TradeError(f"{t.label} has no live price right now, so it can't be closed.")
        self._close(db, user, t, q.price, "manual")
        return t

    def _close(self, db: Session, user: User, t: Trade, price: float, reason: str,
               fee: float | None = None) -> None:
        inst = self.inst_of(t)
        if fee is None:
            fee = fee_close(inst, t.product, t.side, price, t.qty)
        gross = pnl(t.side, t.entry_price, price, t.qty)
        t.status = "closed"
        t.exit_price = price
        t.closed_at = time.time()
        t.close_reason = reason
        t.fee_close = fee
        t.gross_pnl = gross
        t.pnl = gross - t.fee_open - fee
        t.r_multiple = t.pnl / t.initial_risk if t.initial_risk else None
        user.cash += gross - fee
        db.flush()
        self.snapshot(db, user)
        db.commit()

    def cancel_order(self, db: Session, user: User, order_id: int) -> None:
        o = db.get(Order, order_id)
        if o is None or o.user_id != user.id or o.status != "pending":
            raise TradeError("Order not found.")
        o.status = "cancelled"
        o.closed_at = time.time()
        db.commit()

    def update_journal(self, db: Session, user: User, trade_id: int, data: dict) -> Trade:
        t = self._get_trade(db, user, trade_id)
        for key in ("note", "setup", "emotion", "lesson"):
            if key in data and data[key] is not None:
                setattr(t, key, data[key])
        if "tags" in data and data["tags"] is not None:
            t.tags = join_tags(data["tags"])
        if "rating" in data:
            t.rating = data["rating"]
        db.commit()
        return t

    def update_settings(self, db: Session, user: User, data: dict) -> None:
        if "require_sl" in data:
            user.require_sl = bool(data["require_sl"])
        if "max_risk_pct" in data:
            user.max_risk_pct = float(data["max_risk_pct"])
        if "daily_loss_pct" in data:
            user.daily_loss_pct = float(data["daily_loss_pct"])
        db.commit()

    def reset_account(self, db: Session, user: User) -> None:
        for t in self._open_trades(db, user):
            self._close(db, user, t, self._last(t), "reset", fee=0.0)
        for o in self._pending_orders(db, user):
            o.status = "cancelled"
            o.closed_at = time.time()
        user.season += 1
        user.season_started_at = time.time()
        user.cash = user.start_balance
        user.day_start_equity = user.start_balance
        user.day_start_date = self.clock.today_str()
        db.flush()
        self.snapshot(db, user, user.start_balance)
        db.commit()

    # ------------------------------------------------------------------ price ticks
    def process_tick(self, db: Session) -> int:
        """Settle expiries, expire day orders, fill limit orders, square off MIS, fire stops/targets.
        Returns how many events happened."""
        events = self._settle_expired(db)
        events += self._expire_orders(db)
        if not self.clock.is_open():
            return events

        for o in list(db.scalars(select(Order).where(Order.status == "pending"))):
            q = self.hub.fresh(o.symbol)
            if q is None:
                continue
            touched = q.price <= o.limit_price if o.side == "long" else q.price >= o.limit_price
            if not touched:
                continue
            user = db.get(User, o.user_id)
            inst = self._get_inst(o.symbol)
            self._open_trade(db, user, inst, o.side, o.product, o.qty, o.limit_price, o.sl, o.tp,
                             "limit", o.note, o.setup, o.tags)
            o.status = "filled"
            o.closed_at = time.time()
            db.commit()
            events += 1

        dirty = False
        for t in list(db.scalars(select(Trade).where(Trade.status == "open"))):
            q = self.hub.fresh(t.symbol)
            if q is None:
                continue
            p = q.price
            t.last_price = p
            if p > t.peak_price:
                t.peak_price = p
            if p < t.trough_price:
                t.trough_price = p
            dirty = True
            long_ = t.side == "long"
            user = db.get(User, t.user_id)
            if t.product == "MIS" and self.clock.squareoff_due(t.opened_at):
                reason = "squareoff"
            elif t.liq_price is not None and ((long_ and p <= t.liq_price) or (not long_ and p >= t.liq_price)):
                reason = "rms"
            elif t.sl is not None and ((long_ and p <= t.sl) or (not long_ and p >= t.sl)):
                reason = "sl"
            elif t.tp is not None and ((long_ and p >= t.tp) or (not long_ and p <= t.tp)):
                reason = "tp"
            else:
                continue
            log.info("auto-close trade %s %s %s: %s at seen price %.2f (source %s, %.1fs old)",
                     t.id, t.side, t.symbol, reason, p, q.source, time.time() - q.ts)
            if reason == "rms":
                self._close(db, user, t, t.liq_price, "rms")
            elif reason == "tp":
                self._close(db, user, t, t.tp, "tp")
            else:
                self._close(db, user, t, p, reason)
            events += 1
            dirty = False
        if dirty:
            db.commit()
        return events

    def _expire_orders(self, db: Session) -> int:
        """Limit orders are day orders: they lapse when the session ends (not in demo 'always open')."""
        n = 0
        today = self.clock.today_str()
        for o in list(db.scalars(select(Order).where(Order.status == "pending"))):
            inst = self.hub.universe.get(o.symbol)
            gone = inst is None or (inst.expiry and self.clock.expired(inst.expiry))
            lapsed = (not self.clock.force_open) and (o.day != today or self.clock.day_over())
            if gone or lapsed:
                o.status = "cancelled"
                o.closed_at = time.time()
                n += 1
        if n:
            db.commit()
        return n

    def _settle_expired(self, db: Session) -> int:
        n = 0
        for t in list(db.scalars(select(Trade).where(Trade.status == "open",
                                                     Trade.kind.in_(("future", "option"))))):
            if not t.expiry or not self.clock.expired(date.fromisoformat(t.expiry)):
                continue
            q = self.hub.last(t.underlying)
            if q is None:
                continue                      # wait until the underlying has a price
            spot = q.price
            if t.kind == "future":
                settle = spot
            else:
                settle = bs.intrinsic(t.opt_type, spot, t.strike)
            fee = settlement_charges(t.kind, t.side, spot, t.strike, t.opt_type or None, t.qty)
            user = db.get(User, t.user_id)
            log.info("expiry settlement trade %s %s at %.2f (underlying %.2f)", t.id, t.symbol, settle, spot)
            self._close(db, user, t, settle, "expiry", fee=fee)
            n += 1
        return n

    # ------------------------------------------------------------------ housekeeping
    def housekeeping(self, db: Session) -> None:
        """Every minute: roll the daily-loss baseline and sample equity."""
        open_by_user: dict[int, list[Trade]] = {}
        for t in db.scalars(select(Trade).where(Trade.status == "open")):
            open_by_user.setdefault(t.user_id, []).append(t)
        now = time.time()
        today = self.clock.today_str()
        for user in db.scalars(select(User)):
            trades = open_by_user.get(user.id, [])
            equity = user.cash + sum(pnl(t.side, t.entry_price, self._last(t), t.qty) for t in trades)
            if user.day_start_date != today:
                user.day_start_date = today
                user.day_start_equity = equity
            last = db.scalar(select(Snapshot).where(
                Snapshot.user_id == user.id, Snapshot.season == user.season
            ).order_by(Snapshot.ts.desc()).limit(1))
            if last is None or abs(last.equity - equity) > 1.0 or now - last.ts > 3600:
                db.add(Snapshot(user_id=user.id, season=user.season, ts=now, equity=equity))
        db.commit()

    # ------------------------------------------------------------------ leaderboard
    def leaderboard(self, db: Session, period: str) -> list[dict]:
        days = {"7d": 7, "30d": 30}.get(period)
        cutoff = time.time() - days * 86400 if days else None
        open_by_user: dict[int, list[Trade]] = {}
        for t in db.scalars(select(Trade).where(Trade.status == "open")):
            open_by_user.setdefault(t.user_id, []).append(t)
        rows: list[dict] = []
        for user in db.scalars(select(User)):
            closed = list(db.scalars(select(Trade).where(
                Trade.user_id == user.id, Trade.season == user.season, Trade.status == "closed")))
            if len(closed) < settings.min_trades_leaderboard:
                continue
            if cutoff is not None and not any(t.closed_at and t.closed_at >= cutoff for t in closed):
                continue
            equity = user.cash + sum(pnl(t.side, t.entry_price, self._last(t), t.qty)
                                     for t in open_by_user.get(user.id, []))
            if cutoff is None:
                base = user.start_balance
            else:
                snap = db.scalar(select(Snapshot).where(
                    Snapshot.user_id == user.id, Snapshot.season == user.season,
                    Snapshot.ts <= cutoff).order_by(Snapshot.ts.desc()).limit(1))
                base = snap.equity if snap else user.start_balance
            wins = sum(1 for t in closed if t.pnl > 0)
            rows.append({"username": user.username, "equity": equity,
                         "return_pct": (equity / base - 1) * 100 if base > 0 else 0.0,
                         "trades": len(closed), "win_rate": wins / len(closed) * 100,
                         "_uid": user.id, "_season": user.season})
        rows.sort(key=lambda r: r["return_pct"], reverse=True)
        rows = rows[:50]
        for i, r in enumerate(rows, 1):
            uid, season = r.pop("_uid"), r.pop("_season")
            snaps = [s.equity for s in db.scalars(select(Snapshot).where(
                Snapshot.user_id == uid, Snapshot.season == season).order_by(Snapshot.ts))]
            r["max_drawdown_pct"] = max_drawdown_pct(snaps)
            r["rank"] = i
        return rows
