from flask import Flask, jsonify, request
import requests, time, math
from datetime import datetime
from zoneinfo import ZoneInfo

app = Flask(__name__)
IST = ZoneInfo("Asia/Kolkata")

VERSION = "3.9"
TV_URL = "https://scanner.tradingview.com/india/scan"

NSE_HOME = "https://www.nseindia.com/"
NSE_OC_PAGE = "https://www.nseindia.com/option-chain"
NSE_OC_CONTRACT = "https://www.nseindia.com/api/option-chain-contract-info"
NSE_OC_V3 = "https://www.nseindia.com/api/option-chain-v3"

TV_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/126 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.tradingview.com",
    "Referer": "https://www.tradingview.com/"
}

NSE_HEADERS = {
    "User-Agent": TV_HEADERS["User-Agent"],
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": NSE_OC_PAGE,
    "X-Requested-With": "XMLHttpRequest",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin"
}

_cache = {"signal": None, "ts": 0}
_trigger = {
    "direction": None,
    "level": None,
    "count": 0,
    "confirmed": False,
    "started_at": None
}
_locked_quote_cache = {}

def market_open_now():
    now = datetime.now(IST)
    if now.weekday() >= 5:
        return False
    m = now.hour * 60 + now.minute
    return 555 <= m <= 930

def tv_rating(v):
    if v >= .5: return "STRONG BUY"
    if v >= .1: return "BUY"
    if v <= -.5: return "STRONG SELL"
    if v <= -.1: return "SELL"
    return "NEUTRAL"

def fetch_tv():
    cols = [
        "close|5","open|5","Recommend.All|5","RSI|5","EMA9|5","EMA21|5","EMA50|5",
        "MACD.macd|5","MACD.signal|5","ADX|5","ATR|5","high|5","low|5","VWAP|5",
        "open|15","high|15","low|15","close|15","Recommend.All|15","RSI|15",
        "EMA9|15","EMA21|15","EMA50|15","MACD.macd|15","MACD.signal|15","ADX|15"
    ]
    payload = {
        "symbols":{"tickers":["NSE:NIFTY"],"query":{"types":[]}},
        "columns":cols,
        "range":[0,1]
    }
    r = requests.post(TV_URL, json=payload, headers=TV_HEADERS, timeout=12)
    r.raise_for_status()
    j = r.json()
    if not j.get("data"):
        raise RuntimeError("TradingView returned no NIFTY data.")
    vals = j["data"][0]["d"]
    if len(vals) != len(cols):
        raise RuntimeError("TradingView returned incomplete indicator data.")
    x = dict(zip(cols, vals))

    def f(k, d=0):
        try:
            return float(x[k]) if x[k] is not None else float(d)
        except Exception:
            return float(d)

    spot = f("close|5")
    return {
        "spot": spot,
        "open5": f("open|5", spot),
        "rec5": f("Recommend.All|5"),
        "rsi5": f("RSI|5", 50),
        "ema9_5": f("EMA9|5", spot),
        "ema21_5": f("EMA21|5", spot),
        "ema50_5": f("EMA50|5", spot),
        "macd5": f("MACD.macd|5"),
        "macds5": f("MACD.signal|5"),
        "adx5": f("ADX|5"),
        "atr5": max(f("ATR|5", 1), .01),
        "high5": f("high|5", spot),
        "low5": f("low|5", spot),
        "vwap5": f("VWAP|5", spot),

        "open15": f("open|15", spot),
        "high15": f("high|15", spot),
        "low15": f("low|15", spot),
        "close15": f("close|15", spot),
        "rec15": f("Recommend.All|15"),
        "rsi15": f("RSI|15", 50),
        "ema9_15": f("EMA9|15", spot),
        "ema21_15": f("EMA21|15", spot),
        "ema50_15": f("EMA50|15", spot),
        "macd15": f("MACD.macd|15"),
        "macds15": f("MACD.signal|15"),
        "adx15": f("ADX|15")
    }

def score(tv, bull=True):
    s = 0
    checks = []

    def add(label, ok, pts):
        nonlocal s
        ok = bool(ok)
        checks.append({"label": label, "ok": ok})
        if ok:
            s += pts

    add("5m rating confirms", tv["rec5"] >= .1 if bull else tv["rec5"] <= -.1, 12)
    add("15m rating confirms", tv["rec15"] >= .1 if bull else tv["rec15"] <= -.1, 16)
    add("EMA 9/21 5m trend", tv["ema9_5"] > tv["ema21_5"] if bull else tv["ema9_5"] < tv["ema21_5"], 12)
    add("EMA 9/21 15m trend", tv["ema9_15"] > tv["ema21_15"] if bull else tv["ema9_15"] < tv["ema21_15"], 14)
    add("Price vs EMA50 5m", tv["spot"] > tv["ema50_5"] if bull else tv["spot"] < tv["ema50_5"], 8)
    add("Price vs EMA50 15m", tv["spot"] > tv["ema50_15"] if bull else tv["spot"] < tv["ema50_15"], 8)
    add("Price vs VWAP", tv["spot"] > tv["vwap5"] if bull else tv["spot"] < tv["vwap5"], 8)
    add("RSI 5m healthy", (52 <= tv["rsi5"] <= 70) if bull else (30 <= tv["rsi5"] <= 48), 5)
    add("RSI 15m healthy", (50 <= tv["rsi15"] <= 68) if bull else (32 <= tv["rsi15"] <= 50), 5)
    add("MACD 5m confirms", tv["macd5"] > tv["macds5"] if bull else tv["macd5"] < tv["macds5"], 5)
    add("MACD 15m confirms", tv["macd15"] > tv["macds15"] if bull else tv["macd15"] < tv["macds15"], 4)
    add("ADX 5m >= 18", tv["adx5"] >= 18, 2)
    add("ADX 15m >= 18", tv["adx15"] >= 18, 1)
    return s, checks

def quality_gate(tv, bull, score_value):
    same_direction = (tv["rec5"] >= .1 and tv["rec15"] >= .1) if bull else (tv["rec5"] <= -.1 and tv["rec15"] <= -.1)
    ema_trend = (tv["ema9_5"] > tv["ema21_5"] and tv["ema9_15"] > tv["ema21_15"]) if bull else (tv["ema9_5"] < tv["ema21_5"] and tv["ema9_15"] < tv["ema21_15"])
    long_trend = (tv["spot"] > tv["ema50_5"] and tv["spot"] > tv["ema50_15"]) if bull else (tv["spot"] < tv["ema50_5"] and tv["spot"] < tv["ema50_15"])
    vwap_ok = tv["spot"] > tv["vwap5"] if bull else tv["spot"] < tv["vwap5"]
    macd_ok = (tv["macd5"] > tv["macds5"] and tv["macd15"] > tv["macds15"]) if bull else (tv["macd5"] < tv["macds5"] and tv["macd15"] < tv["macds15"])
    rsi_ok = ((50 <= tv["rsi5"] <= 70) and (50 <= tv["rsi15"] <= 68)) if bull else ((30 <= tv["rsi5"] <= 50) and (32 <= tv["rsi15"] <= 50))

    if tv["adx5"] < 15:
        regime, regime_ok = "CHOP / LOW TREND", False
    elif tv["adx5"] < 18 or tv["adx15"] < 18:
        regime, regime_ok = "DEVELOPING TREND", False
    else:
        regime, regime_ok = "TRENDING", True

    candle_ok = tv["spot"] > tv["open5"] if bull else tv["spot"] < tv["open5"]
    buf = max(tv["atr5"] * .10, 1.5)
    structure = max(tv["ema21_5"], tv["ema50_5"], tv["vwap5"]) if bull else min(tv["ema21_5"], tv["ema50_5"], tv["vwap5"])
    breakout_ok = tv["spot"] >= structure + buf if bull else tv["spot"] <= structure - buf

    hard = {
        "5m + 15m direction agree": same_direction,
        "EMA 9/21 aligned": ema_trend,
        "Price beyond EMA50s": long_trend,
        "Price vs VWAP confirms": vwap_ok,
        "MACD confirms 5m + 15m": macd_ok,
        "Trend regime strong enough": regime_ok,
        "RSI healthy": rsi_ok,
        "5m candle confirms": candle_ok,
        "Breakout clears structure + buffer": breakout_ok
    }

    passed = sum(1 for v in hard.values() if v)
    confirmed = all(hard.values()) and score_value >= 80
    prepare = same_direction and ema_trend and long_trend and vwap_ok and rsi_ok and score_value >= 65 and not confirmed

    if confirmed:
        state = "CONFIRMED"
        detail = "All new-entry conditions aligned. Waiting for frozen trigger confirmation and a tradable option quote."
    elif prepare:
        state = "PREPARE"
        detail = "Setup is developing. Execution remains blocked until the remaining confirmations pass."
    elif regime == "CHOP / LOW TREND":
        state = "AVOID"
        detail = "Low-trend/choppy regime. New entries are blocked."
    else:
        state = "WAIT"
        detail = "Conditions are mixed. Wait for stronger alignment."

    return confirmed, prepare, hard, passed, state, detail, regime

def pexp(x):
    for f in ("%d-%b-%Y", "%d-%b-%y"):
        try:
            return datetime.strptime(x, f).date()
        except Exception:
            pass
    return None

def nearest_expiry(xs):
    today = datetime.now(IST).date()
    parsed = [(pexp(x), x) for x in xs]
    parsed = [x for x in parsed if x[0]]
    future = [x for x in parsed if x[0] >= today]
    usable = future or parsed
    if not usable:
        raise RuntimeError("No usable NIFTY expiry.")
    return sorted(usable)[0][1]

def fetch_oc():
    s = requests.Session()
    s.headers.update(NSE_HEADERS)
    s.get(NSE_HOME, timeout=10)
    s.get(NSE_OC_PAGE, timeout=10)

    ci = s.get(NSE_OC_CONTRACT, params={"symbol": "NIFTY"}, timeout=12)
    ci.raise_for_status()
    info = ci.json()
    expiries = info.get("expiryDates", []) or info.get("records", {}).get("expiryDates", [])
    if not expiries:
        raise RuntimeError("NSE returned no NIFTY expiry dates.")

    ex = nearest_expiry(expiries)
    r = s.get(NSE_OC_V3, params={"type": "Indices", "symbol": "NIFTY", "expiry": ex}, timeout=12)

    if r.status_code in (401, 403):
        s = requests.Session()
        s.headers.update(NSE_HEADERS)
        s.get(NSE_HOME, timeout=10)
        s.get(NSE_OC_PAGE, timeout=10)
        r = s.get(NSE_OC_V3, params={"type": "Indices", "symbol": "NIFTY", "expiry": ex}, timeout=12)

    r.raise_for_status()
    j = r.json()
    if not j.get("records", {}).get("data"):
        raise RuntimeError("NSE option chain returned no contracts.")
    return j

def choose_option(oc, spot, bull, atr5, confidence):
    rec = oc["records"]
    ex = nearest_expiry(rec.get("expiryDates", []))
    typ = "CE" if bull else "PE"
    atm = round(spot / 50) * 50
    candidates = []

    def num(d, *keys):
        for k in keys:
            v = d.get(k)
            if v not in (None, "", "-"):
                try:
                    return float(str(v).replace(",", ""))
                except Exception:
                    pass
        return 0.0

    for row in rec.get("data", []):
        if row.get("expiryDate") and row.get("expiryDate") != ex:
            continue
        side = row.get(typ)
        if not side:
            continue

        st = float(row.get("strikePrice", 0) or 0)
        if abs(st - atm) > 300:
            continue

        ltp = num(side, "lastPrice", "ltp", "last_price")
        bid = num(side, "buyPrice1", "bidPrice", "bidprice", "bid", "bestBid")
        ask = num(side, "sellPrice1", "askPrice", "askprice", "ask", "bestAsk")
        vol = int(num(side, "totalTradedVolume", "volume"))
        oi = int(num(side, "openInterest", "oi"))

        if ltp <= 0 or bid <= 0 or ask <= 0 or ask < bid:
            continue

        mid = (ask + bid) / 2
        spread_pct = ((ask - bid) / mid * 100) if mid > 0 else 999
        if spread_pct > 12 or vol < 100 or oi <= 0:
            continue

        rank = (abs(st - atm) / 50) * 6 + spread_pct * 3 - min(math.log10(vol + 1), 6) * 4 - min(math.log10(oi + 1), 7) * 3
        candidates.append((rank, st, ltp, bid, ask, vol, oi, spread_pct))

    if not candidates:
        raise RuntimeError("No nearby NIFTY option passed liquidity checks.")

    _, st, ltp, bid, ask, vol, oi, spread_pct = sorted(candidates)[0]

    entry = ask
    spread = ask - bid
    base_pct = .14 if confidence >= 90 else .16 if confidence >= 80 else .18
    risk = min(max(entry * base_pct, spread * 3, entry * .10), entry * .22)
    rr1 = 1.50 if confidence >= 90 else 1.35
    rr2 = 2.40 if confidence >= 90 else 2.15

    return {
        "contract": f"NIFTY {int(st)} {typ}",
        "expiry": ex,
        "strike": st,
        "type": typ,
        "ltp": round(ltp, 2),
        "bid": round(bid, 2),
        "ask": round(ask, 2),
        "entry": round(entry, 2),
        "sl": round(max(entry - risk, .05), 2),
        "target1": round(entry + rr1 * risk, 2),
        "target2": round(entry + rr2 * risk, 2),
        "volume": vol,
        "oi": oi,
        "spread_pct": round(spread_pct, 2),
        "liquidity": "GOOD" if spread_pct <= 5 and vol >= 1000 else "FAIR",
        "tradable": True
    }

def confirm_trigger(direction, spot, atr, setup_active):
    global _trigger

    if direction not in ("BUY", "SELL") or not setup_active:
        _trigger = {"direction": None, "level": None, "count": 0, "confirmed": False, "started_at": None}
        return None, 0, False

    if _trigger["direction"] != direction or _trigger["level"] is None:
        buffer = max(float(atr) * .15, 5.0)
        level = spot + buffer if direction == "BUY" else spot - buffer
        _trigger = {
            "direction": direction,
            "level": round(level, 2),
            "count": 0,
            "confirmed": False,
            "started_at": datetime.now(IST).strftime("%H:%M:%S")
        }

    level = float(_trigger["level"])
    beyond = spot >= level if direction == "BUY" else spot <= level

    if not _trigger["confirmed"]:
        _trigger["count"] = _trigger["count"] + 1 if beyond else 0
        if _trigger["count"] >= 2:
            _trigger["confirmed"] = True

    return level, min(_trigger["count"], 2), bool(_trigger["confirmed"])

def exact_option_quote(strike, opt_type, expiry_date):
    global _locked_quote_cache
    strike = float(strike)
    opt_type = str(opt_type).upper()
    cache_key = f"{int(strike)}-{opt_type}-{expiry_date or ''}"

    def num(d, *keys):
        for k in keys:
            v = d.get(k)
            if v not in (None, "", "-"):
                try:
                    return float(str(v).replace(",", ""))
                except Exception:
                    pass
        return 0.0

    try:
        oc = fetch_oc()
        for row in oc["records"].get("data", []):
            try:
                row_strike = float(row.get("strikePrice", 0) or 0)
            except Exception:
                continue

            if row_strike != strike:
                continue

            side = row.get(opt_type)
            if not side:
                continue

            row_exp = row.get("expiryDate") or side.get("expiryDate") or ""
            if expiry_date and row_exp and row_exp != expiry_date:
                continue

            ltp = num(side, "lastPrice", "ltp", "last_price")
            bid = num(side, "buyPrice1", "bidPrice", "bidprice", "bid", "bestBid")
            ask = num(side, "sellPrice1", "askPrice", "askprice", "ask", "bestAsk")
            vol = int(num(side, "totalTradedVolume", "volume"))
            oi = int(num(side, "openInterest", "oi"))

            mid = (ask + bid) / 2 if ask > 0 and bid > 0 else 0
            sp = ((ask - bid) / mid * 100) if mid > 0 and ask >= bid else None

            q = {
                "contract": f"NIFTY {int(strike)} {opt_type}",
                "expiry": row_exp or expiry_date,
                "strike": strike,
                "type": opt_type,
                "ltp": round(ltp, 2),
                "bid": round(bid, 2),
                "ask": round(ask, 2),
                "volume": vol,
                "oi": oi,
                "spread_pct": round(sp, 2) if sp is not None else None,
                "source": "NSE live",
                "stale": False,
                "quote_time": datetime.now(IST).strftime("%H:%M:%S")
            }

            if q["ltp"] > 0:
                _locked_quote_cache[cache_key] = {"quote": q, "ts": time.time()}
                return q

        raise RuntimeError("Locked option contract not present in latest NSE response.")

    except Exception as e:
        cached = _locked_quote_cache.get(cache_key)
        if cached and time.time() - cached["ts"] <= 90:
            q = dict(cached["quote"])
            q["stale"] = True
            q["source"] = "Last good NSE quote"
            q["warning"] = str(e)
            return q
        raise RuntimeError("Locked option quote unavailable: " + str(e))

def build_signal():
    tv = fetch_tv()
    bull_score, bull_checks = score(tv, True)
    bear_score, bear_checks = score(tv, False)

    bull = bull_score >= bear_score
    conf = bull_score if bull else bear_score
    checks = bull_checks if bull else bear_checks
    diff = abs(bull_score - bear_score)

    confirmed_gate, prepare_gate, quality_checks, quality_passed, entry_state, entry_detail, regime = quality_gate(tv, bull, conf)

    if confirmed_gate and conf >= 80 and diff >= 20:
        raw_signal = "BUY" if bull else "SELL"
    elif prepare_gate and conf >= 65 and diff >= 15:
        raw_signal = "BUY WATCH" if bull else "SELL WATCH"
    else:
        raw_signal = "NO TRADE"

    direction = "BUY" if bull else "SELL"
    setup_active = raw_signal in ("BUY", "SELL", "BUY WATCH", "SELL WATCH")
    trigger_level, trigger_count, trigger_confirmed = confirm_trigger(direction, tv["spot"], tv["atr5"], setup_active)

    option = None
    warning = ""
    if setup_active:
        try:
            option = choose_option(fetch_oc(), tv["spot"], bull, tv["atr5"], conf)
        except Exception as e:
            warning = "Signal available, but option quote unavailable: " + str(e)

    execution_ready = bool(
        raw_signal in ("BUY", "SELL")
        and confirmed_gate
        and trigger_confirmed
        and option
        and option.get("tradable")
    )

    final_signal = direction if execution_ready else (
        ("BUY WATCH" if bull else "SELL WATCH") if setup_active else "NO TRADE"
    )

    buffer = max(tv["atr5"] * .15, 5.0)
    buy_above = round(trigger_level if bull and trigger_level is not None else tv["spot"] + buffer, 2)
    sell_below = round(trigger_level if (not bull) and trigger_level is not None else tv["spot"] - buffer, 2)

    return {
        "spot": round(tv["spot"], 2),
        "signal": final_signal,
        "direction": direction,
        "confidence": conf,
        "rating5": tv_rating(tv["rec5"]),
        "rating15": tv_rating(tv["rec15"]),
        "rsi5": tv["rsi5"],
        "rsi15": tv["rsi15"],
        "ema9_5": tv["ema9_5"],
        "ema21_5": tv["ema21_5"],
        "ema9_15": tv["ema9_15"],
        "ema21_15": tv["ema21_15"],
        "ema50_5": tv["ema50_5"],
        "ema50_15": tv["ema50_15"],
        "vwap5": tv["vwap5"],
        "atr5": tv["atr5"],
        "adx5": tv["adx5"],
        "adx15": tv["adx15"],
        "macd_5": tv["macd5"],
        "macd_signal_5": tv["macds5"],
        "macd_15": tv["macd15"],
        "macd_signal_15": tv["macds15"],
        "checks": checks,
        "quality_checks": [{"label": k, "ok": v} for k, v in quality_checks.items()],
        "quality_passed": quality_passed,
        "quality_total": len(quality_checks),
        "entry_state": entry_state,
        "entry_state_detail": entry_detail,
        "market_regime": regime,
        "buy_above": buy_above,
        "sell_below": sell_below,
        "trigger_level": trigger_level,
        "trigger_confirmations": trigger_count,
        "trigger_confirmed": trigger_confirmed,
        "trigger_started_at": _trigger.get("started_at"),
        "option": option,
        "execution_ready": execution_ready,
        "market_open": market_open_now(),
        "warning": warning,
        "updated": datetime.now(IST).strftime("%d-%b-%Y %I:%M:%S %p"),
        "data_source": "TradingView + NSE",
        "version": VERSION
    }

PAGE = r"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#07111f">
<title>NIFTY Professional Signals V3.9</title>
<style>
:root{--card:#0f1c2e;--card2:#12233a;--text:#eef5ff;--muted:#9bb0c9;--green:#22c55e;--red:#ef4444;--amber:#f59e0b;--line:#223855}
*{box-sizing:border-box}
body{margin:0;background:linear-gradient(180deg,#06101d,#0a1627);color:var(--text);font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;min-height:100vh}
.wrap{max-width:820px;margin:auto;padding:14px 12px 46px}
h1{font-size:24px;margin:6px 0}.sub,.small{color:var(--muted);font-size:13px;line-height:1.45}.sub{margin-bottom:12px}
.card{background:rgba(15,28,46,.98);border:1px solid var(--line);border-radius:18px;padding:14px;margin:10px 0}
.banner{background:#372b12;border:1px solid #7a5b17;border-radius:13px;padding:10px;color:#ffe4a3;font-size:12px}
.status{display:flex;gap:8px;align-items:center;font-size:13px;color:var(--muted);flex-wrap:wrap}
.dot{width:9px;height:9px;border-radius:50%;background:#64748b}.dot.on{background:var(--green);box-shadow:0 0 9px var(--green)}
.price{font-size:42px;font-weight:900;margin:5px 0}.signal{font-size:29px;font-weight:900}.buy{color:var(--green)}.sell{color:var(--red)}.watch{color:var(--amber)}.neutral{color:#cbd5e1}
.row{display:grid;grid-template-columns:1fr 1fr;gap:10px}.row3{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}@media(max-width:540px){.row3{grid-template-columns:1fr 1fr}}
.kpi{background:var(--card2);border:1px solid var(--line);border-radius:14px;padding:11px}.kpi .t{color:var(--muted);font-size:11px;text-transform:uppercase}.kpi .v{font-size:20px;font-weight:800;margin-top:3px}
.contract{font-size:27px;font-weight:900;margin:4px 0}
.pill{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:5px 9px;font-size:12px;margin:3px}.ok{border-color:#23673d;color:#8ef0ab}.bad{border-color:#73323a;color:#ff9fa9}
button{width:100%;border:0;border-radius:12px;padding:13px;font-weight:800;font-size:15px;background:#0ea5e9;color:#00101a;margin-top:8px}.secondary{background:#1d304a;color:#eaf3ff}.danger{background:#40202a;color:#ffd9df}
#error{white-space:pre-wrap;color:#ffb4bc;font-size:12px;margin-top:8px}
</style>
</head>
<body>
<div class="wrap">
<h1>NIFTY Professional Signals V3.9</h1>
<div class="sub">TradingView signal engine + NSE option contract + frozen trigger + option trade monitor</div>
<div class="banner">Decision-support only. Verify the selected contract, premium and risk in your broker before any real order.</div>

<div class="card">
<div class="status"><span id="dot" class="dot"></span><span id="status">Starting...</span><span id="marketStatus" style="margin-left:auto">--</span></div>
<div class="price" id="spot">--</div>
<div id="signal" class="signal neutral">WAITING</div>
<div class="small" id="updated">--</div>
<div id="error"></div>
</div>

<div class="card">
<div class="small">Entry quality</div>
<div id="qualityState" style="font-size:22px;font-weight:900">WAIT</div>
<div class="small" id="qualitySummary">Loading...</div>
<div class="small" id="regime">Market regime: --</div>
<div style="height:8px"></div><div id="qualityChecks"></div>
</div>

<div class="card">
<div class="small">Selected option / locked contract</div>
<div id="contract" class="contract">--</div>
<div class="small" id="expiry">--</div>
<div class="small" id="liquidity">--</div>
<div style="height:10px"></div>
<div class="row3">
<div class="kpi"><div class="t">OPTION LTP</div><div class="v" id="optionLtp">--</div></div>
<div class="kpi"><div class="t">BID</div><div class="v" id="bid">--</div></div>
<div class="kpi"><div class="t">ASK / ENTRY</div><div class="v" id="ask">--</div></div>
</div>
</div>

<div class="row">
<div class="kpi"><div class="t">ENTRY</div><div class="v" id="entry">--</div></div>
<div class="kpi"><div class="t">STOP LOSS</div><div class="v" id="sl">--</div></div>
</div>
<div style="height:8px"></div>
<div class="row">
<div class="kpi"><div class="t">TARGET 1</div><div class="v" id="t1">--</div></div>
<div class="kpi"><div class="t">TARGET 2</div><div class="v" id="t2">--</div></div>
</div>

<div class="card">
<div class="small">Trade monitor</div>
<div id="tradeState" style="font-size:22px;font-weight:900">NO ACTIVE TRADE</div>
<div id="tradeDetail" class="small">A trade is locked only after the signal, frozen trigger 2/2 and option quote are all confirmed.</div>
<button class="danger" id="resetTradeBtn">RESET TRADE MONITOR</button>
</div>

<div class="card">
<div class="small">Current scanner trigger levels</div>
<div class="row">
<div class="kpi"><div class="t">NIFTY BUY ABOVE</div><div class="v" id="buyAbove">--</div></div>
<div class="kpi"><div class="t">NIFTY SELL BELOW</div><div class="v" id="sellBelow">--</div></div>
</div>
<div class="small" id="triggerDetail" style="margin-top:8px">Frozen trigger: --</div>
</div>

<div class="card">
<div class="small">Current market indicators</div>
<div class="row3" style="margin-top:8px">
<div><div class="small">Rating 5m</div><b id="rating5">--</b></div>
<div><div class="small">Rating 15m</div><b id="rating15">--</b></div>
<div><div class="small">RSI 5m</div><b id="rsi5">--</b></div>
<div><div class="small">RSI 15m</div><b id="rsi15">--</b></div>
<div><div class="small">ADX 5m</div><b id="adx5">--</b></div>
<div><div class="small">ADX 15m</div><b id="adx15">--</b></div>
</div>
<div style="height:10px"></div><div id="checks"></div>
</div>

<button id="refresh">REFRESH NOW</button>
</div>

<script>
"use strict";
const $=id=>document.getElementById(id);
const fmt=x=>(x==null||!Number.isFinite(Number(x)))?"--":Number(x).toLocaleString("en-IN",{minimumFractionDigits:2,maximumFractionDigits:2});
const rupee=x=>(x==null||!Number.isFinite(Number(x)))?"--":"\u20B9"+fmt(x);

function readTrade(){
  try{return JSON.parse(localStorage.getItem("niftyV39Trade")||"null")}
  catch(e){return null}
}
function saveTrade(t){localStorage.setItem("niftyV39Trade",JSON.stringify(t))}
function clearTrade(){localStorage.removeItem("niftyV39Trade");updateTradeUI(null)}
function updateTradeUI(t){
  if(!t){
    $("tradeState").textContent="NO ACTIVE TRADE";
    $("tradeDetail").textContent="A trade is locked only after the signal, frozen trigger 2/2 and option quote are all confirmed.";
    return;
  }
  $("tradeState").textContent=t.state||"ENTRY LOCKED / MONITORING";
  const p=Number(t.currentLtp||0),e=Number(t.entry||0);
  const pnl=(p>0&&e>0)?((p-e)/e*100):null;
  $("tradeDetail").textContent=`${t.contract} | Entry ${rupee(e)} | Current ${rupee(p)}${pnl!==null?` | Ref P/L ${pnl>=0?"+":""}${pnl.toFixed(1)}%`:""} | SL ${rupee(t.sl)} | T1 ${rupee(t.t1)} | T2 ${rupee(t.t2)}`;
}

async function refreshLockedTrade(){
  const t=readTrade();
  if(!t)return;
  try{
    const r=await fetch(`/api/locked-option?strike=${encodeURIComponent(t.strike)}&type=${encodeURIComponent(t.type)}&expiry=${encodeURIComponent(t.expiry||"")}`,{cache:"no-store"});
    const q=await r.json();
    if(!r.ok||q.error)throw new Error(q.error||"quote unavailable");
    t.currentLtp=Number(q.ltp||0);
    const p=t.currentLtp;
    const effectiveSl=Number(t.trailingSl||0)>0?Number(t.trailingSl):Number(t.sl);

    if((t.state==="ENTRY LOCKED / MONITORING"||t.state==="TARGET 1 HIT")&&p<=effectiveSl){
      t.state="STOP LOSS / TRAIL HIT - CLOSED";
    }else if((t.state==="ENTRY LOCKED / MONITORING"||t.state==="TARGET 1 HIT")&&p>=Number(t.t2)){
      t.state="TARGET 2 HIT - CLOSED";
    }else if(t.state==="ENTRY LOCKED / MONITORING"&&p>=Number(t.t1)){
      t.state="TARGET 1 HIT";
      t.trailingSl=Math.max(Number(t.entry),Number(t.sl));
    }

    saveTrade(t);
    updateTradeUI(t);
  }catch(e){}
}

function maybeLockTrade(d){
  let t=readTrade();
  if(t){updateTradeUI(t);return}

  if(!(d.option&&d.execution_ready&&d.trigger_confirmed&&d.option.tradable))return;

  t={
    contract:d.option.contract,
    expiry:d.option.expiry,
    strike:Number(d.option.strike),
    type:d.option.type,
    entry:Number(d.option.entry),
    sl:Number(d.option.sl),
    t1:Number(d.option.target1),
    t2:Number(d.option.target2),
    currentLtp:Number(d.option.ltp||0),
    trailingSl:0,
    state:"ENTRY LOCKED / MONITORING",
    lockedAt:d.updated
  };
  saveTrade(t);
  updateTradeUI(t);
}

function render(d){
  $("dot").className="dot on";
  $("status").textContent="Connected - V3.9";
  $("marketStatus").textContent=d.market_open?"MARKET OPEN":"MARKET CLOSED";
  $("spot").textContent=fmt(d.spot);
  $("updated").textContent=`Updated ${d.updated} | ${d.data_source}`;
  $("error").textContent=d.warning||"";

  $("signal").textContent=d.signal;
  $("signal").className="signal "+(d.signal==="BUY"?"buy":d.signal==="SELL"?"sell":d.signal.includes("WATCH")?"watch":"neutral");

  $("qualityState").textContent=d.entry_state||"WAIT";
  $("qualitySummary").textContent=`Filters passed ${d.quality_passed||0}/${d.quality_total||0}. ${d.entry_state_detail||""}`;
  $("regime").textContent=`Market regime: ${d.market_regime||"--"}`;
  $("qualityChecks").innerHTML=(d.quality_checks||[]).map(x=>`<span class="pill ${x.ok?"ok":"bad"}">${x.ok?"PASS":"FAIL"} - ${x.label}</span>`).join("");

  $("buyAbove").textContent=fmt(d.buy_above);
  $("sellBelow").textContent=fmt(d.sell_below);
  $("triggerDetail").textContent=`Frozen trigger: ${d.trigger_level==null?"--":fmt(d.trigger_level)} | confirmation ${d.trigger_confirmations||0}/2`;

  $("rating5").textContent=d.rating5;
  $("rating15").textContent=d.rating15;
  $("rsi5").textContent=fmt(d.rsi5);
  $("rsi15").textContent=fmt(d.rsi15);
  $("adx5").textContent=fmt(d.adx5);
  $("adx15").textContent=fmt(d.adx15);
  $("checks").innerHTML=(d.checks||[]).map(x=>`<span class="pill ${x.ok?"ok":"bad"}">${x.ok?"PASS":"FAIL"} - ${x.label}</span>`).join("");

  const t=readTrade();
  if(t){
    $("contract").textContent=t.contract;
    $("expiry").textContent=`Locked expiry ${t.expiry} | Strike ${t.strike} | ${t.type}`;
    $("entry").textContent=rupee(t.entry);
    $("sl").textContent=rupee(t.sl);
    $("t1").textContent=rupee(t.t1);
    $("t2").textContent=rupee(t.t2);
    updateTradeUI(t);
    return;
  }

  if(d.option){
    $("contract").textContent=d.option.contract;
    $("expiry").textContent=`Expiry ${d.option.expiry} | Strike ${d.option.strike} | ${d.option.type}`;
    $("liquidity").textContent=`Liquidity ${d.option.liquidity} | Volume ${d.option.volume} | OI ${d.option.oi} | Spread ${fmt(d.option.spread_pct)}%`;
    $("optionLtp").textContent=rupee(d.option.ltp);
    $("bid").textContent=rupee(d.option.bid);
    $("ask").textContent=rupee(d.option.ask);
    $("entry").textContent=rupee(d.option.entry);
    $("sl").textContent=rupee(d.option.sl);
    $("t1").textContent=rupee(d.option.target1);
    $("t2").textContent=rupee(d.option.target2);
  }else{
    $("contract").textContent="--";
    $("expiry").textContent="No option locked yet";
    $("liquidity").textContent="--";
    $("optionLtp").textContent="--";
    $("bid").textContent="--";
    $("ask").textContent="--";
    $("entry").textContent="--";
    $("sl").textContent="--";
    $("t1").textContent="--";
    $("t2").textContent="--";
  }

  maybeLockTrade(d);
  updateTradeUI(readTrade());
}

async function refresh(){
  $("status").textContent="Updating...";
  try{
    const r=await fetch("/api/signal",{cache:"no-store"});
    const d=await r.json();
    if(!r.ok||d.error)throw new Error(d.error||"request failed");
    render(d);
  }catch(e){
    $("dot").className="dot";
    $("status").textContent="Data unavailable";
    $("error").textContent="Live refresh failed: "+e.message;
  }
}

$("refresh").onclick=refresh;
$("resetTradeBtn").onclick=()=>{clearTrade();refresh()};
updateTradeUI(readTrade());
refresh();
setInterval(refresh,15000);
setInterval(refreshLockedTrade,15000);
</script>
</body>
</html>"""

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True, "version": VERSION})

@app.route("/api/locked-option", methods=["GET"])
def api_locked_option():
    try:
        strike = request.args.get("strike", type=float)
        opt_type = (request.args.get("type") or "").upper()
        expiry_date = request.args.get("expiry") or ""

        if strike is None or opt_type not in ("CE", "PE"):
            return jsonify({"error": "Invalid locked option parameters."}), 400

        return jsonify(exact_option_quote(strike, opt_type, expiry_date))
    except Exception as e:
        return jsonify({"error": str(e)}), 503

@app.route("/api/signal", methods=["GET"])
def api_signal():
    now = time.time()
    if _cache["signal"] is not None and now - _cache["ts"] < 12:
        return jsonify(_cache["signal"])

    try:
        result = build_signal()
        _cache["signal"] = result
        _cache["ts"] = now
        return jsonify(result)
    except Exception as e:
        if _cache["signal"] is not None:
            old = dict(_cache["signal"])
            old["warning"] = "Using cached data: " + str(e)
            return jsonify(old)
        return jsonify({"error": str(e)}), 503

@app.route("/", methods=["GET"])
@app.route("/<path:p>", methods=["GET"])
def home(p=""):
    return PAGE, 200, {"Content-Type":"text/html; charset=utf-8"}

if __name__ == "__main__":
    import os
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")))
