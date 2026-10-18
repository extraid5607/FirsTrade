# Decisions and known gaps

Plain list of choices made while building, and what is a guess.

## Market model
- Hours 9:15 am to 3:30 pm IST, Monday to Friday, minus the holidays in `data/holidays.json`.
  **The 2026 holiday list came from a public website, not the NSE circular. Check it** against the NSE
  holiday list and edit the file if needed.
- Expiry day is **Tuesday** (weekly NIFTY and monthly last-Tuesday). If that day is a holiday the expiry
  moves to the previous trading day. This is the rule as understood in October 2026; confirm on nseindia.com.
- Indices cannot be traded directly (chart and underlying only), like a real market.
- Orders are only accepted while the market is open. Day limit orders lapse at the close.
- Cash settlement at expiry: futures at the underlying price, options at intrinsic value.
- Lot sizes: NIFTY 65, BANKNIFTY 30, FINNIFTY 60, MIDCPNIFTY 120 as believed current. **Stock F&O lot sizes in
  demo mode are placeholders.** In live mode the lot size comes from Groww's instrument list.

## Products and margin (approximate on purpose)
- CNC: full value, buy only. MIS: value / 5. NRML futures: 11% of value. Short options: 12% of the
  underlying's value. Long options: the premium. Real SPAN margins change daily and need exchange files,
  so these are simple stand-ins (`FUTURES_MARGIN_PCT`, `OPTION_SELL_MARGIN_PCT`, `MIS_LEVERAGE`).
- Risk-system exit: a leveraged position is closed automatically when it has lost 90% of its margin.
  CNC and long options have no such exit (nothing more can be lost than was paid).

## Charges
- STT rows follow the ClearTax April 2026 table. Brokerage, exchange, SEBI, stamp duty and GST rates are
  typical discount-broker numbers and are **unverified**. All are in `data/charges.json`; `/api/health`
  reports `charges.verified: false`.

## Prices and safety nets
- A price is "fresh" only while the market is open and the quote is under 30 seconds old.
- Guards against bad data: two sources disagreeing by more than a set percent are ignored; a sudden jump from
  one source waits for a second reading; a price more than 30% from the previous close is rejected; a source
  that fails twice is skipped for 45 seconds.
- Futures and options are only priced for things being looked at (open positions, orders, the chart, the chain)
  so the rate limits are respected (Groww limiter: at most 6 per second and 200 per minute).
- Demo prices are a random walk with Black-Scholes option prices (rate 6.5%, a volatility smile).

## Accounts
- Start balance 10,00,000 rupees. Seasons and resets as in ZeroPaper. Leaderboard needs 5 closed trades.
- Sign-up can be gated with `INVITE_CODE`. There is no email, so no password reset.

## Not built
- Brackets/OCO orders, partial fills, order modification, multi-leg strategies, SPAN margin, corporate actions,
  circuit limits, pre-open session, BSE, commodities, currency derivatives.

## Things most likely to need a fix on first live run
1. Column names in Groww's instrument CSV (`parse_groww_csv`).
2. The symbol format sent to the price endpoint for futures and options (`NSE_<trading_symbol>`).
3. Option-chain reply shape and whether IV is a percent or a fraction (`_iv` in `providers/groww.py`).
4. Candle interval strings (1minute, 5minute, 15minute, 1hour, 1day).
`scripts/check_live.py` tests each of these and says which one failed.
