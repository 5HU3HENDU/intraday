#!/usr/bin/env python3
"""Daily news picker for the overnight-news intraday paper test.

Scoring is HYBRID + SUBJECTIVE (Shiva, 2026-11-24): this script only
collects overnight news, matches it to Nifty 500 stocks, applies the
exclusion list, groups headlines into stories, and writes a SHORTLIST.
It does NOT score direction. The morning agent reads the shortlist and
scores each story subjectively (-2..+2 with a one-line reason).

News window: 16:00 IST on the LAST TRADING DAY -> 08:30 IST on the
next trading day (spans weekends/holidays; e.g. Wed 25 Nov window is
Mon 23 Nov 16:00 -> Wed 25 Nov 08:30).

Sources: Google News RSS (primary; chunked name queries + chunked
ticker queries), ET Markets RSS (thin supplement). BSE announcements
are 403-blocked from this VM - do not hammer them. Moneycontrol is
unusable as a primary source (no timestamps). NEVER touch nseindia.in
(Shiva: don't ping NSE).

Usage:
  python3 pick.py                        # today's run (trading day check applies)
  python3 pick.py --date 2026-11-25     # simulate a run date
  python3 pick.py --start "2026-11-23 16:00" --end "2026-11-24 08:30"
                                         # explicit window (testing; skips trading-day check)
  python3 pick.py --out shortlist-test.json

Output: shortlist-<trading-date>.json (in this folder, muse/)
"""
import argparse
import datetime
import email.utils
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
OUT_DIR = os.path.dirname(os.path.abspath(__file__))  # this folder (muse/)

# NSE equity trading holidays 2026 (official NSE circular; weekday closures).
# Weekend holidays need no entry (weekend check covers them). Muhurat trading
# on Sun 8 Nov 2026 is a special session, not a full trading day - ignore.
NSE_HOLIDAYS_2026 = {
    datetime.date(2026, 1, 26),   # Republic Day
    datetime.date(2026, 3, 3),    # Holi
    datetime.date(2026, 3, 26),   # Shri Ram Navami
    datetime.date(2026, 3, 31),   # Shri Mahavir Jayanti
    datetime.date(2026, 4, 3),    # Good Friday
    datetime.date(2026, 4, 14),   # Dr. Baba Saheb Ambedkar Jayanti
    datetime.date(2026, 5, 1),    # Maharashtra Day
    datetime.date(2026, 5, 28),   # Bakri Id
    datetime.date(2026, 6, 26),   # Muharram
    datetime.date(2026, 9, 14),   # Ganesh Chaturthi
    datetime.date(2026, 10, 2),   # Mahatma Gandhi Jayanti
    datetime.date(2026, 10, 20),  # Dussehra
    datetime.date(2026, 11, 10),  # Diwali-Balipratipada
    datetime.date(2026, 11, 24),  # Prakash Gurpurb Sri Guru Nanak Dev
    datetime.date(2026, 12, 25),  # Christmas
}
# TODO: extend with the 2027 list when NSE publishes it (paper test runs
# into Dec 2026 only, so 2026 covers it; a run in Jan 2027 would need it).

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"}


def is_trading_day(d, holidays=NSE_HOLIDAYS_2026):
    return d.weekday() < 5 and d not in holidays


def last_trading_day(today, holidays=NSE_HOLIDAYS_2026):
    d = today - datetime.timedelta(days=1)
    while not is_trading_day(d, holidays):
        d -= datetime.timedelta(days=1)
    return d


def fetch(url, timeout=25):
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except Exception:
        return None


def yahoo_last_bar_date():
    """Latest daily-bar date for ^NSEI (Yahoo chart API). Returns a date or None.
    Used as a reality check on the computed last trading day."""
    raw = fetch("https://query2.finance.yahoo.com/v8/finance/chart/%5ENSEI?range=10d&interval=1d")
    if raw is None:
        return None
    try:
        d = json.loads(raw)
        ts = d["chart"]["result"][0]["timestamp"]
        if not ts:
            return None
        return datetime.datetime.fromtimestamp(ts[-1], IST).date()
    except Exception:
        return None


def short_name(name):
    n = name
    # NOTE: do NOT strip a trailing " India" — it is part of the name for
    # "Bank of India", "Oil India", "Coal India" (stripping it gave the
    # variants "Bank of" / "Oil" / "Coal", which matched every bank/oil/coal
    # headline). Only the parenthesised "(India)" form is stripped.
    for suf in (" Ltd.", " Ltd", " Limited", " (India)"):
        if n.endswith(suf):
            n = n[: -len(suf)]
    return n.strip()


# Well-known headline aliases (brand names differ from legal names).
ALIASES = {
    "ETERNAL": ["Zomato"],
    "JPL": ["Jio"],
    "360ONE": ["360 ONE"],
    "TATACONSUM": ["Tata Consumer"],
    "TATACOMM": ["Tata Communications"],
    "TATASTEEL": ["Tata Steel"],
    "TATAMOTORS": ["Tata Motors"],
    "TATAPOWER": ["Tata Power"],
    "TATACHEM": ["Tata Chemicals"],
    "HINDUNILVR": ["HUL", "Hindustan Unilever"],
    "BAJFINANCE": ["Bajaj Finance"],
    "BAJAJFINSV": ["Bajaj Finserv"],
    "BAJAJ-AUTO": ["Bajaj Auto"],
    "M&M": ["Mahindra & Mahindra"],
    "LT": ["L&T", "Larsen & Toubro"],
    "LTI": ["LTIMindtree"],
    "SBIN": ["SBI", "State Bank of India"],
    "ICICIBANK": ["ICICI Bank"],
    "HDFCBANK": ["HDFC Bank"],
    "KOTAKBANK": ["Kotak Mahindra Bank"],
    "AXISBANK": ["Axis Bank"],
    "BHARTIARTL": ["Bharti Airtel", "Airtel"],
    "RELIANCE": ["Reliance Industries", "Reliance"],
    "INFY": ["Infosys"],
    "TCS": ["Tata Consultancy Services"],
    "HCLTECH": ["HCLTech", "HCL Technologies"],
    "WIPRO": ["Wipro"],
    "TECHM": ["Tech Mahindra"],
    "MARUTI": ["Maruti Suzuki"],
    "HEROMOTOCO": ["Hero MotoCorp"],
    "BAJAJHLDNG": ["Bajaj Holdings"],
    "GROWW": ["Groww"],
    "POLICYBZR": ["Policybazaar"],
    "PAYTM": ["Paytm", "One97"],
    "NYKAA": ["Nykaa"],
    "DMART": ["DMart", "Avenue Supermarts"],
    "ADANIENT": ["Adani Enterprises"],
    "ADANIPORTS": ["Adani Ports"],
    "ADANIGREEN": ["Adani Green"],
    "ADANIPOWER": ["Adani Power"],
    "HAL": ["Hindustan Aeronautics"],
    "BEL": ["Bharat Electronics"],
    "COALINDIA": ["Coal India"],
    "ONGC": ["ONGC"],
    "IOC": ["Indian Oil"],
    "BPCL": ["Bharat Petroleum"],
    "HINDPETRO": ["Hindustan Petroleum", "HPCL"],
    "NTPC": ["NTPC"],
    "POWERGRID": ["Power Grid"],
    "IRCTC": ["IRCTC"],
    "IRFC": ["Indian Railway Finance"],
    "RVNL": ["Rail Vikas Nigam"],
    "SUZLON": ["Suzlon"],
    "ZOMATO": ["Zomato"],
}

# ---------------------------------------------------------------------------
# Exclusion list — false positives found in the 24 Nov 2026 dry run.
# Each entry: symbol -> list of rules. A matched row is DROPPED if any
# drop_if pattern matches the headline AND no keep_if pattern matches.
# Patterns are case-insensitive regexes on the headline.
# Add new entries here when the morning run reports a false positive.
# ---------------------------------------------------------------------------
EXCLUSIONS = {
    "ACC": {
        # ACC the cement company vs ACC the sports team / tournament.
        "drop_if": [r"\bcricket\b", r"\bbasketball\b", r"\bfootball\b", r"\bvs\.?\b.*\b(match|tournament|league|cup)\b",
                    r"\btournament\b", r"\bwicket", r"\bbatsman\b", r"\bbowler\b", r"\binnings\b",
                    r"invitational", r"showdown", r"\bmaui\b", r"ole miss", r"\bsec\b",
                    r"\basean\b", r"cosmetic", r"committee", r"of the week", r"volleyball",
                    r"\bspark tank\b", r"warfighter", r"receiver of", r"player of",
                    r"nagad", r"anti-corruption", r"travel ban", r"asia cup",
                    r"points table", r"rising stars", r"athletics", r"fundraising",
                    r"boosters"],
        "keep_if": [r"\bACC\s+(Ltd|Limited)\b", r"\bprofit\b", r"\brevenue\b", r"\bresults\b", r"\bcement\b"],
    },
    "ABB": {
        # ABB Ltd (Swiss parent) vs ABB India Ltd. Parent stories mention
        # Ziehl-Abegg / Switzerland / Zurich; India stories say ABB India.
        "drop_if": [r"ziehl", r"\bswiss\b", r"\bzurich\b", r"\bswitzerland\b",
                    r"data cent(er|re) cooling"],
        "keep_if": [r"\bABB India\b"],
    },
    "LINDEINDIA": {
        # Linde India vs racing driver van der Linde / Linde plc, and vs
        # Linde Material Handling (forklifts / warehouse systems, KION Group).
        "drop_if": [r"van der linde", r"\bracing\b", r"\bdriver\b", r"formula\s*1", r"\bF1\b",
                    r"grand prix", r"motorsport", r"\bwarehouse\b", r"forklift",
                    r"material handling"],
        "keep_if": [r"\bLinde India\b"],
    },
    "LUPIN": {
        # Lupin Ltd (pharma) vs the Netflix show "Lupin".
        "drop_if": [r"netflix", r"\bseries\b", r"\bseason\b", r"streaming", r"web series", r"\bshow\b"],
        "keep_if": [r"\bLupin\s+(Ltd|Limited|Pharmaceuticals)\b", r"\bFDA\b", r"\bdrug\b", r"\bpharma"],
    },
    "ASTRAL": {
        # Astral Ltd (pipes) vs Asus Astral graphics cards, and vs
        # Astral Foods (South African poultry company).
        "drop_if": [r"\bgpu\b", r"graphics card", r"\basus\b", r"geforce", r"\brtx\b", r"graphics",
                    r"\bfoods\b", r"south africa", r"chicken", r"poultry", r"\bfeed\b"],
        "keep_if": [r"\bAstral\s+(Ltd|Limited|Pipes)\b", r"\bpipes\b"],
    },
    "CUPID": {
        # Cupid Ltd vs the film / thriller "Cupid".
        "drop_if": [r"\bfilm\b", r"\bmovie\b", r"\bthriller\b", r"\bactor\b", r"\bactress\b",
                    r"\bjoins\b", r"\bcast\b", r"\btrailer\b", r"\bnetflix\b"],
        "keep_if": [r"\bprofit\b", r"\bresults\b", r"\brevenue\b", r"\border\b", r"\bshares?\b"],
    },
    "GABRIEL": {
        # Gabriel India Ltd (auto parts) vs people named Gabriel (sport/film).
        "drop_if": [r"\bfantasy\b", r"\bfootball\b", r"\bnfl\b", r"\bquarterback\b",
                    r"\bactor\b", r"\bfilm\b", r"\bmovie\b", r"comedian", r"offstage",
                    r"\bshow\b", r"stand-?up"],
        "keep_if": [r"\bprofit\b", r"\bresults\b", r"\brevenue\b", r"\border\b", r"\bshares?\b",
                    r"\bbrake", r"\bauto\b"],
    },
    "NCC": {
        # NCC Ltd (construction) vs NCC the consumer commission / cadet corps /
        # foreign bodies sharing the acronym.
        "drop_if": [r"restaurant", r"inspection", r"campaign", r"sauna", r"\bcadet\b",
                    r"\belection\b", r"\bconsumer commission\b", r"abhiyaan",
                    r"mukt bharat", r"mounted rally", r"\bncc data\b", r"rideau",
                    r"skateway", r"national capital commission", r"students",
                    r"staging", r"naperville"],
        "keep_if": [r"\border\b", r"\bwins?\b", r"\bcontract\b", r"\bresults\b",
                    r"\bprofit\b", r"\brevenue\b", r"\bshares?\b", r"construction"],
    },
    "BANKINDIA": {
        # Bank of India Ltd vs the Reserve Bank of India ("Bank of India"
        # is a substring of "Reserve Bank of India").
        "drop_if": [r"reserve bank of india"],
        "keep_if": [r"\bBank of India\s+(Ltd|Limited)\b"],
    },
    "BSE": {
        # BSE Ltd (the listed exchange company) vs BSE the index ("BSE
        # Sensex"), BSE Odisha (board of secondary education), and
        # "(BSE:xxxxx)" exchange codes on other companies' pages.
        "drop_if": [r"sensex", r"odisha", r"osstet", r"board of secondary",
                    r"mutual funds buying", r"\(BSE:\d+\)", r"approval to raise"],
        "keep_if": [r"\bBSE\s+(Ltd|Limited)\b", r"\bBSE\s+(shares?|stock)\b",
                    r"\bBSE\s+Q[1-4]\b"],
    },
    "SIEMENS": {
        # Siemens Ltd (India) vs Siemens AG / Siemens Energy /
        # Siemens Healthineers / Siemens Gamesa (foreign group units).
        "drop_if": [r"energy", r"healthineers", r"gamesa", r"\bAG\b",
                    r"germany", r"\bUK\b", r"britain", r"first bus", r"first sign",
                    r"\bDAX\b", r"blood test"],
        "keep_if": [r"\bSiemens\s+(Ltd|Limited|India)\b"],
    },
    "TRENT": {
        # Trent Ltd (Tata retail) vs Trent Williams (NFL), the Rolls-Royce
        # Trent jet engine, Trent Bridge (cricket), Trent University/River.
        "drop_if": [r"trent\s+\d", r"trent williams", r"rolls-royce",
                    r"trent bridge", r"cricket", r"trent university",
                    r"trent river"],
        "keep_if": [r"\bTrent\s+(Ltd|Limited)\b", r"\bTrent\s+Q[1-4]\b",
                    r"\bTrent\s+(shares?|stock|profit|revenue|results)\b"],
    },
    "LTM": {
        # LTM Ltd vs "LTM" = last twelve months (a finance abbreviation).
        "drop_if": [r"\bLTM\s+(sales|revenue|ebitda)\b", r"last twelve months"],
        "keep_if": [r"\bLTM\s+(Ltd|Limited)\b", r"\bLTM\s+Q[1-4]\b",
                    r"\bLTM\s+(shares?|stock|profit|results)\b"],
    },
    "RECLTD": {
        # REC Ltd vs "REC" = renewable energy certificate ("I-REC",
        # "REC agreements").
        "drop_if": [r"\bI-REC\b", r"\bREC\s+agreements?\b",
                    r"renewable energy certificate"],
        "keep_if": [r"\bREC\s+(Ltd|Limited)\b", r"\bREC\s+Q[1-4]\b",
                    r"\bREC\s+(shares?|stock|profit|results)\b"],
    },
    "ITC": {
        # ITC Ltd vs the US International Trade Commission (ITC) and vs
        # "ITC" = input tax credit (GST).
        "drop_if": [r"international trade commission", r"trade ruling",
                    r"mutual funds buying", r"input tax credit", r"\bcbic\b",
                    r"itc loss", r"genuine buyers"],
        "keep_if": [r"\bITC\s+(Ltd|Limited)\b", r"\bITC\s+Q[1-4]\b",
                    r"\bITC\s+(shares?|stock|profit|revenue|results)\b"],
    },
    "BLUESTARCO": {
        # Blue Star Ltd (India, air conditioning) vs Blue Star Foods
        # (US seafood company).
        "drop_if": [r"blue star foods"],
        "keep_if": [r"\bBlue Star\s+(Ltd|Limited)\b"],
    },
    "BBOX": {
        # Black Box Ltd (IT) vs the FDA "black box warning" (drug label).
        "drop_if": [r"black box warning"],
        "keep_if": [r"\bBlack Box\s+(Ltd|Limited)\b"],
    },
    "ETERNAL": {
        # Eternal Ltd (ex-Zomato) vs "Eternal Sunshine" (Ariana Grande
        # album/film) and "Eternal Evil" (metal band).
        "drop_if": [r"eternal sunshine", r"eternal evil",
                    r"eternal (tour|album|song|band)"],
        "keep_if": [r"\bEternal\s+(Ltd|Limited)\b", r"\bEternal\s+Q[1-4]\b",
                    r"\bEternal\s+(shares?|stock|profit|revenue|results)\b",
                    r"zomato"],
    },
    "INFY": {
        # Infosys Ltd vs "ex-Infosys" people (a former employee's startup
        # is not Infosys news).
        "drop_if": [r"ex-infosys", r"former infosys"],
        "keep_if": [r"\bInfosys\s+(Ltd|Limited)\b", r"\bInfosys\s+Q[1-4]\b",
                    r"\bInfosys\s+(shares?|stock|profit|revenue|results)\b"],
    },
    "PHOENIXLTD": {
        # Phoenix Mills Ltd vs people who used to work there ("former
        # Phoenix Mills' exec" is not Phoenix Mills news).
        "drop_if": [r"former phoenix", r"ex-phoenix"],
        "keep_if": [r"\bPhoenix Mills\s+(Ltd|Limited)\b",
                    r"\bPhoenix Mills\s+Q[1-4]\b",
                    r"\bPhoenix Mills\s+(shares?|stock|profit|revenue|results)\b"],
    },
}

# Generic price-page guard: ET / aggregator auto-generated "share price today"
# pages carry no event. Drop them unless an event keyword is present.
PRICE_PAGE_RE = re.compile(r"share price", re.I)
PRICE_PAGE_LIVE_RE = re.compile(r"\b(live|today)\b", re.I)
# Auto-generated / roundup formats: no real event, drop outright (no
# event-keyword escape — "Rated Sell" contains "sell", "Call Contracts"
# contains no keyword but is pure options data).
AUTO_GEN_RE = re.compile(
    r"is rated (strong )?(buy|sell|hold)|rated (strong )?(buy|sell|hold)|"
    r"call contracts|put contracts|ahead of expiry|open interest|"
    r"mutual funds buying|share news today|what are mutual funds|"
    r"stocks bought by fund managers|earnings call (highlig|transcript)|"
    r"call transcript|latest news \d{4}",
    re.I)
EVENT_KEYWORDS = [
    "results", "profit", "revenue", "order", "deal", "bonus", "split",
    "dividend", "approval", "upgrade", "downgrade", "stake", "merger",
    "acquisition", "guidance", "fda", "q1", "q2", "q3", "q4", "board",
    "earnings", "contract", "wins", "bags", "secures", "invest",
    "buyback", "rights", "fpo", "ipo", "listing", "debt", "loan", "breach",
    "cyber", "fine", "fines", "fined", "penalty", "probe", "resign",
    "resignation", "appoint", "appoints", "reappoints", "ceo", "cfo",
    "acquire", "acquires", "buys", "buy", "sells", "sell", "purchase",
    "completes", "announce", "announces", "launch", "launches", "raise",
    "raises", "infusion", "investment", "invests", "funding", "allotment",
    "preferential", "qip", "crash", "crashes", "surge", "surges", "soar",
    "soars", "plunge", "plunges", "jump", "jumps", "rally", "rallies",
    "slump", "slumps", "tank", "tanks", "record", "offload", "offloads",
    "block deal", "bulk deal", "stake sale", "falls", "falling", "rises",
    "rising", "gains", "drops", "dips", "climbs", "sinks", "tumbles",
    "targets", "seeks", "clarification", "terminated", "termination",
    "replaces", "joins", "approve",
]

def has_event_keyword(text, keywords=None):
    """Word-boundary event check. Substring matching let 'win' fire inside
    'Wind'/'showing'/'Winner' and 'fine' inside 'Infineon'. Short keywords
    (fda, q1-q4, ipo, fpo, ceo, cfo, qip) still match as substrings;
    'USFDA' is normalised to 'fda' first. 'win' is deliberately NOT a
    keyword (only 'wins') — 'India win at Trent Bridge' is cricket."""
    if keywords is None:
        keywords = EVENT_KEYWORDS
    low = text.lower().replace("usfda", " fda ")
    for kw in keywords:
        if len(kw) <= 3:
            if kw in low:
                return True
        elif re.search(r"\b" + re.escape(kw) + r"\b", low):
            return True
    return False

# HARD events only — price-move words (falls, jumps, gains, crash...) are
# NOT enough on their own: a headline that only says the stock moved, with
# no cause, is price action, not news. The story-level filter uses this list;
# the match-time finance-context check uses the full EVENT_KEYWORDS.
_SOFT_WORDS = {"crash", "crashes", "surge", "surges", "soar", "soars",
               "plunge", "plunges", "jump", "jumps", "rally", "rallies",
               "slump", "slumps", "tank", "tanks", "record", "falls",
               "falling", "rises", "rising", "gains", "drops", "dips",
               "climbs", "sinks", "tumbles"}
HARD_EVENT_KEYWORDS = [k for k in EVENT_KEYWORDS if k not in _SOFT_WORDS]

def has_hard_event(text):
    return has_event_keyword(text, HARD_EVENT_KEYWORDS)

# Finance-context words required when matching 2-letter tickers (LT, NH),
# which otherwise collide with ordinary words.
FINANCE_WORDS = set(EVENT_KEYWORDS) | {
    "stock", "stocks", "share", "shares", "net", "crore", "lakh", "target",
    "rating", "outlook", "quarter", "growth", "margin", "sales",
}

MACRO_WORDS = ("nifty", "sensex", "stock market", "markets today", "market today",
               "share market", "indian markets", "market outlook", "market live",
               "rupee", "gold price", "silver price", "crude oil", "fii", "dii",
               "ipo", "market crash", "market rally", "nifty 50", "bank nifty")

STOPWORDS = set("""the a an of to in on for and with after from by at as its it is are
will has have shares share stock stocks ltd limited india indian rs cr
q1 q2 q3 q4 fy fy26 fy27 gets get""".split())

# Synonyms folded before token comparison, so differently-phrased headlines
# about the same event cluster together ("USFDA nod" = "FDA approval").
SYNONYMS = {"usfda": "fda", "nod": "approval", "wins": "win", "bags": "win",
            "secures": "win", "gets": "receives"}


def parse_rss(raw):
    items = []
    try:
        root = ET.fromstring(raw)
        for it in root.findall(".//item"):
            title = (it.findtext("title") or "").strip()
            link = (it.findtext("link") or "").strip()
            guid = (it.findtext("guid") or link or title).strip()
            pub = (it.findtext("pubDate") or "").strip()
            dt = None
            if pub:
                try:
                    dt = email.utils.parsedate_to_datetime(pub)
                    if dt is not None:
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=datetime.timezone.utc)
                        dt = dt.astimezone(IST)
                except Exception:
                    dt = None
            items.append({"title": title, "link": link, "guid": guid, "dt": dt, "pub": pub})
    except Exception as e:
        print("RSS parse error:", e, file=sys.stderr)
    return items


def norm_title(t):
    t = re.sub(r"\s+", " ", t.lower()).strip()
    t = re.sub(r"[^a-z0-9 ]", "", t)
    return t


def strip_outlet(title):
    """Split 'Headline - Outlet' / 'Headline | Outlet' -> (headline, outlet)."""
    for sep in (" | ", " - "):
        if sep in title:
            head, tail = title.rsplit(sep, 1)
            if len(head) >= 20 and len(tail) <= 60:
                return head.strip(), tail.strip()
    return title.strip(), ""


def stem_word(t):
    """Light stemming: strip a trailing 's' from words > 3 chars, so
    'raises'/'raise' and 'agreements'/'agreement' count as one token."""
    if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
        return t[:-1]
    return t


def content_tokens(title):
    head, _ = strip_outlet(title)
    toks = re.sub(r"[^a-z0-9 ]", " ", head.lower()).split()
    # Light stemming (see stem_word) so "raises"/"raise" count as one token
    # when grouping the same event across outlets.
    stemmed = []
    for t in toks:
        if t not in STOPWORDS and len(t) > 1:
            stemmed.append(stem_word(SYNONYMS.get(t, t)))
    return set(stemmed)


def jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="run date YYYY-MM-DD (default: today IST)")
    ap.add_argument("--start", help='window start "YYYY-MM-DD HH:MM" IST (testing)')
    ap.add_argument("--end", help='window end "YYYY-MM-DD HH:MM" IST (testing)')
    ap.add_argument("--out", help="output JSON path (default: shortlist-<trading-date>.json)")
    args = ap.parse_args()

    today = datetime.datetime.now(IST).date()
    if args.date:
        today = datetime.date.fromisoformat(args.date)

    # --- window ---
    if args.start and args.end:
        win_start = datetime.datetime.strptime(args.start, "%Y-%m-%d %H:%M").replace(tzinfo=IST)
        win_end = datetime.datetime.strptime(args.end, "%Y-%m-%d %H:%M").replace(tzinfo=IST)
        trading_date = win_end.date()
        is_trading = True  # explicit window: caller takes responsibility
        last_td = win_start.date()
        yahoo_note = "explicit window, no trading-day check"
    else:
        if not is_trading_day(today):
            print(f"{today} is not a trading day (weekend/holiday). Stay silent, no shortlist.")
            out = args.out or os.path.join(OUT_DIR, f"shortlist-{today.isoformat()}.json")
            os.makedirs(OUT_DIR, exist_ok=True)
            json.dump({"run_date": today.isoformat(), "is_trading_day": False,
                       "window": None, "stories": [], "stats": {}},
                      open(out, "w"), indent=2)
            print("Wrote:", out)
            return 0
        last_td = last_trading_day(today)
        win_start = datetime.datetime.combine(last_td, datetime.time(16, 0), tzinfo=IST)
        win_end = datetime.datetime.combine(today, datetime.time(8, 30), tzinfo=IST)
        trading_date = today
        is_trading = True
        # Yahoo sanity check on the last trading day.
        yb = yahoo_last_bar_date()
        if yb is None:
            yahoo_note = "Yahoo ^NSEI check failed (network) - using holiday-list date"
            print("WARN:", yahoo_note, file=sys.stderr)
        elif yb != last_td:
            yahoo_note = f"Yahoo latest bar {yb} != computed last trading day {last_td} - trusting Yahoo"
            print("WARN:", yahoo_note, file=sys.stderr)
            last_td = yb
            win_start = datetime.datetime.combine(last_td, datetime.time(16, 0), tzinfo=IST)
        else:
            yahoo_note = f"Yahoo ^NSEI latest bar {yb} matches computed last trading day"

    print(f"Window: {win_start} -> {win_end} (trading date {trading_date}, last trading day {last_td})")
    print("Yahoo check:", yahoo_note)

    # --- universe ---
    fl = json.load(open(os.path.join(OUT_DIR, "universe.json")))
    n500 = fl["indices"]["NIFTY500"]
    names = fl["names"]
    sym_to_names = {}
    for sym in n500:
        full = names.get(sym, sym)
        variants = {short_name(full), full}
        sym_to_names[sym] = variants
    for sym, al in ALIASES.items():
        if sym in sym_to_names:
            sym_to_names[sym].update(al)

    # --- feeds: name chunks only + ET ---
    # Ticker chunks were tried in testing and REMOVED: tickers like OIL, KEI,
    # MCX, CUPID are ordinary words, so ticker queries flood the fetch with
    # oil-price / connector / film junk. Ticker-only headlines are still
    # caught by ticker MATCHING on the name-query results (below), which is
    # what the dry run actually missed (e.g. "LTTS" in the Persistent story).
    #
    # Chunk size is 5 names (tested 24 Nov 2026): with 20 names per chunk,
    # Google News returns relevance-ranked results and overnight items get
    # crowded out (a 20-name chunk returned 77 items with only 1 Suzlon
    # mention, from Sep 2026); with 5 names the same names return 35 items
    # with 28 Suzlon, including all overnight items. 100 chunks is fine
    # (~30s with 8 workers).
    CHUNK_SIZE = 5
    all_names = [short_name(names.get(sym, sym)) for sym in n500]
    feeds = []
    for i in range(0, len(all_names), CHUNK_SIZE):
        q = " OR ".join(f'"{n}"' for n in all_names[i:i + CHUNK_SIZE])
        feeds.append(("google-news", "https://news.google.com/rss/search?q=" +
                     urllib.parse.quote(q) + "&hl=en-IN&gl=IN&ceid=IN:en"))
    feeds.append(("et-markets", "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"))
    print(f"Feeds to fetch: {len(feeds)} (name chunks + ET)")

    def fetch_one(feed):
        src, url = feed
        raw = fetch(url)
        if raw is None:
            raw = fetch(url)  # one retry for transient failures
        if raw is None:
            return src, None, url
        return src, parse_rss(raw), url

    per_source, raw_items = {}, []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for src, items, url in ex.map(fetch_one, feeds):
            per_source.setdefault(src, {"fetched": 0, "failed": 0})
            if items is None:
                per_source[src]["failed"] += 1
                print(f"FAILED: {src} {url[:100]}", file=sys.stderr)
            else:
                per_source[src]["fetched"] += len(items)
                for it in items:
                    it["source"] = src
                raw_items.extend(items)
    print("Raw items fetched:", len(raw_items), "| per source:", per_source)

    # --- filter to window ---
    in_window, no_date = [], 0
    for it in raw_items:
        if it["dt"] is None:
            no_date += 1
            continue
        if win_start <= it["dt"] <= win_end:
            in_window.append(it)
    print(f"In window: {len(in_window)} (no parseable date: {no_date})")

    # --- dedupe by normalized title ---
    seen = {}
    for it in in_window:
        k = norm_title(it["title"])
        if k and k not in seen:
            seen[k] = it
    deduped = list(seen.values())
    print(f"After title dedupe: {len(deduped)}")

    # --- match to N500 (names + tickers) ---
    # Returns {symbol: {"name": bool, "ticker": bool, "common_word": bool}}.
    # A match that comes ONLY from a bare ticker or from a common-word name
    # ("Cupid", "Gabriel") must also have a finance/event keyword in the
    # headline, otherwise it is junk (MCX connector, KEI-01 car, "Cupid" film).
    COMMON_WORD_VARIANTS = {"CUPID": {"cupid"}, "GABRIEL": {"gabriel"},
                            "ETERNAL": {"eternal"}, "TRENT": {"trent"}}

    def match_stocks(text):
        hits = {}
        low = text.lower()
        for sym, variants in sym_to_names.items():
            name_hit = False
            caps_only = True  # True only if every matched variant was all-caps
            common_word_hit = False
            for v in variants:
                if not v or len(v) < 3:
                    continue
                # "L&T" alias must not fire on L&T Technology Services (LTTS).
                if sym == "LT" and v == "L&T" and re.search(r"L&\s*T\s+Technology|LTTS", text, re.I):
                    continue
                if v.isupper() and len(v) <= 6:
                    # All-caps short name (CESC, BSE, HUL, SBI): match
                    # case-sensitively, like a ticker — "Cesc" the footballer
                    # must not match CESC, "BSE Odisha" must not match BSE Ltd.
                    if re.search(r"(?<![A-Za-z0-9])" + re.escape(v) + r"(?![A-Za-z0-9])", text):
                        name_hit = True
                    continue
                if re.search(r"\b" + re.escape(v) + r"\b", text, re.I):
                    name_hit = True
                    caps_only = False
                    if sym in COMMON_WORD_VARIANTS and v.lower() in COMMON_WORD_VARIANTS[sym]:
                        common_word_hit = True
                    break
            ticker_hit = False
            if not name_hit:
                if len(sym) >= 3:
                    if re.search(r"(?<![A-Za-z0-9])" + re.escape(sym) + r"(?![A-Za-z0-9])", text):
                        ticker_hit = True
                elif len(sym) == 2:
                    # 2-letter tickers need finance context + not followed by '&'
                    if sym == "LT":
                        if re.search(r"(?<![A-Za-z0-9])LT(?![A-Za-z0-9&])", text):
                            ticker_hit = True
                    else:
                        if re.search(r"(?<![A-Za-z0-9])" + re.escape(sym) + r"(?![A-Za-z0-9])", text):
                            if any(w in low for w in FINANCE_WORDS):
                                ticker_hit = True
            if name_hit or ticker_hit:
                hits[sym] = {"name": name_hit, "ticker": ticker_hit,
                             "common_word": common_word_hit,
                             "caps_only": caps_only and name_hit}
        return hits

    matched_rows, excluded, dropped_macro, unmatched = [], [], 0, []
    for it in deduped:
        text = it["title"]
        hits = match_stocks(text)
        if hits:
            low = text.lower()
            has_event = has_event_keyword(text)
            for sym, prov in hits.items():
                # ticker-only, caps-only, or common-word-only matches need finance context
                if (prov["ticker"] and not prov["name"]) or prov["common_word"] or prov["caps_only"]:
                    if not has_event:
                        excluded.append({"symbol": sym, "headline": text,
                                         "reason": "ticker/caps/common-word match, no finance context"})
                        continue
                # exclusion list
                rule = EXCLUSIONS.get(sym)
                if rule:
                    drop = any(re.search(p, text, re.I) for p in rule["drop_if"])
                    keep = any(re.search(p, text, re.I) for p in rule["keep_if"])
                    if drop and not keep:
                        excluded.append({"symbol": sym, "headline": text,
                                         "reason": f"exclusion list ({sym})"})
                        continue
                # generic price-page guard
                if PRICE_PAGE_RE.search(text) and PRICE_PAGE_LIVE_RE.search(text):
                    if not has_event:
                        excluded.append({"symbol": sym, "headline": text,
                                         "reason": "auto-generated price page, no event"})
                        continue
                # auto-generated / roundup formats: no real event
                if AUTO_GEN_RE.search(text):
                    excluded.append({"symbol": sym, "headline": text,
                                     "reason": "auto-generated/roundup, no event"})
                    continue
                matched_rows.append({
                    "symbol": sym, "company": names.get(sym, sym),
                    "headline": text, "source": it["source"], "link": it["link"],
                    "dt": it["dt"], "outlet": strip_outlet(text)[1],
                    "via_ticker": prov["ticker"] and not prov["name"],
                })
        else:
            low = text.lower()
            if any(w in low for w in MACRO_WORDS):
                dropped_macro += 1
            else:
                unmatched.append(it)
    print(f"Matched rows: {len(matched_rows)} | distinct stocks: {len(set(r['symbol'] for r in matched_rows))}")
    print(f"Excluded by rules: {len(excluded)} | dropped macro: {dropped_macro} | unmatched non-macro: {len(unmatched)}")

    # Company-name tokens per symbol (stemmed, same as content_tokens),
    # so the merge rule can tell "same event" from "same company, different
    # event": two headlines about one company always share the name tokens
    # ("adani energy solutions" = 3 tokens), so the stock-overlap arm also
    # requires at least one SHARED token outside both companies' names.
    sym_name_tokens = {}
    for sym, legal in names.items():
        toks = re.sub(r"[^a-z0-9 ]", " ", short_name(legal).lower()).split()
        sym_name_tokens[sym] = {stem_word(SYNONYMS.get(t, t)) for t in toks
                                if t not in STOPWORDS and len(t) > 1}

    # Greedy single-link clustering on content-token Jaccard, in time order.
    # Similarity is the MAX pairwise Jaccard against any row already in the
    # cluster (not vs the union of all cluster tokens, which dilutes as the
    # cluster grows). Merge rule (tested): same stock AND Jaccard >= 0.15 AND
    # at least one shared token outside both companies' names, OR Jaccard
    # >= 0.5 outright. A plain 0.25 threshold merged different companies'
    # results headlines (generic tokens — q2fy27, results, net profit, yoy,
    # crore — Jaccard ~0.35); a plain 0.40 split same-event paraphrases
    # (Adani Airport deal, ~0.20-0.30); stock-overlap alone merged different
    # events about one company (the name tokens alone push Jaccard to ~0.2).
    # The 0.5 arm still merges multi-company events (Tata/APL) via rows that
    # name both companies.
    clusters = []  # each: {"token_sets": [set, ...], "rows": [matched rows]}
    for row in sorted(matched_rows, key=lambda r: r["dt"]):
        toks = content_tokens(row["headline"])
        placed = False
        for cl in clusters:
            sim = 0.0
            shares_event_token = False
            subset = False
            for ts, crow in zip(cl["token_sets"], cl["rows"]):
                s = jaccard(toks, ts)
                if s > sim:
                    sim = s
                shared = toks & ts
                if shared - sym_name_tokens.get(row["symbol"], set()) - \
                        sym_name_tokens.get(crow["symbol"], set()):
                    shares_event_token = True
                if (len(toks) >= 3 and toks <= ts) or \
                        (len(ts) >= 3 and ts <= toks):
                    subset = True
            same_stock = row["symbol"] in {r["symbol"] for r in cl["rows"]}
            if (same_stock and sim >= 0.15 and shares_event_token) or \
                    sim >= 0.50 or subset:
                cl["rows"].append(row)
                cl["token_sets"].append(toks)
                placed = True
                break
        if not placed:
            clusters.append({"token_sets": [toks], "rows": [row]})

    # Second pass: merge clusters that are the same event. Greedy single-pass
    # clustering is order-dependent (a late headline may not match a cluster
    # whose early rows it resembles only weakly); comparing every pair of
    # clusters on max pairwise Jaccard fixes the stragglers.
    def clusters_similar(a, b):
        best = 0.0
        shares_event_token = False
        for ta, ra in zip(a["token_sets"], a["rows"]):
            for tb, rb in zip(b["token_sets"], b["rows"]):
                s = jaccard(ta, tb)
                if s > best:
                    best = s
                shared = ta & tb
                if shared - sym_name_tokens.get(ra["symbol"], set()) - \
                        sym_name_tokens.get(rb["symbol"], set()):
                    shares_event_token = True
                if (len(ta) >= 3 and ta <= tb) or (len(tb) >= 3 and tb <= ta):
                    return True
        same_stock = bool({r["symbol"] for r in a["rows"]} &
                          {r["symbol"] for r in b["rows"]})
        return (same_stock and best >= 0.15 and shares_event_token) or best >= 0.50

    merged = True
    while merged:
        merged = False
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                if clusters_similar(clusters[i], clusters[j]):
                    clusters[i]["rows"].extend(clusters[j]["rows"])
                    clusters[i]["token_sets"].extend(clusters[j]["token_sets"])
                    del clusters[j]
                    merged = True
                    break
            if merged:
                break

    # Story-level hard-event filter (the "rules" half of hybrid scoring):
    # keep a story only if at least 10% of its headlines carry a HARD event
    # keyword. Price-move words alone (falls, jumps, gains...) are not
    # enough — a headline that only says the stock moved, with no cause, is
    # price action, not news. The proportion (not "any row", not "rep only"):
    # "any row" let a 31-row bank-holiday cluster through on one stray
    # keyword; "rep only" dropped the 52-row Tata/APL cluster because its
    # longest title was a transcript. Real events score ~0.10-0.80 (the rest
    # of the headlines are price-action follow-ons); non-events score ~0-0.03.
    # The representative headline is the longest title that carries a hard
    # event keyword and is not an auto-generated aggregation page
    # ("Latest News 2026: ..."), so the agent sees a real headline.
    kept, dropped_soft = [], 0
    dropped_big = []
    for cl in clusters:
        rows = cl["rows"]

        def _rep_key(r):
            head, _ = strip_outlet(r["headline"])
            return (has_hard_event(head) and not AUTO_GEN_RE.search(head),
                    len(r["headline"]))

        rep = max(rows, key=_rep_key)
        cl["rep"] = rep
        frac = sum(1 for r in rows
                   if has_hard_event(strip_outlet(r["headline"])[0])) / len(rows)
        if frac >= 0.10:
            kept.append(cl)
        else:
            dropped_soft += 1
            if len(rows) >= 5:
                dropped_big.append((len(rows), strip_outlet(rep["headline"])[0][:80],
                                    round(frac, 2)))
    print(f"Stories after event filter: {len(kept)} (dropped {dropped_soft} soft stories)")
    for n, h, f in sorted(dropped_big, reverse=True)[:5]:
        print(f"  dropped big cluster n={n} hard-frac={f}: {h}")

    stories = []
    for i, cl in enumerate(kept, 1):
        rows = cl["rows"]
        stocks = sorted({r["symbol"] for r in rows})
        # representative headline: chosen in the filter step (longest title
        # with a hard event keyword, not an auto-generated aggregation page)
        rep = cl.get("rep") or max(rows, key=lambda r: len(r["headline"]))
        head, _ = strip_outlet(rep["headline"])
        stories.append({
            "story_id": f"S{i}",
            "stocks": [{"symbol": s, "company": names.get(s, s)} for s in stocks],
            "headline": head,
            "summary": "",  # morning agent writes one line when scoring
            "sources": sorted({r["outlet"] or r["source"] for r in rows}),
            "published_ist": min(r["dt"] for r in rows).strftime("%Y-%m-%d %H:%M IST"),
            "last_seen_ist": max(r["dt"] for r in rows).strftime("%Y-%m-%d %H:%M IST"),
            "item_count": len(rows),
            "links": list(dict.fromkeys(r["link"] for r in rows)),
        })
    stories.sort(key=lambda s: s["published_ist"])
    for i, s in enumerate(stories, 1):
        s["story_id"] = f"S{i}"
    print(f"Stories in shortlist: {len(stories)}")

    out = args.out or os.path.join(OUT_DIR, f"shortlist-{trading_date.isoformat()}.json")
    os.makedirs(OUT_DIR, exist_ok=True)
    report = {
        "run_date": trading_date.isoformat(),
        "is_trading_day": is_trading,
        "window": {"start_ist": win_start.strftime("%Y-%m-%d %H:%M IST"),
                   "end_ist": win_end.strftime("%Y-%m-%d %H:%M IST"),
                   "last_trading_day": last_td.isoformat()},
        "yahoo_check": yahoo_note,
        "universe": "NIFTY500",
        "universe_size": len(n500),
        "per_source": per_source,
        "stats": {
            "raw_items": len(raw_items),
            "in_window": len(in_window),
            "after_dedupe": len(deduped),
            "matched_rows": len(matched_rows),
            "distinct_stocks": len(set(r["symbol"] for r in matched_rows)),
            "excluded_rows": len(excluded),
            "dropped_macro": dropped_macro,
            "unmatched_non_macro": len(unmatched),
            "dropped_soft_stories": dropped_soft,
            "stories": len(stories),
        },
        "stories": stories,
        "excluded": excluded,
    }
    json.dump(report, open(out, "w"), indent=2, ensure_ascii=False)
    print("Saved:", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
