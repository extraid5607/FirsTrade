"""FastAPI application: JSON API, live price WebSocket, and the static website (ZeroPaper India)."""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import auth
from .calendar_in import MarketCalendar, MarketClock
from .charges import describe as charges_describe
from .config import settings
from .db import SessionLocal, Snapshot, Trade, User, init_db
from .engine import LOCK, Engine, TradeError
from .instruments import INSTRUMENT_CSV_URL, build_demo_universe, parse_groww_csv
from .prices import PriceHub
from .providers.base import TF_SECONDS
from .providers.groww import GrowwProvider
from .providers.nse import NseProvider
from .providers.yahoo import YahooProvider
from .risk import allowed_products, floor_to_step, preview_position, qty_step, size_position
from .schemas import Credentials, JournalReq, OrderReq, ResetReq, SettingsReq, StopsReq, TokenReq
from .stats import compute_stats

log = logging.getLogger("zeropaper")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

COOKIE = "zp_session"
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
DEMO = settings.price_source == "demo"

cal = MarketCalendar()
clock = MarketClock(cal, force_open=settings.force_market_open, squareoff=settings.squareoff_time)


def _make_hub() -> tuple[PriceHub, GrowwProvider | None]:
    if DEMO:
        return PriceHub(build_demo_universe(cal, clock.today()), clock, demo=True), None
    uni = build_demo_universe(cal, clock.today(), derivatives=False)   # stocks + indices until Groww's list loads
    groww = GrowwProvider(uni)
    providers = {"groww": groww}
    if "yahoo" in settings.providers:
        providers["yahoo"] = YahooProvider()
    chain = {"groww": groww}
    if "nse" in settings.chain_providers:
        chain["nse"] = NseProvider(uni)
    return PriceHub(uni, clock, demo=False, providers=providers, chain_providers=chain), groww


hub, groww_provider = _make_hub()
engine = Engine(hub, clock)
login_limiter = auth.RateLimiter(limit=10, window=300)
register_limiter = auth.RateLimiter(limit=5, window=3600)
_universe_day = ""


# ----------------------------------------------------------------------- universe loading
async def load_universe() -> None:
    """(Re)build the instrument list. Live: Groww's public CSV; if that fails, stocks + indices only."""
    global _universe_day
    today = clock.today()
    if DEMO:
        hub.set_universe(build_demo_universe(cal, today))
        hub.universe_source = "demo (built-in)"
    else:
        try:
            async with httpx.AsyncClient(timeout=60.0) as c:
                r = await c.get(INSTRUMENT_CSV_URL)
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}")
            uni = parse_groww_csv(r.text, today, settings.fno_expiry_window_days,
                                  settings.fno_stock_underlyings)
            if not uni.get("NIFTY") or not uni.of_kind("equity"):
                raise RuntimeError("the list did not contain NIFTY and stocks (format changed?)")
            for base in build_demo_universe(cal, today, derivatives=False).items.values():
                if base.id not in uni.items:     # keep our known stocks/indices if the file lacks one
                    uni.add(base)
            hub.set_universe(uni)
            hub.universe_source = "Groww instrument list"
            log.info("instrument list loaded: %s, skipped %s", uni.counts(), uni.skipped)
        except Exception as exc:
            hub.universe_source = f"built-in stocks and indices only (Groww list failed: {type(exc).__name__}: {exc})"
            log.warning("instrument list not loaded: %s", exc)
    _universe_day = clock.today_str()


# ----------------------------------------------------------------------- background loops
def _tick_sync() -> None:
    with LOCK, SessionLocal() as db:
        open_trades = list(db.scalars(select(Trade).where(Trade.status == "open")))
        pending_orders = list(db.scalars(select(Order).where(Order.status == "pending")))
        if open_trades or pending_orders:
            hub.want([t.symbol for t in open_trades] + [o.symbol for o in pending_orders], ttl=90.0)
        engine.process_tick(db)


def _housekeeping_sync() -> None:
    with LOCK, SessionLocal() as db:
        engine.housekeeping(db)
        auth.purge_expired(db)
    login_limiter.sweep()
    register_limiter.sweep()


async def _every(seconds: float, fn) -> None:
    while True:
        try:
            await asyncio.to_thread(fn)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("background task %s failed", fn.__name__)
        await asyncio.sleep(seconds)


async def _universe_watch() -> None:
    while True:
        await asyncio.sleep(600)
        try:
            if clock.today_str() != _universe_day:
                await load_universe()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("universe refresh failed")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    await load_universe()
    await hub.start()
    tasks = [asyncio.create_task(_every(settings.tick_seconds, _tick_sync)),
             asyncio.create_task(_every(60, _housekeeping_sync)),
             asyncio.create_task(_universe_watch())]
    log.info("%s started (price source: %s)", settings.app_name, settings.price_source)
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await hub.stop()


app = FastAPI(title=settings.app_name, lifespan=lifespan, docs_url=None, redoc_url=None)


# ----------------------------------------------------------------------- middleware
@app.middleware("http")
async def guard(request: Request, call_next):
    if request.method in ("POST", "PATCH", "PUT", "DELETE") and request.url.path.startswith("/api/"):
        if request.headers.get("x-requested-with") not in ("firstrade", "zeropaper"):
            return JSONResponse({"detail": "Missing request header."}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; font-src 'self'; connect-src 'self' ws: wss:; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(TradeError)
async def trade_error_handler(_request: Request, exc: TradeError):
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_request: Request, exc: RequestValidationError):
    first = exc.errors()[0] if exc.errors() else {}
    loc = [str(p) for p in first.get("loc", ()) if p not in ("body", "query", "path")]
    field = loc[-1] if loc else "request"
    msg = str(first.get("msg", "invalid value"))[:120]
    return JSONResponse({"detail": f"Invalid {field}: {msg}"}, status_code=422)


@app.exception_handler(auth.AuthError)
async def auth_error_handler(_request: Request, exc: auth.AuthError):
    return JSONResponse({"detail": str(exc)}, status_code=400)


# ----------------------------------------------------------------------- dependencies
def get_db():
    with SessionLocal() as db:
        yield db


def optional_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    return auth.user_for_token(db, request.cookies.get(COOKIE))


def current_user(user: User | None = Depends(optional_user)) -> User:
    if user is None:
        raise HTTPException(401, "Please log in.")
    return user


def data_user(user: User | None = Depends(optional_user)) -> User | None:
    """Market data is private in live mode: real prices only go to logged-in students."""
    if settings.price_source == "live" and user is None:
        raise HTTPException(401, "Please log in.")
    return user


def admin_user(user: User = Depends(current_user)) -> User:
    if not settings.admin_username or user.username_lower != settings.admin_username:
        raise HTTPException(403, "Admin only.")
    return user


def _is_admin(u: User) -> bool:
    return bool(settings.admin_username) and u.username_lower == settings.admin_username


def _user_view(u: User) -> dict:
    return {"username": u.username, "season": u.season, "start_balance": u.start_balance,
            "admin": _is_admin(u),
            "settings": {"require_sl": u.require_sl, "max_risk_pct": u.max_risk_pct,
                         "daily_loss_pct": u.daily_loss_pct}}


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(COOKIE, token, max_age=settings.session_days * 86400, httponly=True,
                        samesite="lax", secure=settings.cookie_secure, path="/")


def _ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _instrument(symbol: str):
    inst = hub.universe.get(symbol)
    if inst is None:
        raise HTTPException(404, "Unknown instrument.")
    return inst


# ----------------------------------------------------------------------- auth
@app.post("/api/auth/register")
def register(body: Credentials, request: Request, response: Response, db: Session = Depends(get_db)):
    if not register_limiter.allow(_ip(request)):
        raise HTTPException(429, "Too many sign-ups from this network. Try again later.")
    with LOCK:
        user = auth.create_user(db, body.username.strip(), body.password, clock.today_str(), body.invite)
        engine.snapshot(db, user, user.cash)
        db.commit()
    _set_cookie(response, auth.new_session(db, user.id))
    return {"user": _user_view(user)}


@app.post("/api/auth/login")
def login(body: Credentials, request: Request, response: Response, db: Session = Depends(get_db)):
    if not login_limiter.allow(f"{_ip(request)}|{body.username.lower()}"):
        raise HTTPException(429, "Too many attempts. Wait a few minutes and try again.")
    user = auth.authenticate(db, body.username.strip(), body.password)
    _set_cookie(response, auth.new_session(db, user.id))
    return {"user": _user_view(user)}


@app.post("/api/auth/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    auth.end_session(db, request.cookies.get(COOKIE))
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@app.get("/api/me")
def me(user: User | None = Depends(optional_user)):
    return {
        "app_name": settings.app_name, "demo": hub.demo, "market": clock.status(),
        "invite_required": bool(settings.invite_code),
        "user": _user_view(user) if user else None,
        "limits": {"max_open": settings.max_open_positions,
                   "min_trades_leaderboard": settings.min_trades_leaderboard},
        "notes": {"mis_leverage": settings.mis_leverage, "futures_margin_pct": settings.futures_margin_pct,
                  "option_sell_margin_pct": settings.option_sell_margin_pct},
    }


# ----------------------------------------------------------------------- market data
def _row(inst) -> dict:
    d = inst.public()
    q = hub.last(inst.id)
    d["price"] = q.price if q else None
    d["change"] = q.change_pct if q else None
    d["live"] = bool(hub.fresh(inst.id))
    d["products"] = allowed_products(inst)
    if inst.kind in ("index", "equity"):
        d["fno"] = bool(hub.universe.expiries(inst.id) or hub.universe.futures(inst.id))
    return d


@app.get("/api/market")
def market():
    return clock.status()


@app.get("/api/markets")
def markets(q: str = Query("", max_length=30), user: User | None = Depends(optional_user)):
    items = hub.universe.search(q) if q else hub.universe.watchlist()
    rows = [_row(i) for i in items]
    if settings.price_source == "live" and user is None:       # names only, no real prices
        for r in rows:
            r["price"], r["change"], r["live"] = None, None, False
    return {"markets": rows, "market": clock.status()}


@app.get("/api/prices")
def prices(_u: User | None = Depends(data_user)):
    wanted = [k for k, v in list(hub._wanted.items()) if v > time.time()][:200]
    return {"t": time.time(), "q": hub.snapshot(), "d": hub.quote_view(wanted)}


@app.get("/api/instrument/{symbol}")
def instrument(symbol: str, _u: User | None = Depends(data_user)):
    inst = _instrument(symbol)
    if inst.kind in ("future", "option"):
        hub.want([inst.id])
    d = _row(inst)
    d["products"] = allowed_products(inst)
    d["qty_step"] = qty_step(inst)
    q = hub.last(inst.id)
    if q:
        d["bid"], d["ask"], d["oi"] = q.bid, q.ask, q.oi
    if inst.kind in ("future", "option"):
        d["underlying_price"] = hub.underlying_price(inst)
    return d


@app.get("/api/expiries")
def expiries(underlying: str = Query(max_length=20)):
    if hub.universe.get(underlying) is None:
        raise HTTPException(404, "Unknown underlying.")
    return {"underlying": underlying, "lot_size": hub.universe.lot_size(underlying),
            "options": [e.isoformat() for e in hub.universe.expiries(underlying)],
            "futures": [{"expiry": f.expiry.isoformat(), "id": f.id, "label": f.label}
                        for f in hub.universe.futures(underlying)]}


@app.get("/api/chain")
async def chain(underlying: str = Query(max_length=20), expiry: str = Query(max_length=10),
                strikes: int = Query(15, ge=3, le=60), _u: User | None = Depends(data_user)):
    try:
        e = date.fromisoformat(expiry)
    except ValueError:
        raise HTTPException(400, "Bad expiry date.")
    if e not in hub.universe.expiries(underlying):
        raise HTTPException(404, "No contracts for this expiry.")
    try:
        data = await hub.chain(underlying, e)
    except Exception as exc:
        log.warning("chain %s %s failed: %s", underlying, expiry, exc)
        raise HTTPException(503, f"The option chain is unavailable right now ({str(exc)[:140]}).")
    rows = data["rows"]
    atm = data["summary"]["atm"]
    if atm is not None and len(rows) > 2 * strikes + 1:
        idx = min(range(len(rows)), key=lambda i: abs(rows[i]["strike"] - atm))
        rows = rows[max(0, idx - strikes): idx + strikes + 1]
    ids = [leg["id"] for r in rows for leg in (r.get("ce"), r.get("pe")) if leg and leg.get("id")]
    hub.want(ids, ttl=60)
    return {**data, "rows": rows, "total_strikes": len(data["rows"])}


@app.get("/api/candles")
async def candles(symbol: str = Query(max_length=40), tf: str = "15m",
                  limit: int = Query(300, ge=20, le=600), _u: User | None = Depends(data_user)):
    _instrument(symbol)
    if tf not in TF_SECONDS:
        raise HTTPException(400, "Unknown timeframe.")
    try:
        rows = await hub.candles(symbol, tf, limit)
    except Exception as exc:
        log.warning("candles %s %s failed: %s", symbol, tf, exc)
        raise HTTPException(503, "Chart data is unavailable right now. Try again in a moment.")
    return {"symbol": symbol, "tf": tf, "candles": rows}


@app.get("/api/health")
def health():
    g = groww_provider.status() if groww_provider else None
    return {"ok": True, "price_source": settings.price_source, "market": clock.status(),
            "hub": hub.status(), "groww": g, "charges": {"verified": False},
            "invite_code_set": bool(settings.invite_code)}


@app.websocket("/ws/prices")
async def ws_prices(ws: WebSocket):
    if settings.price_source == "live":
        with SessionLocal() as db:
            ok = auth.user_for_token(db, ws.cookies.get(COOKIE)) is not None
        if not ok:
            await ws.close(code=1008)
            return
    await ws.accept()
    try:
        while True:
            wanted = [k for k, v in list(hub._wanted.items()) if v > time.time()][:200]
            await ws.send_json({"t": time.time(), "q": hub.snapshot(), "d": hub.quote_view(wanted),
                                "open": clock.is_open()})
            await asyncio.sleep(1.0)
    except (WebSocketDisconnect, RuntimeError):
        pass


# ----------------------------------------------------------------------- trading
@app.get("/api/account")
def account(user: User = Depends(current_user), db: Session = Depends(get_db)):
    with LOCK:
        return engine.state(db, user)


@app.post("/api/orders")
def place_order(body: OrderReq, user: User = Depends(current_user), db: Session = Depends(get_db)):
    with LOCK:
        result = engine.place_order(db, user, body)
        return {"result": result, "account": engine.state(db, user)}


@app.delete("/api/orders/{order_id}")
def cancel_order(order_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    with LOCK:
        engine.cancel_order(db, user, order_id)
        return {"account": engine.state(db, user)}


@app.post("/api/trades/{trade_id}/close")
def close_trade(trade_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    with LOCK:
        t = engine.close_trade(db, user, trade_id)
        return {"trade": engine.trade_view(t), "account": engine.state(db, user)}


@app.patch("/api/trades/{trade_id}/stops")
def set_stops(trade_id: int, body: StopsReq, user: User = Depends(current_user),
              db: Session = Depends(get_db)):
    with LOCK:
        engine.set_stops(db, user, trade_id, body.sl, body.tp)
        return {"account": engine.state(db, user)}


@app.get("/api/trades")
def trades(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
           user: User = Depends(current_user), db: Session = Depends(get_db)):
    where = (Trade.user_id == user.id, Trade.season == user.season, Trade.status == "closed")
    total = db.scalar(select(func.count()).select_from(Trade).where(*where)) or 0
    rows = db.scalars(select(Trade).where(*where).order_by(Trade.closed_at.desc())
                      .limit(limit).offset(offset))
    return {"total": total, "trades": [engine.trade_view(t) for t in rows]}


@app.patch("/api/trades/{trade_id}/journal")
def journal(trade_id: int, body: JournalReq, user: User = Depends(current_user),
            db: Session = Depends(get_db)):
    with LOCK:
        t = engine.update_journal(db, user, trade_id, body.model_dump(exclude_unset=True))
        return {"trade": engine.trade_view(t)}


@app.get("/api/stats")
def stats(period: str = Query("all", pattern="^(all|7d|30d)$"),
          user: User = Depends(current_user), db: Session = Depends(get_db)):
    cutoff = None
    if period != "all":
        cutoff = time.time() - {"7d": 7, "30d": 30}[period] * 86400
    q = select(Trade).where(Trade.user_id == user.id, Trade.season == user.season,
                            Trade.status == "closed")
    s = select(Snapshot).where(Snapshot.user_id == user.id, Snapshot.season == user.season)
    if cutoff is not None:
        q = q.where(Trade.closed_at >= cutoff)
        s = s.where(Snapshot.ts >= cutoff)
    closed = list(db.scalars(q))
    snaps = [(x.ts, x.equity) for x in db.scalars(s.order_by(Snapshot.ts))]
    with LOCK:
        snaps.append((time.time(), engine.equity(db, user)))
    return compute_stats(closed, snaps, user.start_balance)


@app.get("/api/risk/size")
def risk_size(symbol: str, side: str = Query(pattern="^(long|short)$"),
              product: str = Query(pattern="^(CNC|MIS|NRML)$"),
              entry: float | None = Query(None, gt=0), stop: float | None = Query(None, gt=0),
              tp: float | None = Query(None, gt=0),
              risk_pct: float | None = Query(None, gt=0, le=100),
              qty: float | None = Query(None, gt=0, lt=1e9), lots: int | None = Query(None, gt=0, le=10000),
              user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Order-ticket preview. Give `risk_pct` (+ `stop`) to size by risk, or `qty`/`lots` directly."""
    inst = _instrument(symbol)
    given = [x is not None for x in (risk_pct, qty, lots)]
    if sum(given) != 1:
        raise HTTPException(400, "Give exactly one of risk_pct, qty or lots.")
    if risk_pct is not None and stop is None:
        raise HTTPException(400, "Sizing by risk needs a stop-loss price.")
    ref = entry
    if ref is None:
        q = hub.last(symbol)
        if q is None:
            raise HTTPException(503, "No price yet for this instrument.")
        ref = q.price
    und = hub.underlying_price(inst)
    with LOCK:
        st = engine.state(db, user)
    try:
        if risk_pct is not None:
            out = size_position(inst, product, side, st["equity"], risk_pct, ref, stop, tp,
                                available=st["available"], max_risk_pct=user.max_risk_pct,
                                underlying_price=und)
        else:
            units = float(lots * inst.lot_size) if lots is not None and inst.kind != "equity" else float(qty or lots)
            out = preview_position(inst, product, side, st["equity"], ref,
                                   floor_to_step(units, qty_step(inst)), stop, tp,
                                   available=st["available"], max_risk_pct=user.max_risk_pct,
                                   underlying_price=und)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return out | {"entry": ref, "available": st["available"], "equity": st["equity"]}


@app.patch("/api/settings")
def update_settings(body: SettingsReq, user: User = Depends(current_user),
                    db: Session = Depends(get_db)):
    with LOCK:
        engine.update_settings(db, user, body.model_dump(exclude_unset=True))
    return {"user": _user_view(user)}


@app.post("/api/account/reset")
def reset_account(body: ResetReq, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not body.confirm:
        raise HTTPException(400, "Confirm the reset.")
    with LOCK:
        engine.reset_account(db, user)
        return {"user": _user_view(user), "account": engine.state(db, user)}


@app.get("/api/leaderboard")
def leaderboard(period: str = Query("all", pattern="^(all|7d|30d)$"), db: Session = Depends(get_db)):
    with LOCK:
        return {"period": period, "rows": engine.leaderboard(db, period),
                "min_trades": settings.min_trades_leaderboard}


# ----------------------------------------------------------------------- admin
@app.get("/api/admin/overview")
def admin_overview(_admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    users = db.scalar(select(func.count()).select_from(User)) or 0
    open_n = db.scalar(select(func.count()).select_from(Trade).where(Trade.status == "open")) or 0
    return {"users": users, "open_positions": open_n, "hub": hub.status(),
            "groww": groww_provider.status() if groww_provider else None,
            "charges": charges_describe()}


@app.post("/api/admin/groww-token")
def admin_groww_token(body: TokenReq, _admin: User = Depends(admin_user)):
    """Paste today's Groww access token here (it expires every day at 06:00 IST). Kept in memory."""
    if groww_provider is None:
        raise HTTPException(400, "Groww is not used in demo mode.")
    groww_provider.set_token(body.token)
    return {"ok": True}


# The website itself (must be mounted last so it does not shadow the API routes).
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
