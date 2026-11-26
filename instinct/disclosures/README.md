# Disclosure log (record only)

NSE corporate announcements for the symbols in `../stocks.md` (554 rows), all categories.

**Window:** 16:00 IST on one trading day to 08:30 IST on the next trading day. Weekends and market holidays inside that span belong to the same window. One file per window: `disclosures-<session date>.jsonl` (next to this README) (session date = the next trading day).

**Build:** `python3 build_window.py auto <outdir>` after 08:30 IST. It reads the NSE holiday calendar, fetches one request per calendar day, keeps universe symbols whose announcement time is inside the window, and writes a `_meta` line followed by one record per filing (symbol, announced time, dissemination time, category, subject, PDF link).

**Commits:** one bulk commit per window. No automatic per-fetch commits, no Actions.

**Limits:** NSE only. Built after the window closes, so times are exchange-stamped, not our own contemporaneous capture; do not treat a record as "seen in real time". Symbols not on NSE or placeholders never match. Special sessions are not handled. Record only: no signals, no trades.
