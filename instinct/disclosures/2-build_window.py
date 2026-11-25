#!/usr/bin/env python3
"""Build one next-trading-day disclosure window file. Record only.
Window: 16:00 IST on the previous trading day to 08:30 IST on session day T.
Usage: build_window.py [T=YYYY-MM-DD] [outdir]
"""
import json, re, sys, time, os, datetime as dt, requests
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
     "Accept": "application/json, text/plain, */*", "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-announcements"}
def get(s, url, params=None):
    last = None
    for i in range(3):
        try:
            r = s.get(url, params=params, headers=H, timeout=40)
            if r.status_code == 200: return r.json()
            last = r.status_code
        except Exception as e: last = repr(e)[:120]
        time.sleep(3 * (i + 1))
    raise RuntimeError(f"fetch failed {url}: {last}")
def universe(path):
    u = {}
    for line in open(path, encoding="utf-8"):
        m = re.match(r"\|\s*(.+?)\s*\|\s*([A-Z0-9&\-]+)\s*\|", line)
        if m and m.group(2) not in ("Symbol", "---"): u[m.group(2)] = m.group(1)
    return u
def main():
    s = requests.Session(); s.get("https://www.nseindia.com/", headers=H, timeout=40)
    hol = {dt.datetime.strptime(x["tradingDate"], "%d-%b-%Y").date() for x in get(s, "https://www.nseindia.com/api/holiday-master", {"type": "trading"})["CM"]}
    trading = lambda d: d.weekday() < 5 and d not in hol
    now = dt.datetime.now(IST)
    if len(sys.argv) > 1 and sys.argv[1] != "auto": T = dt.date.fromisoformat(sys.argv[1])
    else:
        T = now.date()
        if now.hour * 60 + now.minute < 8 * 60 + 30: T -= dt.timedelta(days=1)
        while not trading(T): T -= dt.timedelta(days=1)
    if not trading(T): print("T not a trading day"); return 2
    P = T - dt.timedelta(days=1)
    while not trading(P): P -= dt.timedelta(days=1)
    start = dt.datetime.combine(P, dt.time(16, 0), IST); end = dt.datetime.combine(T, dt.time(8, 30), IST)
    if now < end: print("window not closed yet"); return 3
    uni = universe(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "stocks.md") if os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "stocks.md")) else "/tmp/stocks.md")
    rows = []; days = []
    d = P
    while d <= T:   # one request per calendar day, so a day's rows are never cut by a cap
        part = get(s, "https://www.nseindia.com/api/corporate-announcements", {"index": "equities", "from_date": d.strftime("%d-%m-%Y"), "to_date": d.strftime("%d-%m-%Y")})
        if isinstance(part, dict): part = part.get("data", [])
        days.append({"date": d.isoformat(), "rows": len(part)}); rows += part
        d += dt.timedelta(days=1)
    seen = set(); out = []; bad = 0; uni_rows = 0
    for r in rows:
        sym = r.get("symbol")
        if sym not in uni: continue
        uni_rows += 1
        try: t = dt.datetime.strptime(r["an_dt"], "%d-%b-%Y %H:%M:%S").replace(tzinfo=IST)
        except Exception: bad += 1; continue
        if not (start <= t < end): continue
        sid = str(r.get("seq_id"))
        if sid in seen: continue
        seen.add(sid)
        out.append({"seq_id": sid, "symbol": sym, "name": r.get("sm_name"), "isin": r.get("sm_isin"), "announced_at_ist": t.isoformat(),
                    "disseminated_at": r.get("exchdisstime"), "category": r.get("desc"), "subject": r.get("attchmntText"),
                    "pdf": r.get("attchmntFile"), "has_xbrl": r.get("hasXbrl")})
    out.sort(key=lambda x: x["announced_at_ist"])
    meta = {"_meta": True, "session_date": T.isoformat(), "window_start_ist": start.isoformat(), "window_end_ist": end.isoformat(),
            "previous_trading_day": P.isoformat(), "built_at_ist": now.isoformat(), "source": "NSE corporate-announcements API (one request per calendar day)",
            "universe_symbols": len(uni), "rows_fetched_by_day": days, "rows_in_universe": uni_rows, "records": len(out), "unparsed_time_rows": bad,
            "caveats": ["NSE only; BSE not cross-checked", "built after the window closed, so capture time is not contemporaneous; times are exchange-stamped",
                        "per-day API row cap unverified; coverage not proven complete", "symbols not on NSE or placeholders never match", "calendar from NSE holiday master; special sessions not handled"]}
    od = sys.argv[2] if len(sys.argv) > 2 else "."
    path = os.path.join(od, f"disclosures-{T.isoformat()}.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(meta, ensure_ascii=False) + "\n")
        for r in out: f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(path, meta["records"], days, "P=", P, "T=", T)
    return 0
if __name__ == "__main__": sys.exit(main())
