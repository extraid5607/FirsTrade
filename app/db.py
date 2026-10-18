"""Database engine, session factory and table definitions (ZeroPaper India)."""
from __future__ import annotations

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


_is_sqlite = settings.database_url.startswith("sqlite")
engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
    pool_pre_ping=True,
)

if _is_sqlite:
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - trivial
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()

SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=True)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(20))
    username_lower: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    pw_hash: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[float] = mapped_column(Float)

    # `cash` = starting balance + realised P&L - charges. Margin blocked by open positions is
    # reserved out of it (see engine.state), not deducted.
    cash: Mapped[float] = mapped_column(Float)
    start_balance: Mapped[float] = mapped_column(Float)
    season: Mapped[int] = mapped_column(Integer, default=1)   # +1 on every account reset
    season_started_at: Mapped[float] = mapped_column(Float)

    # Risk rules (chosen by the user, enforced by the engine)
    require_sl: Mapped[bool] = mapped_column(Boolean, default=False)
    max_risk_pct: Mapped[float] = mapped_column(Float, default=2.0)
    daily_loss_pct: Mapped[float] = mapped_column(Float, default=5.0)

    # Equity at the start of the current IST trading day (for the daily loss limit)
    day_start_equity: Mapped[float] = mapped_column(Float)
    day_start_date: Mapped[str] = mapped_column(String(10), default="")


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[float] = mapped_column(Float)


class Order(Base):
    """A pending limit order (valid for the trading day). Margin + charges are reserved."""
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    season: Mapped[int] = mapped_column(Integer, default=1)
    symbol: Mapped[str] = mapped_column(String(40))
    side: Mapped[str] = mapped_column(String(5))
    product: Mapped[str] = mapped_column(String(4), default="MIS")
    qty: Mapped[float] = mapped_column(Float)
    limit_price: Mapped[float] = mapped_column(Float)
    sl: Mapped[float | None] = mapped_column(Float, nullable=True)
    tp: Mapped[float | None] = mapped_column(Float, nullable=True)
    reserved: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(10), default="pending")  # pending|filled|cancelled
    day: Mapped[str] = mapped_column(String(10), default="")            # IST date it was placed
    created_at: Mapped[float] = mapped_column(Float)
    closed_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    setup: Mapped[str] = mapped_column(String(40), default="")
    tags: Mapped[str] = mapped_column(String(200), default="")

    __table_args__ = (Index("ix_orders_status", "status"),)


class Trade(Base):
    """One position, from open to close. Open positions and history share this table.

    The contract details (kind, underlying, expiry, strike ...) are copied onto the trade, so
    it can still be valued and settled after the instrument list is refreshed."""
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    season: Mapped[int] = mapped_column(Integer, default=1)
    symbol: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(8), default="equity")      # equity|future|option
    underlying: Mapped[str] = mapped_column(String(20), default="")
    expiry: Mapped[str] = mapped_column(String(10), default="")
    strike: Mapped[float | None] = mapped_column(Float, nullable=True)
    opt_type: Mapped[str] = mapped_column(String(2), default="")
    lot_size: Mapped[int] = mapped_column(Integer, default=1)
    label: Mapped[str] = mapped_column(String(80), default="")
    product: Mapped[str] = mapped_column(String(4), default="MIS")
    side: Mapped[str] = mapped_column(String(5))
    qty: Mapped[float] = mapped_column(Float)
    entry_price: Mapped[float] = mapped_column(Float)
    last_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    margin: Mapped[float] = mapped_column(Float)
    liq_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    entry_type: Mapped[str] = mapped_column(String(6), default="market")
    sl: Mapped[float | None] = mapped_column(Float, nullable=True)
    tp: Mapped[float | None] = mapped_column(Float, nullable=True)
    initial_sl: Mapped[float | None] = mapped_column(Float, nullable=True)
    initial_risk: Mapped[float | None] = mapped_column(Float, nullable=True)  # rupees at first stop
    fee_open: Mapped[float] = mapped_column(Float, default=0.0)
    opened_at: Mapped[float] = mapped_column(Float)
    peak_price: Mapped[float] = mapped_column(Float)
    trough_price: Mapped[float] = mapped_column(Float)

    status: Mapped[str] = mapped_column(String(6), default="open", index=True)  # open|closed
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    closed_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    close_reason: Mapped[str] = mapped_column(String(12), default="")  # manual|sl|tp|squareoff|rms|expiry|reset
    fee_close: Mapped[float] = mapped_column(Float, default=0.0)
    gross_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    pnl: Mapped[float] = mapped_column(Float, default=0.0)  # net of all charges
    r_multiple: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Journal
    note: Mapped[str] = mapped_column(Text, default="")
    setup: Mapped[str] = mapped_column(String(40), default="")
    tags: Mapped[str] = mapped_column(String(200), default="")
    emotion: Mapped[str] = mapped_column(String(20), default="")
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lesson: Mapped[str] = mapped_column(Text, default="")

    __table_args__ = (Index("ix_trades_user_status", "user_id", "status"),)


class Snapshot(Base):
    """Equity over time, for the equity curve, drawdown and period returns."""
    __tablename__ = "snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    season: Mapped[int] = mapped_column(Integer, default=1)
    ts: Mapped[float] = mapped_column(Float)
    equity: Mapped[float] = mapped_column(Float)

    __table_args__ = (Index("ix_snap_user_ts", "user_id", "season", "ts"),)


def init_db() -> None:
    Base.metadata.create_all(engine)
