# Overnight-news intraday paper test — morning & evening procedure

**Strategy (Shiva, 2026-11-24):** Nifty 500 universe. News window = 16:00 on the
last trading day → 08:30 on the next trading day (spans weekends/holidays).
Sentiment analysis → pick 2–3 stocks with the clearest up/down signal → intraday.
Paper test only — no real money at stake.

**Scoring (Shiva, 2026-11-24): HYBRID + SENTIMENT-BASED + SUBJECTIVE.**
Hard rules filter the news to a shortlist (this script). Then the morning agent
reads each shortlisted story and scores its sentiment **by judgment**,
−2 to +2 with a one-line reason — **not by a formula**. Size and context count:
a ₹5,000 cr order beats a ₹50 cr one; a blockbuster-drug approval beats a
small-drug one; a breach is scored on how bad it really is.

## Files

- `pick.py` — the daily picker (this README's script). Writes
  `shortlist-<trading-date>.json`. Does NOT score direction.
- `shortlist-<date>.json` — one entry per story: stocks, headline, sources,
  published time, links, one-line summary (blank — the agent writes it).
- `paper-test-log.json` — one entry per pick per trading day; price fields
  filled in by the evening run.
- `dry-run-2026-11-24.json` — the first dry run (Mon 23 Nov 16:00 → Tue 24
  Nov 08:30). Superseded by `pick.py`.

## Morning run (cron ~08:30 IST, before 09:00)

1. Fetch NSE filings first (the freshest, precise times): open
   `https://www.nseindia.com/api/corporate-announcements?index=equities`
   via browser.open (fresh copy, max_age_minutes 30). Take the rows whose
   `symbol` is in the N500 universe and whose `an_dt` is inside the window
   (last trading day 16:00 → today 08:30). Save them as
   `filings-nse-<today>.json` — a JSON list of
   `{symbol, headline, dt, source, link}` where headline is
   `"<Company>: <desc> — <voiceSummary first sentence>"` (or attchmntText
   if there is no voiceSummary), dt is the `an_dt` in ISO form, source is
   `"nse-filings"`, link is the `attchmntFile` URL. If the fetch fails or
   yields nothing usable, skip it — Screener filings (step 2) still apply.
2. Run: `python3 pick.py --filings filings-nse-<today>.json` (from inside
   this `muse/` folder — shortlists land here too; omit `--filings` if
   step 1 produced no file)
   - The script computes the window itself (last trading day 16:00 → today
     08:30) and sanity-checks the last trading day against Yahoo `^NSEI`
     daily bars. NSE 2026 holidays are hardcoded in the script.
   - Filings in the shortlist come only from step 1 (NSE via `--filings`).
     Screener.in was tried as an in-process mirror and dropped on 25 Nov
     2026 — it republishes filings ~5 days late, so it found nothing in
     the overnight window.
   - **If today is not a trading day** (weekend/holiday) the script writes a
     shortlist with `is_trading_day: false` and exits — **stay silent, send
     nothing to Shiva.**
3. Read `shortlist-<today>.json`. If the stories list is empty, send Shiva one
   line ("no clear overnight signal today") and log nothing.
4. Score each story subjectively, −2 to +2, with a one-line reason:
   - **+2 / −2** — very strong, specific, likely to move the stock clearly
     (big order, results big beat/miss, major deal, FDA approval for a big
     drug, serious breach).
   - **+1 / −1** — clear direction but modest size or mixed detail.
   - **0** — neutral / routine / already priced in / unclear.
   - Judge size, context, and what is already priced in. Do not use a formula.
5. Pick the **top 2–3 by absolute score** (|score|), up or down. If the best
   story scores 0, pick nothing and send the one-line "no clear signal" note.
   Ties: prefer the story with the bigger/more specific news. **Do not pick
   the same stock twice** — if two shortlisted stories are the same event
   (same stocks, e.g. several Tata Steel/APL Apollo angles), score them once
   and count them as one pick.
6. Send Shiva the report in WhatsApp chat `0faa91ee-6edc-4317-85b1-6645c47a55a7`
   **before 09:00**, in simple words, chat text only, no file:
   - each pick: stock, predicted direction (up/down), score + reason, the news
     behind it (one line), and the Google Finance link **inline in a sentence**:
     `https://www.google.com/finance/quote/SYM:NSE`
   - one line on how many stories were in the shortlist.
7. Append the picks to `paper-test-log.json` under `days` (one entry per pick;
   see the `pick_fields` schema in that file). Leave the price fields blank
   for the evening run. If no picks, append nothing.
8. Push the morning's files to the intraday repo (`MUSE/` folder only, one
   batched commit — never push `instinct/` or the root `index.html`):
   `shortlist-<today>.json` and `paper-test-log.json` (plus `index.html`
   only if it changed). Paths file must live under `~/workspace`, not `/tmp`:
   `printf 'muse/shortlist-<today>.json\0muse/paper-test-log.json\0' >
   /home/hatch/workspace/intraday-push-paths.txt`, then
   `/opt/hatch/bin/github push-files --owner 5HU3HENDU --repo intraday
   --branch main --message "Paper test picks <today>" --source-dir
   /home/hatch/workspace/intraday --paths-file
   /home/hatch/workspace/intraday-push-paths.txt`.
   The page at `muse/index.html` reads `paper-test-log.json`, so the picks
   show there once pushed. If the push fails, the WhatsApp report already
   sent still stands — retry the push once, then leave it for the evening run.

## Evening run (cron ~16:00 IST, after close)

1. For each pick logged for today, fetch from the Yahoo chart API
   (`https://query2.finance.yahoo.com/v8/finance/chart/SYM.NS?range=1d&interval=1m`,
   browser User-Agent header — the v7 quote API is 401, the chart API works):
   - `open_0915` = open of the 09:15 bar
   - `price_0930` = close of the 09:30 bar (or the last trade price at ~09:30)
   - `close` = last bar's close
2. Fill in `ret_from_open_pct`, `ret_from_0930_pct`, and `correct` (true if the
   close moved in the predicted direction vs the open).
3. Save the log. Do **not** message Shiva in the evening unless the morning
   report failed to send.
4. Push the updated `paper-test-log.json` to the intraday repo (`MUSE/`
   folder only, one batched commit): `printf 'muse/paper-test-log.json\0' >
   /home/hatch/workspace/intraday-push-paths.txt`, then
   `/opt/hatch/bin/github push-files --owner 5HU3HENDU --repo intraday
   --branch main --message "Paper test results <today>" --source-dir
   /home/hatch/workspace/intraday --paths-file
   /home/hatch/workspace/intraday-push-paths.txt`. This is what fills in the
   results columns on the page.

## Judging the test (after 2–3 weeks)

- Hit rate: share of picks where `correct` is true.
- Average `ret_from_open_pct` in the predicted direction, vs the same from
  09:30 — which entry worked better?
- If the signal works, discuss going real (sizing, leverage, loss cap,
  kill switch — same rails as the parked trading bot). If not, stop.

## Known limits (do not re-discover)

- **Exchange filings are now a source (added 25 Nov 2026, Shiva: all 3
  routes).** NSE's corporate-announcements JSON is fetched fresh by the
  morning agent via browser.open and passed as `--filings` (latest ~20
  rows only, precise times + NSE's AI summary). A filing counts as an
  event for the story filter unless it is a routine notice (meet
  schedules, transcripts, newspaper publications). Screener.in was tried
  as the in-process mirror and DROPPED the same day — it republishes
  filings ~5 days late (latest per-company item was 18–20 Nov while NSE
  showed the same companies filing on 24 Nov), so it found 0 rows in
  the window. BSE is NOT wired in: its API is 403 from the VM and
  redirects to a form page in the browser; the web table loads (723 rows
  for the 23–25 Nov window) but needs 15 pages per morning. NSE already
  covers the same filings for N500 (dual-listed).
- **NSE: announcements ONLY.** Shiva lifted "don't ping nse" for the
  announcements feed on 25 Nov 2026. No other NSE endpoint is touched.
- **Moneycontrol is unusable as a primary source** (no publication timestamps).
- **Google News RSS is relevance-sorted and capped (~100/query).** Overnight
  items can get crowded out by morning news — the morning run must fire at
  08:30 sharp, and fetches have one retry each.
- **ET Markets RSS is a thin supplement** (rolling 50 items only).
- **Picker fixes (24 Nov 2026, tested on the Mon 23 Nov 16:00 → Tue 24 Nov
  08:30 window):** Google News chunks are 5 names each — 20-name chunks
  crowd overnight items out of the relevance-ranked results. Company short
  names keep a trailing " India" ("Bank of India", "Oil India", "Coal India"
  — stripping it matched every bank/oil/coal headline). All-caps short names
  (CESC, BSE) match case-sensitively and need a finance keyword — "Cesc" the
  footballer and "BSE Odisha" are not the stocks. Event keywords match on
  word boundaries — substring matching let "win" fire inside "Wind" /
  "showing" / "Winner" and "fine" inside "Infineon". The story filter needs
  a HARD event (order, deal, results, approval, FDA, bonus, buyback...) in
  at least 10% of the cluster's headlines — price-move words alone (falls,
  jumps, gains) are price action, not news. Stories are grouped by Jaccard
  similarity with stemming, merging when the same stock shares a non-company
  token (so different events about one company stay apart, and different
  companies' results headlines stay apart). The representative headline is
  the longest title with a hard event keyword, not an auto-generated
  aggregation page. **Verified result on that window: 58 stories**, all real
  N500 company events — the 11 dry-run stories are all present as single
  stories (Tata/APL, Lupin FDA, Persistent breach, TechM bonus, Suzlon
  cyber, Lenskart block deal, Adani Airport/360 ONE, PTCIL defence order),
  ticker-only headlines are caught (LTTS), and every known false positive
  from the dry run is gone (no Bank of Poland/Nova Scotia, no oil-price,
  no ACC sports, no BSE Sensex/Odisha, no Eternal Sunshine, no Trent
  Williams, no LTM Sales, no I-REC, no Reserve Bank of India). A busy night
  runs ~50-60 stories; a quiet night will be far fewer — the agent scores
  each headline and the soft ones (interviews, launches, awards,
  appointments) score 0 and are skipped fast.
- **False positives:** the exclusion list in `pick.py` (`EXCLUSIONS`) covers
  the dry-run cases (ACC = cricket, ABB Swiss parent vs ABB India, Linde
  racing driver, Netflix show "Lupin", Asus Astral GPU, auto-generated
  price pages). When the morning run reports a new false positive, add it
  there — do not silently drop stories.
- **Coverage is thin:** only 13/500 stocks had real news on the dry-run night
  (holiday eve). Most mornings will have few stories; that is expected.
- The shortlist is grouped by story (same event across outlets = one story).
  Scoring must be per story, or the biggest story wins by volume.
