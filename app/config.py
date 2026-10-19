"""Runtime settings, read once from environment variables (the app never reads a .env file)."""
from __future__ import annotations

import os
from dataclasses import dataclass


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


def _list(name: str, default: str) -> tuple[str, ...]:
    raw = os.getenv(name, default)
    return tuple(x.strip() for x in raw.split(",") if x.strip())


def _database_url() -> str:
    url = (os.getenv("DATABASE_URL") or "").strip()
    if not url:
        return "sqlite:///./firstrade.db"
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


@dataclass(frozen=True)
class Settings:
    app_name: str
    database_url: str
    price_source: str                 # "live" or "demo"
    providers: tuple[str, ...]        # live cash-price sources, in order: angel, groww, yahoo
    chain_providers: tuple[str, ...]  # live option-chain sources, in order: angel, groww, nse
    angel_api_key: str
    angel_client_code: str
    angel_mpin: str
    angel_totp_secret: str
    groww_access_token: str
    groww_api_key: str
    groww_api_secret: str
    invite_code: str                  # when set, sign-up needs this code (live data = private group)
    admin_username: str               # this user may see /api/admin/users
    start_balance: float
    cookie_secure: bool
    session_days: int
    min_trades_leaderboard: int
    max_open_positions: int
    tick_seconds: float
    force_market_open: bool           # demo convenience: treat the market as always open
    squareoff_time: str               # MIS positions are closed automatically at this IST time
    stale_after: float                # seconds after which a live quote is "not live"
    mis_leverage: float               # MIS intraday: margin = value / this
    futures_margin_pct: float         # approx. SPAN+exposure margin for futures, % of value
    option_sell_margin_pct: float     # approx. margin for SHORT options, % of underlying value
    fno_stock_underlyings: tuple[str, ...]
    fno_expiry_window_days: int
    default_max_risk_pct: float
    default_daily_loss_pct: float


def _read_angel_file() -> dict[str, str]:
    from pathlib import Path
    for p in [Path("Angel one API.txt"), Path("angel_api.txt"), Path("data/Angel one API.txt")]:
        if p.is_file():
            try:
                res = {}
                for line in p.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if "=" in line and not line.startswith("#"):
                        k, v = line.split("=", 1)
                        k, v = k.strip().upper(), v.strip()
                        if "CONSUMER_KEY" in k or "API_KEY" in k:
                            res["api_key"] = v
                        elif "CLIENT" in k:
                            res["client_code"] = v
                        elif "MPIN" in k:
                            res["mpin"] = v
                        elif "TOTP" in k:
                            res["totp_secret"] = v
                if res.get("api_key") and res.get("client_code"):
                    return res
            except Exception:
                pass
    return {}


def load_settings() -> Settings:
    source = os.getenv("PRICE_SOURCE", "live").strip().lower()
    demo = source == "demo"
    angel_f = _read_angel_file()
    return Settings(
        app_name=os.getenv("APP_NAME", "FirsTrade"),
        database_url=_database_url(),
        price_source=source,
        providers=_list("PROVIDERS", "angel,groww,yahoo"),
        chain_providers=_list("CHAIN_PROVIDERS", "angel,groww,nse"),
        angel_api_key=os.getenv("ANGEL_API_KEY", "").strip() or os.getenv("CONSUMER_KEY", "").strip() or angel_f.get("api_key", ""),
        angel_client_code=os.getenv("ANGEL_CLIENT_CODE", "").strip() or os.getenv("ANGELONE_CLIENT_ID", "").strip() or angel_f.get("client_code", ""),
        angel_mpin=os.getenv("ANGEL_MPIN", "").strip() or os.getenv("MPIN", "").strip() or angel_f.get("mpin", ""),
        angel_totp_secret=os.getenv("ANGEL_TOTP_SECRET", "").strip() or os.getenv("TOTP_SECRET", "").strip() or angel_f.get("totp_secret", ""),
        groww_access_token=os.getenv("GROWW_ACCESS_TOKEN", "").strip(),
        groww_api_key=os.getenv("GROWW_API_KEY", "").strip(),
        groww_api_secret=os.getenv("GROWW_API_SECRET", "").strip(),
        invite_code=os.getenv("INVITE_CODE", "").strip(),
        admin_username=os.getenv("ADMIN_USERNAME", "").strip().lower(),
        start_balance=_float("START_BALANCE", 1_000_000.0),
        cookie_secure=_bool("COOKIE_SECURE", False),
        session_days=_int("SESSION_DAYS", 30),
        min_trades_leaderboard=_int("MIN_TRADES_LEADERBOARD", 5),
        max_open_positions=_int("MAX_OPEN_POSITIONS", 15),
        tick_seconds=_float("TICK_SECONDS", 1.0),
        force_market_open=_bool("FORCE_MARKET_OPEN", demo),
        squareoff_time=os.getenv("SQUAREOFF_TIME", "15:20"),
        stale_after=_float("STALE_AFTER", 30.0),
        mis_leverage=_float("MIS_LEVERAGE", 5.0),
        futures_margin_pct=_float("FUTURES_MARGIN_PCT", 11.0),
        option_sell_margin_pct=_float("OPTION_SELL_MARGIN_PCT", 12.0),
        fno_stock_underlyings=_list(
            "FNO_STOCK_UNDERLYINGS", "RELIANCE,HDFCBANK,ICICIBANK,SBIN,TCS,INFY,AXISBANK,ITC"),
        fno_expiry_window_days=_int("FNO_EXPIRY_WINDOW_DAYS", 75),
        default_max_risk_pct=_float("DEFAULT_MAX_RISK_PCT", 2.0),
        default_daily_loss_pct=_float("DEFAULT_DAILY_LOSS_PCT", 5.0),
    )


settings = load_settings()
