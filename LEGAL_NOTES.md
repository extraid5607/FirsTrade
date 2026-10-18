# Legal notes (not legal advice)

I am an AI model, not a lawyer. This is a list of things worth knowing, written from what was publicly
reported up to the build date. Check each with a qualified person before you rely on it.

## Showing real-time prices to other people
- NSE market data is licensed. Showing real-time prices to anyone outside your own use can need a data
  licence and fees, and SEBI has rules on real-time data distribution. A free retail API token (such as
  Groww's) is normally for **your own** use, and its terms may not allow passing the data on.
- You decided to show live prices only to yourself and your students. The safest way to keep to that is:
  invite-code sign-up (`INVITE_CODE`), a small private group, no public link on social media, no
  screenshots of live quotes in public videos, and reading Groww's API terms to see if sharing with
  students is allowed. If in doubt, ask Groww support in writing.
- In live mode the app itself keeps real prices private: prices, charts and the option chain need a login, and the WebSocket refuses anonymous visitors (tested).
- Delayed or end-of-day data is treated differently from real-time data. Demo mode (simulated) has no such issue.

## Virtual / paper trading
- SEBI has warned (November 2024 advisory) about virtual trading and "paper trading" contests run by
  unregistered entities, especially where there are prizes or links to real trading.
- This app avoids the risky parts: no prizes, no money in or out, no link to a broker account, a visible
  disclaimer, and an invite code. **Do not add prizes, paid contests or "copy these trades" features.**
- It teaches; it does not recommend securities. Do not present any trade shown here as advice. You
  are not SEBI-registered, so keep to education and say so (the app's footer already does).

## Online money games law
- India's Promotion and Regulation of Online Gaming Act (2025) targets online money games. This app has
  no money at stake, which is the point of keeping it free of fees and prizes.

## Similar products
- Front Page (the app you compared this with) runs under its own legal set-up. Do not copy its branding.

## Your own checklist
1. Keep `INVITE_CODE` on in live mode.
2. Keep the disclaimer text.
3. Read the Groww Trade API terms about sharing data.
4. No prizes, no sponsorship tied to results.
5. Do not store anything beyond a username and password hash (that is all the app stores).
