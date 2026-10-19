"""Angel One SmartAPI provider (official real-time prices, option chain, candles).

Talking to Angel One SmartAPI:
  POST /rest/auth/angelbroking/user/v1/loginByPassword   TOTP-based auth (MPIN + TOTP)
  POST /rest/secure/angelbroking/market/v1/quote/         bulk LTP, OHLC and market depth
  POST /rest/secure/angelbroking/historical/v1/getCandleData historical candles
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx
import pyotp

from .. import bs
from ..calendar_in import IST
from ..config import settings
from ..instruments import Instrument, Universe
from .base import (TF_SECONDS, AuthExpired, Blocked, Candle, Provider, ProviderError, Quote,
                    summarize_chain)

log = logging.getLogger("angel")
BASE = "https://apiconnect.angelone.in"
MASTER_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"

INDICES_MAP = {
    "NIFTY": ("NSE", "26000"),
    "BANKNIFTY": ("NSE", "26009"),
    "FINNIFTY": ("NSE", "26037"),
    "MIDCPNIFTY": ("NSE", "26074"),
}

INTERVAL_MAP = {
    "1m": "ONE_MINUTE",
    "5m": "FIVE_MINUTE",
    "15m": "FIFTEEN_MINUTE",
    "1h": "ONE_HOUR",
    "1d": "ONE_DAY",
}

SPAN_DAYS = {
    "1m": 4,
    "5m": 12,
    "15m": 35,
    "1h": 120,
    "1d": 365,
}


def _read_file_creds() -> dict[str, str]:
    """Fallback: read credentials from 'Angel one API.txt' if present."""
    paths = [
        Path("Angel one API.txt"),
        Path("angel_api.txt"),
        Path("data/Angel one API.txt"),
        Path("data/angel_api.txt"),
        Path(__file__).resolve().parent.parent.parent / "Angel one API.txt",
    ]
    for p in paths:
        if p.is_file():
            try:
                out = {}
                for line in p.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        k, v = line.split("=", 1)
                        k, v = k.strip().upper(), v.strip()
                        if "CONSUMER_KEY" in k or "API_KEY" in k:
                            out["api_key"] = v
                        elif "CLIENT" in k:
                            out["client_code"] = v
                        elif "MPIN" in k:
                            out["mpin"] = v
                        elif "TOTP" in k:
                            out["totp_secret"] = v
                if out.get("api_key") and out.get("client_code") and out.get("totp_secret"):
                    return out
            except Exception as exc:
                log.warning("could not read %s: %s", p, exc)
    return {}


class AngelProvider(Provider):
    name = "angel"

    def __init__(self, universe: Universe | None = None, client: httpx.AsyncClient | None = None) -> None:
        self.u = universe
        self._http = client or httpx.AsyncClient(timeout=12.0)
        file_creds = _read_file_creds()

        self.api_key = (os.getenv("ANGEL_API_KEY") or os.getenv("CONSUMER_KEY") or file_creds.get("api_key") or "").strip()
        self.client_code = (os.getenv("ANGEL_CLIENT_CODE") or os.getenv("ANGELONE_CLIENT_ID") or file_creds.get("client_code") or "").strip()
        self.mpin = (os.getenv("ANGEL_MPIN") or os.getenv("MPIN") or file_creds.get("mpin") or "").strip()
        self.totp_secret = (os.getenv("ANGEL_TOTP_SECRET") or os.getenv("TOTP_SECRET") or file_creds.get("totp_secret") or "").strip()

        self._jwt_token: str = ""
        self._token_at: float = 0.0
        self._lock = asyncio.Lock()

        self._master_loaded = False
        self._sym_to_token: dict[str, tuple[str, str]] = {}
        self._token_to_inst_id: dict[tuple[str, str], str] = {}
        self._options_by_und_exp: dict[tuple[str, str], list[dict]] = {}

        self.last_error: str | None = None
        self.last_ok: float | None = None

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.client_code and self.mpin and self.totp_secret)

    async def stop(self) -> None:
        await self._http.aclose()

    # ---- master data loading ---------------------------------------------------
    def _ensure_master(self) -> None:
        if self._master_loaded:
            return

        master_path = Path("data/angel_scrip_master.json")
        if not master_path.is_file():
            master_path = Path(__file__).resolve().parent.parent.parent / "data" / "angel_scrip_master.json"

        if not master_path.is_file():
            try:
                log.info("Downloading Angel One scrip master from %s...", MASTER_URL)
                master_path.parent.mkdir(parents=True, exist_ok=True)
                r = httpx.get(MASTER_URL, timeout=45.0)
                if r.status_code == 200:
                    master_path.write_bytes(r.content)
                    log.info("Downloaded Angel master (%.1f MB)", len(r.content) / 1024 / 1024)
            except Exception as exc:
                log.warning("Failed to auto-download Angel master: %s", exc)
                return

        try:
            with open(master_path, "r", encoding="utf-8") as f:
                items = json.load(f)

            for it in items:
                sym = it.get("symbol")
                token = it.get("token")
                exch = it.get("exch_seg")
                if not sym or not token or not exch:
                    continue

                self._sym_to_token[sym] = (exch, token)

                name = it.get("name")
                exp = it.get("expiry")
                if exch == "NFO" and name and exp and it.get("instrumenttype") in ("OPTIDX", "OPTSTK"):
                    self._options_by_und_exp.setdefault((name, exp), []).append(it)

            self._master_loaded = True
            log.info("Angel master indexed %d symbols, %d option series", len(self._sym_to_token), len(self._options_by_und_exp))
        except Exception as exc:
            log.exception("failed to load angel master: %s", exc)

    # ---- authentication --------------------------------------------------------
    async def _ensure_token(self) -> str:
        if self._jwt_token and (time.time() - self._token_at) < 14 * 3600:
            return self._jwt_token

        if not self.configured:
            raise AuthExpired("Angel One API credentials not configured.")

        async with self._lock:
            if self._jwt_token and (time.time() - self._token_at) < 14 * 3600:
                return self._jwt_token

            totp = pyotp.TOTP(self.totp_secret).now()
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-UserType": "USER",
                "X-SourceID": "WEB",
                "X-ClientLocalIP": "127.0.0.1",
                "X-ClientPublicIP": "106.193.147.98",
                "X-MACAddress": "fe80::216e:6507:4b9c:37f9",
                "X-PrivateKey": self.api_key,
            }
            payload = {
                "clientcode": self.client_code,
                "password": self.mpin,
                "totp": totp,
            }

            try:
                r = await self._http.post(f"{BASE}/rest/auth/angelbroking/user/v1/loginByPassword",
                                          json=payload, headers=headers, timeout=12.0)
            except httpx.HTTPError as exc:
                self.last_error = f"network: {type(exc).__name__}"
                raise ProviderError(self.last_error)

            if r.status_code != 200:
                self.last_error = f"auth HTTP {r.status_code}"
                raise AuthExpired(f"Angel One auth failed: HTTP {r.status_code}")

            body = r.json()
            if not body.get("status"):
                err = body.get("message") or body.get("errorcode") or "auth error"
                self.last_error = err
                raise AuthExpired(f"Angel One auth error: {err}")

            jwt = (body.get("data") or {}).get("jwtToken")
            if not jwt:
                raise AuthExpired("Angel One did not return jwtToken.")

            self._jwt_token = jwt
            self._token_at = time.time()
            self.last_error = None
            log.info("Angel One authentication successful.")
            return self._jwt_token

    def _headers(self, jwt: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {jwt}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-UserType": "USER",
            "X-SourceID": "WEB",
            "X-ClientLocalIP": "127.0.0.1",
            "X-ClientPublicIP": "106.193.147.98",
            "X-MACAddress": "fe80::216e:6507:4b9c:37f9",
            "X-PrivateKey": self.api_key,
        }

    # ---- symbol resolution -----------------------------------------------------
    def token_for(self, inst: Instrument) -> tuple[str, str] | None:
        """Return (exchange, token) for an Instrument."""
        self._ensure_master()

        # 1. Standard indices
        if inst.kind == "index":
            idx = INDICES_MAP.get(inst.id)
            if idx:
                return idx

        # 2. Options: format tradingsymbol e.g. NIFTY19OCT2623050CE
        if inst.kind == "option" and inst.expiry and inst.strike and inst.opt_type:
            strike_val = int(inst.strike) if inst.strike.is_integer() else inst.strike
            tradingsymbol = f"{inst.underlying}{inst.expiry:%d%b%y}{strike_val}{inst.opt_type}".upper()
            found = self._sym_to_token.get(tradingsymbol)
            if found:
                return found

        # 3. Equities: e.g. RELIANCE-EQ
        if inst.kind == "equity":
            tradingsymbol = f"{inst.id}-EQ"
            found = self._sym_to_token.get(tradingsymbol)
            if found:
                return found

        # 4. Futures: e.g. NIFTY19OCT26FUT
        if inst.kind == "future" and inst.expiry:
            tradingsymbol = f"{inst.underlying}{inst.expiry:%d%b%y}FUT".upper()
            found = self._sym_to_token.get(tradingsymbol)
            if found:
                return found

        # 5. Direct symbol match
        return self._sym_to_token.get(inst.id)

    # ---- quotes ----------------------------------------------------------------
    async def quotes(self, insts: list[Instrument]) -> dict[str, Quote]:
        if not self.configured or not insts:
            return {}

        jwt = await self._ensure_token()
        self._ensure_master()

        mapped: list[tuple[Instrument, str, str]] = []
        for i in insts:
            res = self.token_for(i)
            if res:
                mapped.append((i, res[0], res[1]))

        if not mapped:
            return {}

        # Angel One supports up to 50 tokens per request
        out: dict[str, Quote] = {}
        now = time.time()
        for batch in [mapped[i:i + 50] for i in range(0, len(mapped), 50)]:
            by_exch: dict[str, list[str]] = {}
            tok_map: dict[str, Instrument] = {}
            for inst, exch, tok in batch:
                by_exch.setdefault(exch, []).append(tok)
                tok_map[tok] = inst

            try:
                r = await self._http.post(
                    f"{BASE}/rest/secure/angelbroking/market/v1/quote",
                    json={"mode": "FULL", "exchangeTokens": by_exch},
                    headers=self._headers(jwt),
                    timeout=8.0,
                )
                if r.status_code == 401:
                    self._jwt_token = ""
                    continue
                if r.status_code != 200:
                    continue

                data = r.json().get("data", {}).get("fetched", [])
                for item in data:
                    tok = str(item.get("symbolToken"))
                    inst = tok_map.get(tok)
                    if not inst:
                        continue

                    ltp = item.get("ltp")
                    if ltp is None or ltp <= 0:
                        continue

                    depth = item.get("depth", {})
                    buys = depth.get("buy", [])
                    sells = depth.get("sell", [])
                    bid = buys[0].get("price") if buys else None
                    ask = sells[0].get("price") if sells else None
                    oi = item.get("opnInterest")
                    vol = item.get("tradeVolume")
                    close = item.get("close")

                    out[inst.id] = Quote(
                        price=float(ltp),
                        ts=now,
                        prev_close=float(close) if close else None,
                        source="angel",
                        bid=float(bid) if bid else None,
                        ask=float(ask) if ask else None,
                        oi=float(oi) if oi else None,
                        volume=float(vol) if vol else None,
                    )
                self.last_ok = now
            except Exception as exc:
                log.warning("angel quote batch failed: %s", exc)

        return out

    # ---- option chain ----------------------------------------------------------
    async def option_chain(self, und: str, expiry: date) -> dict:
        if not self.configured:
            raise AuthExpired("Angel One not configured")

        jwt = await self._ensure_token()
        self._ensure_master()

        exp_str = expiry.strftime("%d%b%Y").upper()
        candidates = self._options_by_und_exp.get((und, exp_str), [])
        if not candidates:
            raise ProviderError(f"No Angel options found for {und} {exp_str}")

        # Fetch underlying spot price first
        idx_info = INDICES_MAP.get(und)
        und_token = idx_info[1] if idx_info else self._sym_to_token.get(f"{und}-EQ", ("NSE", ""))[1]
        und_exch = idx_info[0] if idx_info else "NSE"
        und_ltp = 0.0

        if und_token:
            try:
                r_und = await self._http.post(
                    f"{BASE}/rest/secure/angelbroking/market/v1/quote",
                    json={"mode": "LTP", "exchangeTokens": {und_exch: [und_token]}},
                    headers=self._headers(jwt),
                    timeout=5.0,
                )
                fetched = r_und.json().get("data", {}).get("fetched", [])
                if fetched:
                    und_ltp = float(fetched[0].get("ltp", 0.0))
            except Exception:
                pass

        # Select strikes around ATM
        strikes_map: dict[float, dict[str, dict]] = {}
        for it in candidates:
            stk_raw = it.get("strike")
            if not stk_raw:
                continue
            strike = float(stk_raw) / 100.0
            opt_type = "CE" if it.get("symbol", "").endswith("CE") else "PE"
            strikes_map.setdefault(strike, {})[opt_type] = it

        sorted_strikes = sorted(strikes_map.keys())
        if und_ltp > 0 and len(sorted_strikes) > 40:
            atm_strike = min(sorted_strikes, key=lambda s: abs(s - und_ltp))
            idx = sorted_strikes.index(atm_strike)
            selected_strikes = sorted_strikes[max(0, idx - 20): min(len(sorted_strikes), idx + 21)]
        else:
            selected_strikes = sorted_strikes[:40]

        # Gather tokens to fetch
        tokens_to_fetch = []
        token_info_map = {}
        for stk in selected_strikes:
            for ot, it in strikes_map[stk].items():
                tok = it["token"]
                tokens_to_fetch.append(tok)
                token_info_map[tok] = (stk, ot, it)

        # Batch fetch quotes
        quotes_res: dict[str, dict] = {}
        for batch in [tokens_to_fetch[i:i + 50] for i in range(0, len(tokens_to_fetch), 50)]:
            try:
                r = await self._http.post(
                    f"{BASE}/rest/secure/angelbroking/market/v1/quote",
                    json={"mode": "FULL", "exchangeTokens": {"NFO": batch}},
                    headers=self._headers(jwt),
                    timeout=8.0,
                )
                for it in r.json().get("data", {}).get("fetched", []):
                    quotes_res[str(it.get("symbolToken"))] = it
            except Exception:
                pass

        now = datetime.now(IST)
        t_years = bs.years_to_expiry(expiry, now)
        lot_size = self.u.lot_size(und) if self.u else 65

        rows = []
        for stk in selected_strikes:
            row: dict = {"strike": stk, "ce": None, "pe": None}
            for ot in ("CE", "PE"):
                cand = strikes_map[stk].get(ot)
                if not cand:
                    continue
                q_data = quotes_res.get(cand["token"])
                if not q_data:
                    continue

                ltp = float(q_data.get("ltp") or 0.0)
                depth = q_data.get("depth", {})
                buys = depth.get("buy", [])
                sells = depth.get("sell", [])
                bid = float(buys[0]["price"]) if buys else None
                ask = float(sells[0]["price"]) if sells else None
                oi = float(q_data.get("opnInterest") or 0.0)
                vol = float(q_data.get("tradeVolume") or 0.0)

                iv = bs.implied_vol(ot, ltp, und_ltp, stk, t_years) if (und_ltp > 0 and ltp > 0) else None
                greeks = bs.greeks(ot, und_ltp, stk, t_years, iv or 0.15) if (und_ltp > 0 and t_years > 0) else {}

                key = "ce" if ot == "CE" else "pe"
                canon_inst = self.u.contract(und, expiry, stk, ot) if self.u else None
                inst_id = canon_inst.id if canon_inst else cand["symbol"]
                row[key] = {
                    "id": inst_id,
                    "ltp": ltp,
                    "bid": bid,
                    "ask": ask,
                    "oi": oi,
                    "volume": vol,
                    "iv": round(iv * 100, 2) if iv else None,
                    **greeks,
                }
            rows.append(row)

        summary = summarize_chain(rows, und_ltp)
        return {
            "source": "angel",
            "underlying": und,
            "expiry": expiry.isoformat(),
            "underlying_price": und_ltp,
            "lot_size": lot_size,
            "summary": summary,
            "rows": rows,
        }

    # ---- candles ---------------------------------------------------------------
    async def candles(self, inst: Instrument, tf: str, limit: int = 300) -> list[Candle]:
        if not self.configured:
            raise AuthExpired("Angel One not configured")

        token_res = self.token_for(inst)
        if not token_res:
            raise ProviderError(f"No Angel token for {inst.id}")

        jwt = await self._ensure_token()
        exch, token = token_res
        interval = INTERVAL_MAP.get(tf, "FIFTEEN_MINUTE")
        days = SPAN_DAYS.get(tf, 35)

        end_dt = datetime.now(IST)
        start_dt = end_dt - timedelta(days=days)

        payload = {
            "exchange": exch,
            "symboltoken": token,
            "interval": interval,
            "fromdate": start_dt.strftime("%Y-%m-%d %H:%M"),
            "todate": end_dt.strftime("%Y-%m-%d %H:%M"),
        }

        r = await self._http.post(
            f"{BASE}/rest/secure/angelbroking/historical/v1/getCandleData",
            json=payload,
            headers=self._headers(jwt),
            timeout=10.0,
        )

        if r.status_code != 200:
            raise ProviderError(f"Angel candles failed: HTTP {r.status_code}")

        data = r.json().get("data", [])
        if not data:
            return []

        out: list[Candle] = []
        for row in data:
            # row format: [datetime_str, open, high, low, close, volume]
            dt_str, o, h, l, c, v = row[:6]
            try:
                dt = datetime.fromisoformat(dt_str)
                ts = int(dt.timestamp())
                out.append([ts, float(o), float(h), float(l), float(c), float(v)])
            except Exception:
                continue

        return out[-limit:]

    def status(self) -> dict:
        return {
            "name": "angel",
            "configured": self.configured,
            "logged_in": bool(self._jwt_token),
            "last_ok": self.last_ok,
            "last_error": self.last_error,
        }
