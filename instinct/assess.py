#!/usr/bin/env python3
"""After-market assessment of a locked pre-open pick file. Research record only, hypothetical paper scenarios.
Usage: assess.py picks-YYYY-MM-DD.json [outdir]

Every rule used is read from the pick file; nothing is a built-in methodology. A pick may carry:
  side: long|short|none, entry: {type: "break"|"open", level}, target, stop, time_exit "HH:MM" (optional), cost_pct (optional)
Missing target/stop/time_exit are reported as "not set", never defaulted. Costs are shown only if cost_pct is given.
Bars: Yahoo Finance 5m (unofficial source, may differ from NSE). A bar is labelled by its START time.
Scoring is by bounds, not a guess:
 - Entry fill: break level is the best case; the bar's own extreme beyond the level on the adverse side is not
   knowable, so fill is "level" if the bar opened inside the level, "bar open" if it gapped through (stated).
 - A bar that contains the entry trigger AND the stop or target is AMBIGUOUS (order unknowable at 5m). Result is
   reported as lower/upper bound (stop-first vs target-first) with status "ambiguous", never as one outcome.
 - Same ambiguity for a later bar that touches both stop and target.
 - time_exit: exit at the OPEN of the first bar starting at/after that time.
 - Data checks: expected 75 five-minute bars (09:15-15:25 start); missing bars, an unfinished last bar, or missing
   prev close mark the result "data incomplete". Nothing is filled in.
"""
import json, sys, os, datetime as dt, requests
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
def bars(sym, day):
    p1 = int(dt.datetime.combine(day, dt.time(0, 0), IST).timestamp())
    r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}.NS", params={"interval": "5m", "period1": p1, "period2": p1 + 86400},
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    j = r.json()["chart"]["result"][0]; q = j["indicators"]["quote"][0]; out = []
    for i, t in enumerate(j["timestamp"]):
        if None in (q["open"][i], q["high"][i], q["low"][i], q["close"][i]): continue
        bt = dt.datetime.fromtimestamp(t, IST)
        if not (dt.time(9, 15) <= bt.time() <= dt.time(15, 25)): continue  # session bars only; drops e.g. the 15:30 zero-volume closing print
        out.append({"t": bt, "o": q["open"][i], "h": q["high"][i], "l": q["low"][i], "c": q["close"][i], "v": q["volume"][i] or 0})
    return out, j["meta"].get("chartPreviousClose")
def pct(a, b, s): return round(s * (a / b - 1) * 100, 2)
def one(p, day):
    b, prev = bars(p["symbol"], day); sym = p["symbol"]
    if not b: return {"symbol": sym, "status": "no data"}
    flags = []
    want = [dt.datetime.combine(day, dt.time(9, 15), IST) + dt.timedelta(minutes=5 * i) for i in range(75)]
    have = {x["t"] for x in b}; miss = [w.strftime("%H:%M") for w in want if w not in have]
    if miss: flags.append(f"{len(miss)} of 75 five-minute bars missing, first {miss[0]}")
    if b[-1]["t"].time() < dt.time(15, 25): flags.append("session not complete in data (last bar start " + b[-1]["t"].strftime("%H:%M") + ")")
    if prev is None: flags.append("previous close missing")
    res = {"symbol": sym, "side": p["side"], "prev_close": prev, "open": b[0]["o"], "gap_pct": pct(b[0]["o"], prev, 1) if prev else None, "day_high": max(x["h"] for x in b),
           "day_low": min(x["l"] for x in b), "last_price": b[-1]["c"], "volume": sum(x["v"] for x in b), "bars": len(b), "data_flags": flags}
    if p["side"] == "none": res["status"] = "no trade called"; return res
    L = p["side"] == "long"; s = 1 if L else -1; e = p["entry"]; lvl = e.get("level"); tgt = p.get("target"); stp = p.get("stop")
    hitT = lambda x: tgt is not None and ((x["h"] >= tgt) if L else (x["l"] <= tgt))
    hitS = lambda x: stp is not None and ((x["l"] <= stp) if L else (x["h"] >= stp))
    ei = None
    for k, x in enumerate(b):
        if e["type"] == "open": ei = k; ep = x["o"]; note = "filled at the bar open"; break
        if (L and x["h"] >= lvl) or (not L and x["l"] <= lvl):
            gapped = (x["o"] > lvl) if L else (x["o"] < lvl)
            ep = x["o"] if gapped else lvl; note = "gapped through the level, filled at bar open" if gapped else "filled at the trigger level"; ei = k; break
    if ei is None: res["status"] = "entry never triggered"; return res
    res.update({"entry_time_ist": b[ei]["t"].isoformat(), "entry_price": round(ep, 2), "entry_fill_note": note})
    te = p.get("time_exit"); texit = dt.datetime.combine(day, dt.time(*map(int, te.split(":"))), IST) if te else None
    mfe = mae = 0.0; out = None; amb = None
    for k in range(ei, len(b)):
        x = b[k]
        if texit and x["t"] >= texit and k > ei: out = (x["t"], x["o"], "time exit at bar open"); break
        fav = x["h"] if L else x["l"]; adv = x["l"] if L else x["h"]
        mfe = max(mfe, pct(fav, ep, s)); mae = min(mae, pct(adv, ep, s))
        sh, th = hitS(x), hitT(x)
        if k == ei and e["type"] == "break" and sh and not th:   # stop-side extreme may have come before the trigger
            amb = (x["t"], "entry bar touches the stop level; it may have printed before the trigger, order unknowable", "stop_only"); break
        if sh and th: amb = (x["t"], "bar touches both stop and target; order unknowable", "both"); break
        if sh: out = (x["t"], stp, "stop"); break
        if th: out = (x["t"], tgt, "target"); break
    res.update({"max_favourable_pct": mfe, "max_adverse_pct": mae})
    cost = p.get("cost_pct")
    def fin(price, why):
        g = pct(price, ep, s); d = {"exit_price": round(price, 2), "exit_reason": why, "gross_pct": g}
        if cost is not None: d["net_pct"] = round(g - cost, 2); d["cost_pct_used"] = cost
        return d
    if amb:
        lo = fin(stp, "stop first") if stp is not None else None
        hi = fin(tgt, "target first") if (tgt is not None and amb[2] == "both") else None
        res.update({"status": "ambiguous", "ambiguity": amb[1], "ambiguity_bar_ist": amb[0].isoformat(), "bounds": {"if_stop_first": lo, "if_target_first": hi, "otherwise": None if amb[2] == "both" else "stop may not have applied (printed before trigger); trade continues, not scored"}})
    elif out: res.update({"status": "scored" if not flags else "scored, data incomplete", "exit_time_ist": out[0].isoformat(), **fin(out[1], out[2])})
    else:
        res.update({"status": "no exit rule reached", "reference_mark_to_last_price": fin(b[-1]["c"], "last price, not an exit rule"),
                    "note": "no stop/target/time_exit was hit or set; mark is a reference only"})
    if flags and res["status"] == "scored": res["status"] = "scored, data incomplete"
    return res
def main():
    picks = json.load(open(sys.argv[1])); day = dt.date.fromisoformat(picks["session_date"])
    if picks.get("lock_status") != "locked_before_open":
        label = "RETROSPECTIVE, not a prospective call: lock_status is " + str(picks.get("lock_status"))
    else: label = "prospective (locked before the 09:15 open)"
    out = {"session_date": picks["session_date"], "label": label, "picks_locked_at_ist": picks.get("locked_at_ist"), "assessed_at_ist": dt.datetime.now(IST).isoformat(),
           "source": "Yahoo Finance 5m bars (unofficial; may differ from NSE)", "bar_definition": "labelled by start time; 75 bars expected 09:15-15:25",
           "results": [one(p, day) for p in picks["picks"]]}
    path = os.path.join(sys.argv[2] if len(sys.argv) > 2 else ".", f"assessment-{picks['session_date']}.json")
    json.dump(out, open(path, "w"), indent=1, ensure_ascii=False); print(path)
    for r in out["results"]: print(r["symbol"], r.get("status"), r.get("data_flags"), r.get("bounds") or r.get("gross_pct"))
main()
