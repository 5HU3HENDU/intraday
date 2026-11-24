#!/usr/bin/env python3
"""Log NSE corporate announcements for the instinct paper-trading universe.

Record only. No signals, no trades. Window: 16:00 IST to 08:30 IST next day.
Each record keeps the exchange time and our own capture time, never rewritten.
Gaps are written to data/runs.jsonl instead of being silently skipped.
"""
import json, os, re, sys, time, datetime as dt
import requests

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
STOCKS = os.path.join(HERE, "..", "stocks.md")
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
     "Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9",
     "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-announcements"}

def universe():
    syms = {}
    for line in open(STOCKS, encoding="utf-8"):
        m = re.match(r"\|\s*(.+?)\s*\|\s*([A-Z0-9&\-]+)\s*\|", line)
        if m and m.group(2) not in ("Symbol", "---"):
            syms[m.group(2)] = m.group(1)
    return syms

def session_date(t):
    """Window 16:00 -> 08:30 IST belongs to the session date of the 08:30 end. None if outside."""
    hm = t.hour * 60 + t.minute
    if hm >= 16 * 60:
        return (t.date() + dt.timedelta(days=1)).isoformat()
    if hm < 8 * 60 + 30:
        return t.date().isoformat()
    return None

def fetch(s, d1, d2):
    url = "https://www.nseindia.com/api/corporate-announcements"
    p = {"index": "equities", "from_date": d1.strftime("%d-%m-%Y"), "to_date": d2.strftime("%d-%m-%Y")}
    last = None
    for i in range(3):
        try:
            r = s.get(url, params=p, headers=H, timeout=40)
            if r.status_code == 200:
                return r.json(), None
            last = f"http {r.status_code}"
        except Exception as e:
            last = repr(e)[:200]
        time.sleep(3 * (i + 1))
    return None, last

def main():
    now = dt.datetime.now(IST)
    uni = universe()
    s = requests.Session()
    run = {"captured_at": now.isoformat(), "universe_size": len(uni), "source": "nse corporate-announcements api"}
    try:
        s.get("https://www.nseindia.com/", headers=H, timeout=40)
    except Exception as e:
        run["warmup_error"] = repr(e)[:200]
    rows, err = fetch(s, (now - dt.timedelta(days=2)).date(), now.date())
    os.makedirs(DATA, exist_ok=True)
    if rows is None:
        run["status"] = "FETCH_FAILED"; run["error"] = err
        open(os.path.join(DATA, "runs.jsonl"), "a").write(json.dumps(run) + "\n")
        print(run); return 0
    if isinstance(rows, dict):
        rows = rows.get("data", [])
    seen = set()
    for f in os.listdir(DATA):
        if f.startswith("disclosures-") and f.endswith(".jsonl"):
            for line in open(os.path.join(DATA, f)):
                try: seen.add(json.loads(line)["seq_id"])
                except Exception: pass
    new = 0; in_uni = 0; in_win = 0; bad_time = 0
    by_file = {}
    for r in rows:
        sym = r.get("symbol")
        if sym not in uni: continue
        in_uni += 1
        try:
            t = dt.datetime.strptime(r["an_dt"], "%d-%b-%Y %H:%M:%S").replace(tzinfo=IST)
        except Exception:
            bad_time += 1; continue
        sd = session_date(t)
        if sd is None: continue
        in_win += 1
        sid = str(r.get("seq_id"))
        if sid in seen: continue
        rec = {"seq_id": sid, "session_date": sd, "symbol": sym, "name": r.get("sm_name"), "isin": r.get("sm_isin"),
               "announced_at_ist": t.isoformat(), "disseminated_at": r.get("exchdisstime"),
               "category": r.get("desc"), "subject": r.get("attchmntText"), "pdf": r.get("attchmntFile"),
               "has_xbrl": r.get("hasXbrl"), "captured_at_ist": now.isoformat(), "source": "NSE",
               "late_capture": (now - t) > dt.timedelta(minutes=45)}
        by_file.setdefault(sd, []).append(rec); new += 1
    for sd, recs in by_file.items():
        with open(os.path.join(DATA, f"disclosures-{sd}.jsonl"), "a", encoding="utf-8") as f:
            for rec in recs: f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    run.update({"status": "OK", "rows_fetched": len(rows), "in_universe": in_uni, "in_window": in_win,
                "new_logged": new, "unparsed_time": bad_time,
                "note": "universe filter matches symbols in stocks.md; placeholder rows may not exist on NSE; coverage not proven complete"})
    open(os.path.join(DATA, "runs.jsonl"), "a").write(json.dumps(run) + "\n")
    print(run); return 0

if __name__ == "__main__":
    sys.exit(main())
