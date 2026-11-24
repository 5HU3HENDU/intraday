# Disclosure log (record only)

Logs NSE corporate announcements for the symbols in `../stocks.md` (554 rows) for the 16:00 to 08:30 IST window.
A GitHub Action runs every 30 minutes from 15:30 to 09:00 IST and appends to `data/disclosures-<session date>.jsonl`.

- Each record keeps the exchange announcement time and our own capture time. `late_capture: true` means we saw it more than 45 minutes after it was filed (not a verified pre-open view).
- `data/runs.jsonl` records every run: rows fetched, rows in universe, rows in window, new rows, and failures. A failed or missing run is a gap, not "no news".
- No signals, no trades. Coverage is not proven complete: NSE only, symbols that are placeholders or not on NSE will never match, and the NSE API row cap is unverified.
- Holidays and weekends are not special-cased yet; the window is applied by clock time only.
