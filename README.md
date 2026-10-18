# FirsTrade

A practice (paper) trading terminal for the Indian market. Pretend money, real-style rules.
Nifty, Bank Nifty and stocks, plus futures and options with a live option chain, a journal,
stats, risk rules, seasons and a leaderboard.

**For learning only. No real money, no prizes, no investment advice.** Read `LEGAL_NOTES.md` before you
show live prices to anyone.

## What it does

- **Trade**: instrument list with search, live chart (1m to 1D, India time), order ticket with a risk
  ladder, positions / orders / history under the chart.
- **Products** like a real broker: CNC (delivery, buy only), MIS (intraday, about 5x, auto square-off at
  3:20 pm), NRML (futures and options you carry overnight).
- **Futures and options** in lots (NIFTY 65, BANKNIFTY 30, FINNIFTY 60, MIDCPNIFTY 120), weekly and
  monthly expiries on Tuesdays, cash settlement at expiry, an **option chain** page (OI, IV, delta, PCR,
  max pain) where you click a price to trade it.
- **Real-style costs**: brokerage, STT, exchange fees, SEBI fee, stamp duty, GST on every order.
- **Risk tools**: size by "risk % of equity" with a stop-loss, R-multiples, daily loss limit, optional
  "stop-loss required" rule, automatic exit at about 90% loss of margin.
- **Journal, stats, leaderboard, seasons/reset**, invite-code sign-up, admin overview.

## Run it on your computer (demo mode, no keys needed)

```bash
python -m venv .venv && source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
PRICE_SOURCE=demo uvicorn app.main:app --port 8000          # Windows PowerShell: $env:PRICE_SOURCE="demo"
```

Open http://localhost:8000 and create an account. Demo mode uses **simulated prices** and treats the
market as always open so you can try everything at night.

Tests: `pip install -r requirements-dev.txt && python -m pytest -q tests` (97 tests, no internet needed).

## Going live (real prices)

1. On a computer with normal internet, run `python scripts/check_live.py` (see below). It prints PASS or
   FAIL per data source. **Do this before showing anything live.** The data-source code was written from
   the published documentation and has never talked to the real servers.
2. Set the environment variables (see `.env.example`):
   - `PRICE_SOURCE=live`
   - `GROWW_ACCESS_TOKEN=<today's token>` (or `GROWW_API_KEY` + `GROWW_API_SECRET`)
   - `INVITE_CODE=<a code only your students know>`
   - `ADMIN_USERNAME=<your username>`
3. The Groww token expires every day (about 6 am IST). Paste a fresh one with
   `POST /api/admin/groww-token` (logged in as the admin) or by changing the environment variable.
4. Live prices only update while the NSE is open (9:15 am to 3:30 pm IST, Mon to Fri, not on holidays).
   Outside those hours the app shows "Market closed" and the last close.

Data sources, in order: **Groww Trade API** (prices, candles, option chain, instrument list), **Yahoo**
(shares and indices only, fallback), **NSE website** (option chain fallback, unofficial, often blocked
from cloud hosts, backs off for 10 minutes when refused and never retries harder).

## Deploy on Render

`render.yaml` creates one web service (1 worker only, because prices and the engine live in memory) and a
free Postgres database. Start with `PRICE_SOURCE=demo`, check the site, then switch to live. Free Render
services sleep when idle; the first visit after a pause is slow.

## Scripts

- `scripts/check_live.py`: end-to-end PASS/FAIL for each live source.
- `scripts/capture_fixtures.py`: saves real sample replies to `tests/fixtures/real/` so the tests can use
  real shapes instead of the hand-written ones.

## What is and is not verified

Verified here: all trading logic, calendar and expiry maths, charges maths, margin and risk sizing,
accounting invariants (a stress test), the API, and the whole screen flow in a real browser at desktop
and phone width, **in demo mode**.

Not verified (the build computer could not reach Groww, NSE or Yahoo): every live data source, the Groww
instrument CSV column names, the symbol format used for futures/options prices, the option-chain reply
shape and IV unit, candle interval names, the 2026 holiday list, the charge rates other than STT, the
margin percentages (approximations), and the stock lot sizes (placeholders; index lots are the ones
believed current). See `DECISIONS.md` for the full list and `LEGAL_NOTES.md` for the rules.
