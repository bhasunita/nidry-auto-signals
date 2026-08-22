# NIFTY Professional Signals V4.1
# Decision-support only. This application DOES NOT place broker orders.
#
# Core design:
# - NIFTY only
# - TradingView public scanner for NIFTY technical data
# - NSE option-chain for selected CE/PE premium and locked-contract monitoring
# - 5m + 15m + 30m confirmation
# - weighted confidence + hard entry gates
# - frozen trigger 2/2
# - server-side background monitor
# - server-side locked trade state
# - WhatsApp alerts through Twilio
# - Target 1, dynamic trail, Target 2, optional target extension
# - reversal / weakening / exit-review warnings
# - end-of-day carry / caution / exit decision
# - session journal
#
# Recommended Render start command:
# gunicorn app:app --workers 1 --threads 4 --timeout 120
#
# Render environment variables already used:
# TWILIO_ACCOUNT_SID
# TWILIO_AUTH_TOKEN
# TWILIO_WHATSAPP_FROM
# WHATSAPP_TO
#
# Optional:
# ENABLE_BACKGROUND_MONITOR=1
# MONITOR_INTERVAL_SECONDS=15
# STATE_FILE=/tmp/nifty_v41_state.json
#
# IMPORTANT:
# A Render Free web service can sleep when idle. When it sleeps, no Python
# process can monitor the market. For genuinely unattended monitoring, use an
# always-on service/background worker. No software can guarantee that no trade
# will ever be missed.

from flask import Flask, jsonify, request
import os
import json
import time
import math
import threading
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

try:
    from twilio.rest import Client as TwilioClient
except Exception:
    TwilioClient = None

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("nifty-v41")

IST = ZoneInfo("Asia/Kolkata")
VERSION = "4.0"

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
    "Referer": "https://www.tradingview.com/",
}
NSE_HEADERS = {
    "User-Agent": TV_HEADERS["User-Agent"],
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": NSE_OC_PAGE,
    "X-Requested-With": "XMLHttpRequest",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
}

TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "").strip()
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "").strip()
TWILIO_WHATSAPP_FROM = os.environ.get("TWILIO_WHATSAPP_FROM", "").strip()
WHATSAPP_TO = os.environ.get("WHATSAPP_TO", "").strip()

ENABLE_BACKGROUND_MONITOR = os.environ.get("ENABLE_BACKGROUND_MONITOR", "1").lower() in ("1", "true", "yes", "on")
MONITOR_INTERVAL_SECONDS = max(10, int(os.environ.get("MONITOR_INTERVAL_SECONDS", "15")))
STATE_FILE = os.environ.get("STATE_FILE", "/tmp/nifty_v41_state.json")

CACHE_TTL = 12

# V4.1 professional risk controls
V41_MIN_CONFIDENCE = int(os.environ.get("V41_MIN_CONFIDENCE", "82"))
V41_MAX_TRADES_PER_DAY = int(os.environ.get("V41_MAX_TRADES_PER_DAY", "3"))
V41_MAX_CONSECUTIVE_LOSSES = int(os.environ.get("V41_MAX_CONSECUTIVE_LOSSES", "2"))
V41_COOLDOWN_AFTER_STOP_MIN = int(os.environ.get("V41_COOLDOWN_AFTER_STOP_MIN", "20"))
V41_MAX_OPTION_SPREAD_PCT = float(os.environ.get("V41_MAX_OPTION_SPREAD_PCT", "1.25"))
V41_RISK_REWARD_MIN = float(os.environ.get("V41_RISK_REWARD_MIN", "1.5"))

_signal_cache = {"signal": None, "ts": 0}
_locked_quote_cache = {}

_trigger_state = {
    "date": None,
    "direction": None,
    "level": None,
    "count": 0,
    "confirmed": False,
    "misses": 0,
    "started_at": None,
}

_state_lock = threading.RLock()
_monitor_thread_started = False

_state = {
    "trade": None,
    "journal": [],
    "last_alert_keys": {},
    "last_signal": None,
    "last_monitor_at": None,
    "last_monitor_error": "",
    "whatsapp_last_ok": False,
    "last_event": None,
    "v41_risk": {"date": None, "trades_today": 0, "consecutive_losses": 0,
                 "last_stop_at": None, "blocked_reason": ""},
}


# ---------------------------------------------------------------------
# General helpers
# ---------------------------------------------------------------------
def now_ist():
    return datetime.now(IST)


def market_open_now():
    n = now_ist()
    if n.weekday() >= 5:
        return False
    mins = n.hour * 60 + n.minute
    return 555 <= mins <= 930  # 09:15 - 15:30


def new_entry_window_now():
    n = now_ist()
    if n.weekday() >= 5:
        return False
    mins = n.hour * 60 + n.minute
    # Avoid first 5 minutes and very-late fresh entries.
    return 560 <= mins <= 885  # 09:20 - 14:45


def fmt(x, digits=2):
    try:
        return round(float(x), digits)
    except Exception:
        return None


def tv_rating(v):
    if v >= .5:
        return "STRONG BUY"
    if v >= .1:
        return "BUY"
    if v <= -.5:
        return "STRONG SELL"
    if v <= -.1:
        return "SELL"
    return "NEUTRAL"


def rating_side(x):
    x = str(x or "").upper()
    if "BUY" in x:
        return "BUY"
    if "SELL" in x:
        return "SELL"
    return "NEUTRAL"


def expiry_parse(x):
    for f in ("%d-%b-%Y", "%d-%b-%y"):
        try:
            return datetime.strptime(x, f).date()
        except Exception:
            pass
    return None


def nearest_expiry(xs):
    today = now_ist().date()
    parsed = [(expiry_parse(x), x) for x in xs]
    parsed = [x for x in parsed if x[0]]
    future = [x for x in parsed if x[0] >= today]
    usable = future or parsed
    if not usable:
        raise RuntimeError("No usable NIFTY expiry.")
    return sorted(usable)[0][1]


def expiry_days(exp):
    d = expiry_parse(exp)
    if not d:
        return None
    return (d - now_ist().date()).days


# ---------------------------------------------------------------------
# Persistent server state
# ---------------------------------------------------------------------
def load_state():
    global _state
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for k in ("trade", "journal", "last_alert_keys", "last_event", "v41_risk"):
                    if k in data:
                        _state[k] = data[k]
    except Exception as e:
        log.warning("State load failed: %s", e)


def save_state():
    try:
        payload = {
            "trade": _state.get("trade"),
            "journal": _state.get("journal", [])[:100],
            "last_alert_keys": _state.get("last_alert_keys", {}),
            "last_event": _state.get("last_event"),
            "v41_risk": _state.get("v41_risk", {}),
        }
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
    except Exception as e:
        log.warning("State save failed: %s", e)


def add_journal(kind, trade=None, detail=""):
    with _state_lock:
        item = {
            "time": now_ist().strftime("%d-%b-%Y %I:%M:%S %p"),
            "kind": kind,
            "contract": (trade or {}).get("contract", "--"),
            "detail": detail,
        }
        _state["journal"].insert(0, item)
        _state["journal"] = _state["journal"][:100]
        _state["last_event"] = item
        save_state()


load_state()



# ---------------------------------------------------------------------
# V4.1 professional execution / capital-protection layer
# ---------------------------------------------------------------------
def v41_reset_daily_risk():
    today = now_ist().date().isoformat()
    r = _state.setdefault("v41_risk", {})
    if r.get("date") != today:
        r.update({"date": today, "trades_today": 0, "consecutive_losses": 0,
                  "last_stop_at": None, "blocked_reason": ""})
        save_state()
    return r


def v41_risk_permission():
    r = v41_reset_daily_risk()
    if int(r.get("trades_today", 0) or 0) >= V41_MAX_TRADES_PER_DAY:
        return False, "DAILY TRADE LIMIT"
    if int(r.get("consecutive_losses", 0) or 0) >= V41_MAX_CONSECUTIVE_LOSSES:
        return False, "CONSECUTIVE LOSS LOCK"
    last_stop = r.get("last_stop_at")
    if last_stop:
        try:
            t = datetime.fromisoformat(last_stop)
            if t.tzinfo is None:
                t = t.replace(tzinfo=IST)
            elapsed = (now_ist() - t).total_seconds() / 60
            if elapsed < V41_COOLDOWN_AFTER_STOP_MIN:
                return False, "POST-STOP COOLDOWN"
        except Exception:
            pass
    return True, "RISK CLEAR"


def v41_market_regime(d):
    a5 = float(d.get("adx5", 0) or 0)
    a15 = float(d.get("adx15", 0) or 0)
    a30 = float(d.get("adx30", 0) or 0)
    if a15 >= 25 and a30 >= 22:
        return "STRONG TREND"
    if a15 >= 18 and a30 >= 16:
        return "TREND"
    if a5 < 18 and a15 < 18:
        return "CHOP / LOW TREND"
    return "TRANSITION"


def v41_entry_timing(d, bull):
    score, reasons = 0, []
    spot = float(d.get("spot", 0) or 0)
    vwap = float(d.get("vwap5", 0) or 0)
    r5 = float(d.get("rsi5", 50) or 50)
    r15 = float(d.get("rsi15", 50) or 50)
    a5 = float(d.get("adx5", 0) or 0)
    a15 = float(d.get("adx15", 0) or 0)

    tests = [
        (((spot > vwap) if bull else (spot < vwap)), 20, "VWAP"),
        (((52 <= r5 <= 68) if bull else (32 <= r5 <= 48)), 20, "RSI 5m"),
        (((50 <= r15 <= 68) if bull else (32 <= r15 <= 50)), 15, "RSI 15m"),
        (a15 >= 18, 20, "ADX 15m"),
        (a5 >= 16, 10, "ADX 5m"),
        (new_entry_window_now(), 15, "ENTRY WINDOW"),
    ]
    for ok, pts, name in tests:
        if ok: score += pts
        else: reasons.append(name)
    return min(100, score), reasons


def v41_risk_quality(d, option=None):
    score, reasons = 100, []
    regime = v41_market_regime(d)
    if regime == "CHOP / LOW TREND":
        score -= 35; reasons.append("CHOP")
    elif regime == "TRANSITION":
        score -= 15; reasons.append("TRANSITION")

    if option:
        bid = float(option.get("bid") or option.get("bidprice") or 0)
        ask = float(option.get("ask") or option.get("askprice") or option.get("entry") or 0)
        mid = (bid + ask) / 2 if bid > 0 and ask > 0 else 0
        spread = ((ask - bid) / mid * 100) if mid and ask >= bid else None
        if spread is not None and spread > V41_MAX_OPTION_SPREAD_PCT:
            score -= 45; reasons.append(f"WIDE SPREAD {spread:.2f}%")
    return max(0, score), reasons


def v41_professional_gate(d, direction, trend_quality=0, option=None):
    bull = str(direction).upper() in ("BUY", "CE", "BULL")
    allowed, why = v41_risk_permission()
    timing, timing_bad = v41_entry_timing(d, bull)
    risk, risk_bad = v41_risk_quality(d, option)
    trend = int(trend_quality or 0)
    combined = round(.45 * trend + .30 * timing + .25 * risk)
    regime = v41_market_regime(d)
    blocks = []
    if not allowed: blocks.append(why)
    if regime == "CHOP / LOW TREND": blocks.append("CHOP FILTER")
    if timing < 65: blocks.append("ENTRY TIMING")
    if risk < 65: blocks.append("RISK / LIQUIDITY")
    if combined < V41_MIN_CONFIDENCE: blocks.append("QUALITY SCORE")
    return {"pass": not blocks, "combined": combined, "trend_quality": trend,
            "entry_timing": timing, "risk_quality": risk, "regime": regime,
            "blocks": blocks, "timing_reasons": timing_bad, "risk_reasons": risk_bad,
            "path": "CANDLE CLOSE -> BREAKOUT -> RETEST/HOLD -> LIQUIDITY -> RISK -> LOCK"}


def v41_note_trade_locked():
    r = v41_reset_daily_risk()
    r["trades_today"] = int(r.get("trades_today", 0) or 0) + 1
    save_state()


def v41_note_trade_result(result):
    r = v41_reset_daily_risk()
    x = str(result).upper()
    if "STOP" in x or "LOSS" in x:
        r["consecutive_losses"] = int(r.get("consecutive_losses", 0) or 0) + 1
        r["last_stop_at"] = now_ist().isoformat()
    elif "TARGET" in x or "WIN" in x:
        r["consecutive_losses"] = 0
    save_state()


# ---------------------------------------------------------------------
# WhatsApp
# ---------------------------------------------------------------------
def whatsapp_configured():
    return bool(
        TwilioClient
        and TWILIO_ACCOUNT_SID
        and TWILIO_AUTH_TOKEN
        and TWILIO_WHATSAPP_FROM
        and WHATSAPP_TO
    )


def send_whatsapp(title, body, dedupe_key=None, cooldown=300):
    now_ts = time.time()

    if dedupe_key:
        last = float(_state["last_alert_keys"].get(dedupe_key, 0) or 0)
        if now_ts - last < cooldown:
            return False

    if not whatsapp_configured():
        log.info("WhatsApp not configured: %s | %s", title, body)
        return False

    try:
        client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        client.messages.create(
            body=f"{title}\n{body}",
            from_=TWILIO_WHATSAPP_FROM,
            to=WHATSAPP_TO,
        )
        with _state_lock:
            _state["whatsapp_last_ok"] = True
            if dedupe_key:
                _state["last_alert_keys"][dedupe_key] = now_ts
            save_state()
        return True
    except Exception as e:
        log.exception("WhatsApp send failed")
        with _state_lock:
            _state["whatsapp_last_ok"] = False
            _state["last_monitor_error"] = f"WhatsApp: {e}"
        return False


# ---------------------------------------------------------------------
# TradingView NIFTY technical data
# ---------------------------------------------------------------------
def fetch_tv():
    cols = [
        # 5m
        "close|5","open|5","high|5","low|5","Recommend.All|5","RSI|5",
        "EMA9|5","EMA21|5","EMA50|5","MACD.macd|5","MACD.signal|5","ADX|5","ATR|5","VWAP|5",
        # 15m
        "close|15","open|15","high|15","low|15","Recommend.All|15","RSI|15",
        "EMA9|15","EMA21|15","EMA50|15","MACD.macd|15","MACD.signal|15","ADX|15",
        # 30m
        "close|30","open|30","high|30","low|30","Recommend.All|30","RSI|30",
        "EMA9|30","EMA21|30","EMA50|30","MACD.macd|30","MACD.signal|30","ADX|30",
    ]

    payload = {
        "symbols": {"tickers": ["NSE:NIFTY"], "query": {"types": []}},
        "columns": cols,
        "range": [0, 1],
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

    def f(k, default=0):
        try:
            return float(x[k]) if x[k] is not None else float(default)
        except Exception:
            return float(default)

    spot = f("close|5")
    if spot <= 0:
        raise RuntimeError("TradingView returned invalid NIFTY price.")

    return {
        "spot": spot,

        "open5": f("open|5", spot),
        "high5": f("high|5", spot),
        "low5": f("low|5", spot),
        "rec5": f("Recommend.All|5"),
        "rsi5": f("RSI|5", 50),
        "ema9_5": f("EMA9|5", spot),
        "ema21_5": f("EMA21|5", spot),
        "ema50_5": f("EMA50|5", spot),
        "macd5": f("MACD.macd|5"),
        "macds5": f("MACD.signal|5"),
        "adx5": f("ADX|5"),
        "atr5": max(f("ATR|5", 1), .01),
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
        "adx15": f("ADX|15"),

        "open30": f("open|30", spot),
        "high30": f("high|30", spot),
        "low30": f("low|30", spot),
        "close30": f("close|30", spot),
        "rec30": f("Recommend.All|30"),
        "rsi30": f("RSI|30", 50),
        "ema9_30": f("EMA9|30", spot),
        "ema21_30": f("EMA21|30", spot),
        "ema50_30": f("EMA50|30", spot),
        "macd30": f("MACD.macd|30"),
        "macds30": f("MACD.signal|30"),
        "adx30": f("ADX|30"),
    }


def weighted_score(tv, bull=True):
    score = 0
    checks = []

    def add(label, ok, pts):
        nonlocal score
        ok = bool(ok)
        checks.append({"label": label, "ok": ok, "points": pts})
        if ok:
            score += pts

    add("5m rating confirms", tv["rec5"] >= .1 if bull else tv["rec5"] <= -.1, 7)
    add("15m rating confirms", tv["rec15"] >= .1 if bull else tv["rec15"] <= -.1, 10)
    add("30m rating confirms", tv["rec30"] >= .1 if bull else tv["rec30"] <= -.1, 12)

    add("EMA 9/21 5m aligned", tv["ema9_5"] > tv["ema21_5"] if bull else tv["ema9_5"] < tv["ema21_5"], 7)
    add("EMA 9/21 15m aligned", tv["ema9_15"] > tv["ema21_15"] if bull else tv["ema9_15"] < tv["ema21_15"], 9)
    add("EMA 9/21 30m aligned", tv["ema9_30"] > tv["ema21_30"] if bull else tv["ema9_30"] < tv["ema21_30"], 10)

    add("Price beyond EMA50 5m", tv["spot"] > tv["ema50_5"] if bull else tv["spot"] < tv["ema50_5"], 6)
    add("Price beyond EMA50 15m", tv["spot"] > tv["ema50_15"] if bull else tv["spot"] < tv["ema50_15"], 7)
    add("Price beyond EMA50 30m", tv["spot"] > tv["ema50_30"] if bull else tv["spot"] < tv["ema50_30"], 8)

    add("Price on correct side of VWAP", tv["spot"] > tv["vwap5"] if bull else tv["spot"] < tv["vwap5"], 6)

    add("RSI 5m healthy", (52 <= tv["rsi5"] <= 70) if bull else (30 <= tv["rsi5"] <= 48), 3)
    add("RSI 15m healthy", (50 <= tv["rsi15"] <= 68) if bull else (32 <= tv["rsi15"] <= 50), 3)
    add("RSI 30m healthy", (48 <= tv["rsi30"] <= 68) if bull else (32 <= tv["rsi30"] <= 52), 3)

    add("MACD 5m confirms", tv["macd5"] > tv["macds5"] if bull else tv["macd5"] < tv["macds5"], 4)
    add("MACD 15m confirms", tv["macd15"] > tv["macds15"] if bull else tv["macd15"] < tv["macds15"], 5)
    add("MACD 30m confirms", tv["macd30"] > tv["macds30"] if bull else tv["macd30"] < tv["macds30"], 5)

    add("ADX 5m >= 18", tv["adx5"] >= 18, 2)
    add("ADX 15m >= 18", tv["adx15"] >= 18, 2)
    add("ADX 30m >= 16", tv["adx30"] >= 16, 1)

    return min(100, score), checks


def quality_gate(tv, bull, score_value):
    same_direction = (
        tv["rec5"] >= .1 and tv["rec15"] >= .1 and tv["rec30"] >= .1
    ) if bull else (
        tv["rec5"] <= -.1 and tv["rec15"] <= -.1 and tv["rec30"] <= -.1
    )

    ema_trend = (
        tv["ema9_5"] > tv["ema21_5"]
        and tv["ema9_15"] > tv["ema21_15"]
        and tv["ema9_30"] > tv["ema21_30"]
    ) if bull else (
        tv["ema9_5"] < tv["ema21_5"]
        and tv["ema9_15"] < tv["ema21_15"]
        and tv["ema9_30"] < tv["ema21_30"]
    )

    long_trend = (
        tv["spot"] > tv["ema50_5"]
        and tv["spot"] > tv["ema50_15"]
        and tv["spot"] > tv["ema50_30"]
    ) if bull else (
        tv["spot"] < tv["ema50_5"]
        and tv["spot"] < tv["ema50_15"]
        and tv["spot"] < tv["ema50_30"]
    )

    vwap_ok = tv["spot"] > tv["vwap5"] if bull else tv["spot"] < tv["vwap5"]

    macd_ok = (
        tv["macd5"] > tv["macds5"]
        and tv["macd15"] > tv["macds15"]
        and tv["macd30"] > tv["macds30"]
    ) if bull else (
        tv["macd5"] < tv["macds5"]
        and tv["macd15"] < tv["macds15"]
        and tv["macd30"] < tv["macds30"]
    )

    rsi_ok = (
        50 <= tv["rsi5"] <= 70
        and 50 <= tv["rsi15"] <= 68
        and 48 <= tv["rsi30"] <= 68
    ) if bull else (
        30 <= tv["rsi5"] <= 50
        and 32 <= tv["rsi15"] <= 50
        and 32 <= tv["rsi30"] <= 52
    )

    if tv["adx5"] < 15 or tv["adx15"] < 15:
        regime = "CHOP / LOW TREND"
        regime_ok = False
    elif tv["adx5"] < 18 or tv["adx15"] < 18 or tv["adx30"] < 16:
        regime = "TRANSITION / DEVELOPING TREND"
        regime_ok = False
    else:
        regime = "TRENDING"
        regime_ok = True

    candle_ok = tv["spot"] > tv["open5"] if bull else tv["spot"] < tv["open5"]

    buffer = max(tv["atr5"] * .12, 2.0)
    if bull:
        structure = max(tv["ema21_5"], tv["ema50_5"], tv["vwap5"])
        breakout_ok = tv["spot"] >= structure + buffer
    else:
        structure = min(tv["ema21_5"], tv["ema50_5"], tv["vwap5"])
        breakout_ok = tv["spot"] <= structure - buffer

    higher_tf_conflict = tv["rec30"] <= -.1 if bull else tv["rec30"] >= .1
    stretched = (
        tv["rsi5"] > 74 or tv["rsi15"] > 74
    ) if bull else (
        tv["rsi5"] < 26 or tv["rsi15"] < 26
    )

    hard = {
        "5m + 15m + 30m direction agree": same_direction,
        "EMA 9/21 aligned across timeframes": ema_trend,
        "Price beyond EMA50 across timeframes": long_trend,
        "VWAP confirms direction": vwap_ok,
        "MACD confirms 5m + 15m + 30m": macd_ok,
        "Trend regime strong enough": regime_ok,
        "RSI healthy / not stretched": rsi_ok and not stretched,
        "5m candle confirms direction": candle_ok,
        "Breakout clears structure + ATR buffer": breakout_ok,
        "No higher-timeframe conflict": not higher_tf_conflict,
        "Fresh-entry time window": new_entry_window_now(),
    }

    passed = sum(1 for v in hard.values() if v)

    confirmed = all(hard.values()) and score_value >= 84

    prepare_core = (
        same_direction
        and ema_trend
        and long_trend
        and vwap_ok
        and not higher_tf_conflict
        and not stretched
        and new_entry_window_now()
        and score_value >= 70
    )
    prepare = prepare_core and passed >= 8 and not confirmed

    if confirmed:
        state = "CONFIRMED"
        detail = "High-confluence setup. Frozen trigger 2/2 and a tradable option quote are still required."
    elif prepare:
        state = "PREPARE"
        detail = "Setup is developing. Execution stays blocked until every hard confirmation passes."
    elif not new_entry_window_now():
        state = "WAIT"
        detail = "Fresh-entry window is closed. Existing locked trades continue to be monitored."
    elif regime == "CHOP / LOW TREND":
        state = "AVOID"
        detail = "Low-trend/choppy regime. New entries are intentionally blocked."
    elif higher_tf_conflict:
        state = "WAIT"
        detail = "30m direction conflicts with the intended trade."
    elif stretched:
        state = "WAIT"
        detail = "Momentum is stretched. Wait for a safer reset."
    else:
        state = "WAIT"
        detail = "Conditions are mixed. Wait for stronger multi-timeframe alignment."

    return {
        "confirmed": confirmed,
        "prepare": prepare,
        "checks": hard,
        "passed": passed,
        "state": state,
        "detail": detail,
        "regime": regime,
        "candle_ok": candle_ok,
        "breakout_ok": breakout_ok,
        "higher_tf_conflict": higher_tf_conflict,
        "stretched": stretched,
    }


# ---------------------------------------------------------------------
# NSE option chain
# ---------------------------------------------------------------------
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

    r = s.get(
        NSE_OC_V3,
        params={"type": "Indices", "symbol": "NIFTY", "expiry": ex},
        timeout=12,
    )

    if r.status_code in (401, 403):
        s = requests.Session()
        s.headers.update(NSE_HEADERS)
        s.get(NSE_HOME, timeout=10)
        s.get(NSE_OC_PAGE, timeout=10)
        r = s.get(
            NSE_OC_V3,
            params={"type": "Indices", "symbol": "NIFTY", "expiry": ex},
            timeout=12,
        )

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
    seen = []

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
        row_exp = row.get("expiryDate")
        if row_exp and row_exp != ex:
            continue

        side = row.get(typ)
        if not side:
            continue

        st = float(row.get("strikePrice", 0) or 0)
        if abs(st - atm) > 350:
            continue

        ltp = num(side, "lastPrice", "ltp", "last_price")
        bid = num(side, "buyPrice1", "bidPrice", "bidprice", "bid", "bestBid")
        ask = num(side, "sellPrice1", "askPrice", "askprice", "ask", "bestAsk")
        bid_qty = num(side, "buyQuantity1", "bidQty", "bidQuantity", "bestBidQty")
        ask_qty = num(side, "sellQuantity1", "askQty", "askQuantity", "bestAskQty")
        volume = int(num(side, "totalTradedVolume", "volume"))
        oi = int(num(side, "openInterest", "oi"))

        seen.append((abs(st - atm), st, ltp, bid, ask, volume, oi))

        if ltp <= 0 or bid <= 0 or ask <= 0 or ask < bid:
            continue

        mid = (ask + bid) / 2
        spread_pct = ((ask - bid) / mid * 100) if mid > 0 else 999

        # Professional liquidity gate.
        if spread_pct > 8 or volume < 500 or oi <= 0:
            continue

        depth_bonus = min(math.log10(max(bid_qty + ask_qty, 1) + 1), 5)

        rank = (
            (abs(st - atm) / 50) * 6
            + spread_pct * 3
            - min(math.log10(volume + 1), 6) * 4
            - min(math.log10(oi + 1), 7) * 3
            - depth_bonus * 2
        )
        candidates.append(
            (rank, st, ltp, bid, ask, volume, oi, spread_pct, bid_qty, ask_qty)
        )

    if not candidates:
        nearby = sorted(seen)[:5]
        detail = "; ".join(
            f"{int(st)} {typ}: LTP {ltp:.2f}, bid {bid:.2f}, ask {ask:.2f}, vol {vol}, OI {oi}"
            for _, st, ltp, bid, ask, vol, oi in nearby
        )
        raise RuntimeError("No nearby option passed professional liquidity checks. " + detail)

    _, st, ltp, bid, ask, volume, oi, spread_pct, bid_qty, ask_qty = sorted(candidates)[0]

    entry = ask
    spread = ask - bid

    base_pct = .18
    if confidence >= 92:
        base_pct = .13
    elif confidence >= 86:
        base_pct = .14
    elif confidence >= 80:
        base_pct = .16

    atr_ratio = (float(atr5) / max(float(spot), 1)) * 100
    if atr_ratio > .08:
        base_pct += .02

    risk = min(max(entry * base_pct, spread * 3, entry * .10), entry * .22)

    rr1 = 1.50 if confidence >= 88 else 1.35
    rr2 = 2.50 if confidence >= 92 else 2.25

    liquidity = "GOOD" if spread_pct <= 4 and volume >= 2000 and bid_qty > 0 and ask_qty > 0 else "FAIR"

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
        "risk": round(risk, 2),
        "risk_pct": round(risk / entry * 100, 1),
        "rr1": rr1,
        "rr2": rr2,
        "volume": volume,
        "oi": oi,
        "bid_qty": int(bid_qty),
        "ask_qty": int(ask_qty),
        "spread_pct": round(spread_pct, 2),
        "liquidity": liquidity,
        "tradable": True,
    }


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
        fallback = None

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
                if fallback is None:
                    fallback = (row, side, row_exp)
                continue

            ltp = num(side, "lastPrice", "ltp", "last_price")
            bid = num(side, "buyPrice1", "bidPrice", "bidprice", "bid", "bestBid")
            ask = num(side, "sellPrice1", "askPrice", "askprice", "ask", "bestAsk")
            bid_qty = num(side, "buyQuantity1", "bidQty", "bidQuantity", "bestBidQty")
            ask_qty = num(side, "sellQuantity1", "askQty", "askQuantity", "bestAskQty")
            volume = int(num(side, "totalTradedVolume", "volume"))
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
                "bid_qty": int(bid_qty),
                "ask_qty": int(ask_qty),
                "volume": volume,
                "oi": oi,
                "spread_pct": round(sp, 2) if sp is not None else None,
                "source": "NSE live",
                "stale": False,
                "quote_time": now_ist().strftime("%H:%M:%S"),
            }

            if q["ltp"] > 0:
                _locked_quote_cache[cache_key] = {"quote": q, "ts": time.time()}
                return q

        if fallback:
            row, side, row_exp = fallback
            ltp = num(side, "lastPrice", "ltp", "last_price")
            bid = num(side, "buyPrice1", "bidPrice", "bidprice", "bid", "bestBid")
            ask = num(side, "sellPrice1", "askPrice", "askprice", "ask", "bestAsk")
            volume = int(num(side, "totalTradedVolume", "volume"))
            oi = int(num(side, "openInterest", "oi"))
            q = {
                "contract": f"NIFTY {int(strike)} {opt_type}",
                "expiry": row_exp or expiry_date,
                "strike": strike,
                "type": opt_type,
                "ltp": round(ltp, 2),
                "bid": round(bid, 2),
                "ask": round(ask, 2),
                "bid_qty": 0,
                "ask_qty": 0,
                "volume": volume,
                "oi": oi,
                "spread_pct": None,
                "source": "NSE same-strike fallback",
                "stale": False,
                "quote_time": now_ist().strftime("%H:%M:%S"),
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


# ---------------------------------------------------------------------
# Frozen trigger
# ---------------------------------------------------------------------
def confirm_trigger(direction, spot, atr, setup_active):
    global _trigger_state

    today = str(now_ist().date())

    def fresh():
        return {
            "date": today,
            "direction": None,
            "level": None,
            "count": 0,
            "confirmed": False,
            "misses": 0,
            "started_at": None,
        }

    if _trigger_state.get("date") != today:
        _trigger_state = fresh()

    if direction not in ("BUY", "SELL"):
        _trigger_state = fresh()
        return None, 0, False, False

    if not setup_active:
        if _trigger_state["direction"] == direction and _trigger_state["level"] is not None:
            _trigger_state["misses"] += 1
            _trigger_state["count"] = 0
            if _trigger_state["misses"] <= 2:
                return float(_trigger_state["level"]), 0, bool(_trigger_state["confirmed"]), True
        _trigger_state = fresh()
        return None, 0, False, False

    if _trigger_state["direction"] != direction or _trigger_state["level"] is None:
        buffer = max(float(atr) * .15, 5.0)
        level = spot + buffer if direction == "BUY" else spot - buffer
        _trigger_state = {
            "date": today,
            "direction": direction,
            "level": round(level, 2),
            "count": 0,
            "confirmed": False,
            "misses": 0,
            "started_at": now_ist().strftime("%H:%M:%S"),
        }
    else:
        _trigger_state["misses"] = 0

    level = float(_trigger_state["level"])
    beyond = spot >= level if direction == "BUY" else spot <= level

    if _trigger_state["confirmed"]:
        return level, 2, True, True

    _trigger_state["count"] = _trigger_state["count"] + 1 if beyond else 0

    if _trigger_state["count"] >= 2:
        _trigger_state["confirmed"] = True

    return level, min(_trigger_state["count"], 2), bool(_trigger_state["confirmed"]), True


# ---------------------------------------------------------------------
# Build full market signal
# ---------------------------------------------------------------------
def build_signal(force=False):
    now_ts = time.time()

    if (
        not force
        and _signal_cache["signal"] is not None
        and now_ts - _signal_cache["ts"] < CACHE_TTL
    ):
        return _signal_cache["signal"]

    tv = fetch_tv()

    bull_score, bull_checks = weighted_score(tv, True)
    bear_score, bear_checks = weighted_score(tv, False)

    bull = bull_score >= bear_score
    confidence = bull_score if bull else bear_score
    checks = bull_checks if bull else bear_checks
    diff = abs(bull_score - bear_score)

    q = quality_gate(tv, bull, confidence)

    if q["confirmed"] and confidence >= 84 and diff >= 18:
        raw_signal = "BUY" if bull else "SELL"
        bias = "HIGH-CONFLUENCE BULLISH" if bull else "HIGH-CONFLUENCE BEARISH"
    elif q["prepare"] and confidence >= 70 and diff >= 12:
        raw_signal = "BUY WATCH" if bull else "SELL WATCH"
        bias = "SELECTIVE BULLISH WATCH" if bull else "SELECTIVE BEARISH WATCH"
    else:
        raw_signal = "NO TRADE"
        bias = "FILTERED OUT - CONDITIONS NOT STRONG ENOUGH"

    direction = "BUY" if bull else "SELL"
    setup_active = raw_signal in ("BUY", "SELL", "BUY WATCH", "SELL WATCH")

    trigger_level, trigger_count, trigger_confirmed, trigger_frozen = confirm_trigger(
        direction, tv["spot"], tv["atr5"], setup_active
    )

    option = None
    warning = ""

    if setup_active:
        try:
            option = choose_option(fetch_oc(), tv["spot"], bull, tv["atr5"], confidence)
        except Exception as e:
            warning = "Signal available, but option quote unavailable: " + str(e)

    execution_ready = bool(
        raw_signal in ("BUY", "SELL")
        and q["confirmed"]
        and trigger_confirmed
        and option
        and option.get("tradable")
    )

    if execution_ready:
        signal = direction
        bias = (
            ("BULLISH" if bull else "BEARISH")
            + " - FROZEN TRIGGER CONFIRMED 2/2 - OPTION READY"
        )
    elif setup_active:
        signal = "BUY WATCH" if bull else "SELL WATCH"
    else:
        signal = "NO TRADE"

    trigger_buffer = max(tv["atr5"] * .15, 5.0)

    if bull:
        buy_above = round(
            trigger_level if trigger_level is not None else tv["spot"] + trigger_buffer,
            2,
        )
        sell_below = round(tv["spot"] - trigger_buffer, 2)
    else:
        buy_above = round(tv["spot"] + trigger_buffer, 2)
        sell_below = round(
            trigger_level if trigger_level is not None else tv["spot"] - trigger_buffer,
            2,
        )

    result = {
        "version": VERSION,
        "spot": round(tv["spot"], 2),
        "signal": signal,
        "raw_signal": raw_signal,
        "direction": direction,
        "bias": bias,
        "confidence": confidence,

        "rating5": tv_rating(tv["rec5"]),
        "rating15": tv_rating(tv["rec15"]),
        "rating30": tv_rating(tv["rec30"]),

        "rsi5": tv["rsi5"],
        "rsi15": tv["rsi15"],
        "rsi30": tv["rsi30"],

        "ema9_5": tv["ema9_5"],
        "ema21_5": tv["ema21_5"],
        "ema50_5": tv["ema50_5"],

        "ema9_15": tv["ema9_15"],
        "ema21_15": tv["ema21_15"],
        "ema50_15": tv["ema50_15"],

        "ema9_30": tv["ema9_30"],
        "ema21_30": tv["ema21_30"],
        "ema50_30": tv["ema50_30"],

        "macd_5": tv["macd5"],
        "macd_signal_5": tv["macds5"],
        "macd_15": tv["macd15"],
        "macd_signal_15": tv["macds15"],
        "macd_30": tv["macd30"],
        "macd_signal_30": tv["macds30"],

        "adx5": tv["adx5"],
        "adx15": tv["adx15"],
        "adx30": tv["adx30"],

        "atr5": tv["atr5"],
        "vwap5": tv["vwap5"],

        "checks": checks,
        "quality_checks": [{"label": k, "ok": v} for k, v in q["checks"].items()],
        "quality_passed": q["passed"],
        "quality_total": len(q["checks"]),
        "quality_grade": (
            "A+"
            if q["confirmed"] and confidence >= 92
            else "A"
            if q["confirmed"]
            else "B"
            if q["prepare"]
            else "C"
            if confidence >= 60
            else "BLOCKED"
        ),
        "entry_state": q["state"],
        "entry_state_detail": q["detail"],
        "market_regime": q["regime"],
        "candle_confirmation": q["candle_ok"],
        "breakout_confirmation": q["breakout_ok"],
        "higher_tf_conflict": q["higher_tf_conflict"],
        "stretched": q["stretched"],

        "buy_above": buy_above,
        "sell_below": sell_below,

        "trigger_level": trigger_level,
        "trigger_confirmations": trigger_count,
        "trigger_confirmed": trigger_confirmed,
        "trigger_frozen": trigger_frozen,
        "trigger_started_at": _trigger_state.get("started_at"),

        "option": option,
        "execution_ready": execution_ready,

        "market_open": market_open_now(),
        "new_entry_window": new_entry_window_now(),
        "data_source": "TradingView + NSE",
        "warning": warning,
        "updated": now_ist().strftime("%d-%b-%Y %I:%M:%S %p"),
    }

    result["reason"] = (
        f"{confidence}/100 | 5m {result['rating5']} | 15m {result['rating15']} | "
        f"30m {result['rating30']} | ADX {tv['adx5']:.1f}/{tv['adx15']:.1f}/{tv['adx30']:.1f} | "
        f"Quality {q['passed']}/{len(q['checks'])} | {q['state']} | {q['regime']} | "
        f"Trigger {trigger_count}/2"
    )

    _signal_cache["signal"] = result
    _signal_cache["ts"] = now_ts

    return result


# ---------------------------------------------------------------------
# Locked-trade management
# ---------------------------------------------------------------------
def is_trade_closed(t):
    return bool(t) and str(t.get("state", "")).endswith("CLOSED")


def lock_trade_from_signal(d):
    if _state.get("trade"):
        return _state["trade"]

    if not (
        d.get("execution_ready")
        and d.get("trigger_confirmed")
        and d.get("option")
        and new_entry_window_now()
    ):
        return None

    o = d["option"]

    t = {
        "contract": o["contract"],
        "expiry": o["expiry"],
        "strike": float(o["strike"]),
        "type": o["type"],
        "direction": "BUY" if o["type"] == "CE" else "SELL",

        "entry": float(o["entry"]),
        "sl": float(o["sl"]),
        "original_sl": float(o["sl"]),
        "trailing_sl": float(o["sl"]),

        "t1": float(o["target1"]),
        "t2": float(o["target2"]),
        "original_t2": float(o["target2"]),
        "risk": float(o.get("risk") or max(float(o["entry"]) - float(o["sl"]), .01)),

        "current_ltp": float(o.get("ltp") or o["entry"]),

        "state": "ENTRY LOCKED / MONITORING",
        "locked_at": d["updated"],

        "target1_hit": False,
        "target_extended": False,
        "add_on_eligible": False,
        "last_health": None,
        "last_eod": None,
        "reversal_alerted": False,
    }

    with _state_lock:
        _state["trade"] = t
        save_state()

    add_journal(
        "ENTRY LOCKED",
        t,
        f"Entry Rs {t['entry']:.2f} | SL Rs {t['sl']:.2f} | "
        f"T1 Rs {t['t1']:.2f} | T2 Rs {t['t2']:.2f}",
    )

    send_whatsapp(
        "NIFTY V4.1 - ENTRY READY / LOCKED",
        (
            f"{t['contract']}\n"
            f"Entry Rs {t['entry']:.2f}\n"
            f"SL Rs {t['sl']:.2f}\n"
            f"T1 Rs {t['t1']:.2f}\n"
            f"T2 Rs {t['t2']:.2f}\n"
            f"NIFTY {d['spot']:.2f} | Confidence {d['confidence']}/100\n"
            f"5m/15m/30m: {d['rating5']} / {d['rating15']} / {d['rating30']}\n"
            f"Decision-support only. Verify in broker."
        ),
        dedupe_key=f"entry:{t['contract']}:{t['locked_at']}",
        cooldown=86400,
    )

    return t


def evaluate_reversal(t, d):
    if not t or is_trade_closed(t):
        return False

    locked_side = "BUY" if t["type"] == "CE" else "SELL"
    opposite = "SELL" if locked_side == "BUY" else "BUY"

    s5 = rating_side(d["rating5"])
    s15 = rating_side(d["rating15"])
    s30 = rating_side(d["rating30"])
    confidence = int(d.get("confidence") or 0)

    strong_conflict = (
        s5 == opposite
        and s15 == opposite
        and (s30 == opposite or confidence >= 88)
        and confidence >= 76
    )

    if strong_conflict and not t.get("reversal_alerted"):
        t["reversal_alerted"] = True
        save_state()

        send_whatsapp(
            "NIFTY V4.1 - REVERSAL / EXIT REVIEW",
            (
                f"{t['contract']} is locked {locked_side}.\n"
                f"Current 5m/15m/30m = {s5}/{s15}/{s30}\n"
                f"Confidence {confidence}/100.\n"
                f"Review position, trail and exit plan immediately. No automatic broker exit."
            ),
            dedupe_key=f"reversal:{t['contract']}:{t['locked_at']}",
            cooldown=86400,
        )

        add_journal(
            "REVERSAL WARNING",
            t,
            f"{s5}/{s15}/{s30} | confidence {confidence}/100",
        )
        return True

    return strong_conflict


def evaluate_trade_health(t, d):
    if not t or is_trade_closed(t):
        return {
            "label": "NO ACTIVE TRADE",
            "score": None,
            "action": "Waiting for an open locked trade.",
            "reasons": [],
            "pnl_pct": None,
        }

    bull = t["type"] == "CE"
    score = 0
    reasons = []

    cp = float(t.get("current_ltp") or 0)
    entry = float(t.get("entry") or 0)
    pnl = ((cp - entry) / entry * 100) if cp > 0 and entry > 0 else 0

    def aligned(r):
        return r in ("BUY", "STRONG BUY") if bull else r in ("SELL", "STRONG SELL")

    tests = [
        (aligned(d["rating5"]), 12, "5m direction against trade"),
        (aligned(d["rating15"]), 16, "15m direction against trade"),
        (aligned(d["rating30"]), 20, "30m direction against trade"),

        ((d["ema9_5"] > d["ema21_5"]) if bull else (d["ema9_5"] < d["ema21_5"]), 8, "5m EMA weakened"),
        ((d["ema9_15"] > d["ema21_15"]) if bull else (d["ema9_15"] < d["ema21_15"]), 10, "15m EMA weakened"),
        ((d["ema9_30"] > d["ema21_30"]) if bull else (d["ema9_30"] < d["ema21_30"]), 10, "30m EMA weakened"),

        ((d["spot"] > d["vwap5"]) if bull else (d["spot"] < d["vwap5"]), 8, "VWAP adverse"),
        ((d["macd_15"] > d["macd_signal_15"]) if bull else (d["macd_15"] < d["macd_signal_15"]), 6, "15m MACD adverse"),
        ((d["macd_30"] > d["macd_signal_30"]) if bull else (d["macd_30"] < d["macd_signal_30"]), 5, "30m MACD adverse"),
        (float(d["adx15"]) >= 18, 3, "15m ADX weak"),
        (float(d["adx30"]) >= 16, 2, "30m ADX weak"),
    ]

    for ok, pts, neg in tests:
        if ok:
            score += pts
        else:
            reasons.append(neg)

    if pnl <= -12:
        score -= 18
    elif pnl <= -7:
        score -= 9

    score = max(0, min(100, score))

    if score < 35 or pnl <= -15:
        label = "EXIT REVIEW"
        action = "High invalidation risk. Review broker position and protective stop immediately."
    elif score < 55:
        label = "WEAKENING"
        action = "Defensive mode. Do not add; protect risk."
    elif score < 75:
        label = "CAUTION"
        action = "Momentum is mixed. Monitor closely."
    else:
        label = "HEALTHY"
        action = "Trend structure remains aligned with the locked trade."

    if t.get("last_health") != label:
        previous = t.get("last_health")
        t["last_health"] = label
        save_state()

        if label in ("EXIT REVIEW", "WEAKENING"):
            send_whatsapp(
                f"NIFTY V4.1 - TRADE {label}",
                f"{t['contract']} | Health {score}/100\n{action}",
                dedupe_key=f"health:{t['contract']}:{t['locked_at']}:{label}",
                cooldown=1200,
            )
            add_journal("TRADE " + label, t, f"{score}/100 | {action}")

        # Clear previous reversal latch if health fully recovers.
        if label == "HEALTHY" and previous in ("WEAKENING", "EXIT REVIEW"):
            t["reversal_alerted"] = False
            save_state()

    return {
        "label": label,
        "score": score,
        "action": action,
        "reasons": reasons[:5],
        "pnl_pct": round(pnl, 2),
    }


def evaluate_eod(t, d):
    if not t or is_trade_closed(t):
        return {
            "label": "NO ACTIVE POSITION",
            "score": None,
            "action": "Waiting for an open locked trade.",
            "reasons": [],
            "window": "Review starts 3:05 PM IST.",
        }

    n = now_ist()
    mins = n.hour * 60 + n.minute
    review = mins >= 905  # 15:05
    final_window = mins >= 920  # 15:20

    bull = t["type"] == "CE"
    score = 0
    reasons = []

    def add(ok, pts, neg):
        nonlocal score
        if ok:
            score += pts
        else:
            reasons.append(neg)

    add(
        d["rating30"] in (("BUY", "STRONG BUY") if bull else ("SELL", "STRONG SELL")),
        22,
        "30m direction adverse",
    )
    add(
        (d["ema9_30"] > d["ema21_30"]) if bull else (d["ema9_30"] < d["ema21_30"]),
        18,
        "30m EMA trend adverse",
    )
    add(
        (d["spot"] > d["ema50_30"]) if bull else (d["spot"] < d["ema50_30"]),
        14,
        "price adverse to 30m EMA50",
    )
    add(
        (d["spot"] > d["vwap5"]) if bull else (d["spot"] < d["vwap5"]),
        10,
        "VWAP adverse",
    )
    add(
        (d["macd_30"] > d["macd_signal_30"]) if bull else (d["macd_30"] < d["macd_signal_30"]),
        14,
        "30m MACD adverse",
    )
    add(float(d["adx30"]) >= 16, 10, "30m trend weak")
    add(float(d["confidence"]) >= 80, 7, "overall confidence below 80")
    add(not d.get("higher_tf_conflict"), 5, "higher-timeframe conflict")

    days = expiry_days(t.get("expiry"))

    if days is not None and days <= 1:
        score -= 25
        reasons.append("expiry too close for comfortable overnight carry")
    elif days is not None and days <= 2:
        score -= 10
        reasons.append("near-expiry theta/gap risk")

    score = max(0, min(100, score))

    if not review:
        label = "REVIEW LATER"
        action = "Final carry/exit decision activates from 3:05 PM India time."
    elif (
        score >= 78
        and float(d["adx30"]) >= 16
        and not d.get("higher_tf_conflict")
        and not (days is not None and days <= 1)
    ):
        label = "CARRY FORWARD CANDIDATE"
        action = "Overnight conditions are comparatively strong. Re-check broker quote, gap risk and position size."
    elif score >= 58:
        label = "HOLD ONLY WITH CAUTION"
        action = "Mixed overnight quality. Consider reducing exposure unless your risk plan permits the carry."
    else:
        label = "EXIT BEFORE CLOSE"
        action = "Overnight carry quality is weak. Review closing before market close."

    if review and t.get("last_eod") != label:
        t["last_eod"] = label
        save_state()

        send_whatsapp(
            "NIFTY V4.1 - EOD " + label,
            f"{t['contract']} | Carry score {score}/100\n{action}",
            dedupe_key=f"eod:{t['contract']}:{now_ist().date()}:{label}",
            cooldown=86400,
        )
        add_journal("EOD " + label, t, f"Carry score {score}/100")

    return {
        "label": label,
        "score": score,
        "action": action,
        "reasons": reasons[:5],
        "window": (
            "FINAL EOD REVIEW WINDOW ACTIVE"
            if final_window
            else "EOD REVIEW ACTIVE"
            if review
            else "Review starts 3:05 PM IST."
        ),
    }


def apply_trade_price(t, p, d):
    if not t or p <= 0:
        return t

    t["current_ltp"] = float(p)

    entry = float(t["entry"])
    t1 = float(t["t1"])
    t2 = float(t["t2"])
    effective_sl = max(float(t.get("sl") or 0), float(t.get("trailing_sl") or 0))

    # 1. Protective stop / trail.
    if not is_trade_closed(t) and p <= effective_sl:
        t["state"] = "STOP LOSS / TRAIL HIT - CLOSED"
        v41_note_trade_result("STOP")
        save_state()

        send_whatsapp(
            "NIFTY V4.1 - STOP / TRAIL HIT",
            f"{t['contract']} | LTP Rs {p:.2f} | Effective SL Rs {effective_sl:.2f}\nReview/exit in broker immediately.",
            dedupe_key=f"stop:{t['contract']}:{t['locked_at']}",
            cooldown=86400,
        )
        add_journal("STOP / TRAIL HIT", t, f"LTP Rs {p:.2f} | SL Rs {effective_sl:.2f}")
        return t

    # 2. Final target.
    if not is_trade_closed(t) and p >= t2:
        t["state"] = "TARGET 2 HIT - CLOSED"
        v41_note_trade_result("TARGET")
        save_state()

        send_whatsapp(
            "NIFTY V4.1 - TARGET 2 HIT",
            f"{t['contract']} | LTP Rs {p:.2f} | Final target Rs {t2:.2f}\nConsider booking/exit per your plan.",
            dedupe_key=f"t2:{t['contract']}:{t['locked_at']}",
            cooldown=86400,
        )
        add_journal("TARGET 2 HIT", t, f"LTP Rs {p:.2f}")
        return t

    # 3. Target 1 -> break-even protection.
    if not t.get("target1_hit") and p >= t1:
        t["target1_hit"] = True
        t["state"] = "TARGET 1 HIT / TRAILING"
        t["trailing_sl"] = max(entry, effective_sl)
        save_state()

        send_whatsapp(
            "NIFTY V4.1 - TARGET 1 HIT",
            (
                f"{t['contract']} | LTP Rs {p:.2f}\n"
                f"T1 Rs {t1:.2f}\n"
                f"Reference trail raised to Rs {t['trailing_sl']:.2f}\n"
                f"T2 Rs {t['t2']:.2f}"
            ),
            dedupe_key=f"t1:{t['contract']}:{t['locked_at']}",
            cooldown=86400,
        )
        add_journal("TARGET 1 HIT", t, f"Trail Rs {t['trailing_sl']:.2f}")

    # 4. Second-stage profit trail.
    if t.get("target1_hit") and not is_trade_closed(t):
        stage2 = t1 + .60 * (float(t["t2"]) - t1)
        if p >= stage2 and float(t.get("trailing_sl") or 0) < t1:
            t["trailing_sl"] = t1
            t["state"] = "PROFIT TRAIL ACTIVE"
            save_state()

            send_whatsapp(
                "NIFTY V4.1 - TRAILING STOP UPDATED",
                f"{t['contract']} | LTP Rs {p:.2f}\nReference trail raised to T1 Rs {t1:.2f}.",
                dedupe_key=f"trail2:{t['contract']}:{t['locked_at']}",
                cooldown=86400,
            )
            add_journal("TRAIL UPDATED", t, f"Trail Rs {t1:.2f}")

    # 5. One-time target extension if the higher timeframe remains very strong.
    if t.get("target1_hit") and not t.get("target_extended") and not is_trade_closed(t):
        bull = t["type"] == "CE"
        direction_ok = (
            d["rating15"] in ("BUY", "STRONG BUY")
            and d["rating30"] in ("BUY", "STRONG BUY")
        ) if bull else (
            d["rating15"] in ("SELL", "STRONG SELL")
            and d["rating30"] in ("SELL", "STRONG SELL")
        )

        macd_ok = (
            d["macd_15"] > d["macd_signal_15"]
            and d["macd_30"] > d["macd_signal_30"]
        ) if bull else (
            d["macd_15"] < d["macd_signal_15"]
            and d["macd_30"] < d["macd_signal_30"]
        )

        if (
            direction_ok
            and macd_ok
            and float(d["adx15"]) >= 22
            and float(d["confidence"]) >= 90
            and p >= t1 + .35 * float(t["risk"])
        ):
            old_t2 = float(t["t2"])
            t["t2"] = round(old_t2 + .50 * float(t["risk"]), 2)
            t["target_extended"] = True
            t["state"] = "TARGET EXTENDED / TRAILING"
            t["trailing_sl"] = round(
                max(
                    float(t.get("trailing_sl") or 0),
                    entry + .25 * float(t["risk"]),
                ),
                2,
            )
            save_state()

            send_whatsapp(
                "NIFTY V4.1 - TARGET REVISED",
                (
                    f"{t['contract']} | LTP Rs {p:.2f}\n"
                    f"T2 revised Rs {old_t2:.2f} -> Rs {t['t2']:.2f}\n"
                    f"Reference trail Rs {t['trailing_sl']:.2f}\n"
                    f"Strong 15m/30m trend remains."
                ),
                dedupe_key=f"targetext:{t['contract']}:{t['locked_at']}",
                cooldown=86400,
            )
            add_journal("TARGET REVISED", t, f"{old_t2:.2f} -> {t['t2']:.2f}")

    # 6. Add-on ELIGIBILITY only. Never auto-add quantity.
    if t.get("target1_hit") and not t.get("add_on_eligible") and not is_trade_closed(t):
        locked_side = "BUY" if t["type"] == "CE" else "SELL"

        same = (
            rating_side(d["rating5"]) == locked_side
            and rating_side(d["rating15"]) == locked_side
            and rating_side(d["rating30"]) == locked_side
        )

        if same and int(d["confidence"]) >= 92 and d["market_regime"] == "TRENDING":
            t["add_on_eligible"] = True
            save_state()

            send_whatsapp(
                "NIFTY V4.1 - ADD-ON ELIGIBLE / REVIEW ONLY",
                (
                    f"{t['contract']} is already protected.\n"
                    f"5m/15m/30m remain aligned | Confidence {d['confidence']}/100.\n"
                    f"This is NOT an automatic add. Review total risk before increasing size."
                ),
                dedupe_key=f"addon:{t['contract']}:{t['locked_at']}",
                cooldown=86400,
            )
            add_journal("ADD-ON ELIGIBLE", t, f"Confidence {d['confidence']}/100")

    save_state()
    return t


# ---------------------------------------------------------------------
# Background monitoring
# ---------------------------------------------------------------------
def monitor_once():
    d = build_signal(force=True)

    with _state_lock:
        _state["last_signal"] = d
        _state["last_monitor_at"] = now_ist().isoformat()
        _state["last_monitor_error"] = ""

        t = _state.get("trade")

        if not t:
            t = lock_trade_from_signal(d)

        if t and not is_trade_closed(t):
            try:
                q = exact_option_quote(
                    t["strike"],
                    t["type"],
                    t.get("expiry") or "",
                )
                apply_trade_price(t, float(q.get("ltp") or 0), d)
            except Exception as e:
                _state["last_monitor_error"] = "Locked option: " + str(e)

            evaluate_reversal(t, d)
            health = evaluate_trade_health(t, d)
            eod = evaluate_eod(t, d)
        else:
            health = {
                "label": "NO ACTIVE TRADE",
                "score": None,
                "action": "Waiting for an open locked trade.",
                "reasons": [],
                "pnl_pct": None,
            }
            eod = {
                "label": "NO ACTIVE POSITION",
                "score": None,
                "action": "Waiting for an open locked trade.",
                "reasons": [],
                "window": "Review starts 3:05 PM IST.",
            }

        save_state()

    return {
        "ok": True,
        "signal": d,
        "trade": t,
        "health": health,
        "eod": eod,
    }


def background_loop():
    log.info("V4.1 background monitor started.")
    time.sleep(4)

    while True:
        try:
            monitor_once()
            sleep_for = MONITOR_INTERVAL_SECONDS if market_open_now() else max(120, MONITOR_INTERVAL_SECONDS)
        except Exception as e:
            log.warning("Background monitor issue: %s", e)

            with _state_lock:
                previous = _state.get("last_monitor_error")
                _state["last_monitor_error"] = str(e)
                _state["last_monitor_at"] = now_ist().isoformat()

            if previous != str(e):
                send_whatsapp(
                    "NIFTY V4.1 - DATA / MONITOR ISSUE",
                    str(e),
                    dedupe_key="monitor-error",
                    cooldown=1800,
                )

            sleep_for = max(30, MONITOR_INTERVAL_SECONDS)

        time.sleep(sleep_for)


def start_background_monitor():
    global _monitor_thread_started

    if not ENABLE_BACKGROUND_MONITOR:
        return

    with _state_lock:
        if _monitor_thread_started:
            return
        _monitor_thread_started = True

    t = threading.Thread(
        target=background_loop,
        daemon=True,
        name="nifty-v41-monitor",
    )
    t.start()


# ---------------------------------------------------------------------
# API
# ---------------------------------------------------------------------
@app.route("/health", methods=["GET"])
def health():
    start_background_monitor()
    return jsonify({
        "ok": True,
        "version": VERSION,
        "market_open": market_open_now(),
        "new_entry_window": new_entry_window_now(),
        "background_monitor": _monitor_thread_started,
        "whatsapp_configured": whatsapp_configured(),
        "last_monitor_at": _state.get("last_monitor_at"),
        "last_monitor_error": _state.get("last_monitor_error"),
    })


@app.route("/api/signal", methods=["GET"])
def api_signal():
    start_background_monitor()

    try:
        d = build_signal()
        return jsonify(d)
    except Exception as e:
        if _signal_cache["signal"] is not None:
            old = dict(_signal_cache["signal"])
            old["warning"] = "Using cached data: " + str(e)
            return jsonify(old)
        return jsonify({"error": str(e)}), 503


@app.route("/api/trade-state", methods=["GET"])
def api_trade_state():
    start_background_monitor()

    with _state_lock:
        t = _state.get("trade")
        d = _state.get("last_signal")

        if t and d:
            health_state = evaluate_trade_health(t, d)
            eod_state = evaluate_eod(t, d)
        else:
            health_state = {
                "label": "NO ACTIVE TRADE",
                "score": None,
                "action": "Waiting for a locked trade.",
                "reasons": [],
                "pnl_pct": None,
            }
            eod_state = {
                "label": "NO ACTIVE POSITION",
                "score": None,
                "action": "Waiting for a locked trade.",
                "reasons": [],
                "window": "Review starts 3:05 PM IST.",
            }

        j = _state.get("journal", [])

        return jsonify({
            "ok": True,
            "trade": t,
            "health": health_state,
            "eod": eod_state,
            "journal_count": len(j),
            "journal_last": j[0] if j else None,
            "journal": j[:20],
            "last_event": _state.get("last_event"),
            "last_monitor_at": _state.get("last_monitor_at"),
            "last_monitor_error": _state.get("last_monitor_error"),
            "whatsapp_configured": whatsapp_configured(),
            "whatsapp_last_ok": _state.get("whatsapp_last_ok"),
        })


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


@app.route("/api/test-whatsapp", methods=["GET"])
def api_test_whatsapp():
    if not whatsapp_configured():
        return jsonify({
            "ok": False,
            "error": (
                "WhatsApp is not configured. Check TWILIO_ACCOUNT_SID, "
                "TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_FROM and WHATSAPP_TO."
            ),
        }), 400

    ok = send_whatsapp(
        "NIFTY V4.1 - TEST ALERT",
        "Render + Twilio WhatsApp connection is working.",
        dedupe_key=f"test:{int(time.time())}",
        cooldown=0,
    )

    return jsonify({"ok": bool(ok)})


@app.route("/api/reset-trade", methods=["POST"])
def api_reset_trade():
    with _state_lock:
        old = _state.get("trade")
        _state["trade"] = None
        save_state()

    if old:
        add_journal("TRADE MONITOR RESET", old, "Manual dashboard reset")

    return jsonify({"ok": True})


# ---------------------------------------------------------------------
# Mobile dashboard
# ---------------------------------------------------------------------
PAGE = r"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#07111f">
<title>NIFTY Professional Signals V4.1</title>
<style>
:root{
  --card:#0f1c2e;--card2:#12233a;--text:#eef5ff;--muted:#9bb0c9;
  --green:#22c55e;--red:#ef4444;--amber:#f59e0b;--line:#223855;--blue:#0ea5e9
}
*{box-sizing:border-box}
body{margin:0;background:linear-gradient(180deg,#06101d,#0a1627);color:var(--text);font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;min-height:100vh}
.wrap{max-width:820px;margin:auto;padding:18px 20px 50px}
.title{font-size:29px;font-weight:900;margin:8px 0}
.sub,.small{color:var(--muted);font-size:14px;line-height:1.5}
.card{background:rgba(15,28,46,.98);border:1px solid var(--line);border-radius:24px;padding:22px;margin:16px 0}
.banner{background:#3a2b0b;border:1px solid #806018;color:#ffe2a0}
.status{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.dot{width:11px;height:11px;border-radius:50%;background:#64748b}.dot.on{background:#00d66b;box-shadow:0 0 12px #00d66b}.dot.warn{background:#f59e0b}
.price{font-size:54px;font-weight:900;margin:8px 0}.signal{font-size:36px;font-weight:900}
.buy{color:#00d66b}.sell{color:#ff6581}.watch{color:#f59e0b}.neutral{color:#d4deea}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px}.grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}
.kpi{background:var(--card2);border:1px solid var(--line);border-radius:18px;padding:15px}.kpi .t{color:var(--muted);font-size:12px}.kpi .v{font-size:23px;font-weight:850;margin-top:5px}
.contract{font-size:31px;font-weight:900;margin:6px 0}.pill{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:7px 10px;font-size:12px;margin:4px}.ok{border-color:#23673d;color:#8ef0ab}.bad{border-color:#73323a;color:#ff9fa9}
.progress{height:16px;background:#071421;border:1px solid #29415f;border-radius:999px;overflow:hidden;margin-top:8px}.bar{height:100%;width:0;background:linear-gradient(90deg,#0ea5e9,#22c55e)}
button{width:100%;border:0;border-radius:15px;padding:16px;font-weight:900;font-size:17px;background:var(--blue);color:#00101a;margin-top:10px}
.secondary{background:#1b3a58;color:#eef5ff}.danger{background:#5a1f2d;color:#ffe3e8}
.err{color:#ff9caf;white-space:pre-wrap;margin-top:8px}
.journal{font-size:12px;color:#b9c7d8;border-top:1px solid #223855;padding-top:7px;margin-top:7px}
@media(max-width:560px){.grid3{grid-template-columns:1fr 1fr}.title{font-size:27px}.price{font-size:48px}.signal{font-size:32px}}
</style>
</head>
<body>
<div class="wrap">

<div class="title">NIFTY Professional Signals V4.1</div>
<div class="sub">5m + 15m + 30m confirmation | professional entry gating | NSE option monitor | server-side background monitoring | WhatsApp alerts</div>

<div class="card banner">
Decision-support only. This application never places or closes broker orders. Public feeds can be delayed or unavailable. Verify every contract, premium, stop and target in your broker before acting.
</div>

<div class="card">
  <div class="status">
    <span id="dot" class="dot"></span>
    <span id="status">Starting...</span>
    <span id="marketStatus" style="margin-left:auto">--</span>
  </div>
  <div id="spot" class="price">--</div>
  <div id="signal" class="signal neutral">WAITING</div>
  <div id="bias" class="small">Loading market data...</div>
  <div id="updated" class="small">--</div>
  <div id="error" class="err"></div>
</div>

<div class="card">
  <div class="small">CURRENT MARKET VIEW</div>
  <div id="marketView" style="font-size:22px;font-weight:850;margin-top:6px">--</div>
  <div class="small" style="margin-top:8px">Weighted confidence</div>
  <div id="confidence" style="font-size:38px;font-weight:900">--</div>
  <div class="progress"><div id="confBar" class="bar"></div></div>
  <div id="reason" class="small" style="margin-top:8px">--</div>
</div>

<div class="card">
  <div class="small">V4.1 PROFESSIONAL ENTRY QUALITY ENGINE</div>
  <div id="qualityGrade" style="font-size:32px;font-weight:900;margin-top:5px">--</div>
  <div id="qualityState" style="font-size:24px;font-weight:850">WAIT</div>
  <div id="qualitySummary" class="small">--</div>
  <div id="regime" class="small" style="margin-top:7px">Market regime: --</div>
  <div id="qualityChecks" style="margin-top:10px"></div>
</div>

<div class="card">
  <div class="small">SELECTED OPTION / LOCKED CONTRACT</div>
  <div id="contract" class="contract">--</div>
  <div id="expiry" class="small">--</div>
  <div id="liquidity" class="small">--</div>

  <div class="grid3" style="margin-top:14px">
    <div class="kpi"><div class="t">OPTION LTP</div><div id="optionLtp" class="v">--</div></div>
    <div class="kpi"><div class="t">BID</div><div id="bid" class="v">--</div></div>
    <div class="kpi"><div class="t">ASK / ENTRY</div><div id="ask" class="v">--</div></div>
  </div>
</div>

<div class="grid2">
  <div class="kpi"><div class="t">ENTRY</div><div id="entry" class="v">--</div></div>
  <div class="kpi"><div class="t">STOP / TRAIL</div><div id="sl" class="v">--</div></div>
  <div class="kpi"><div class="t">TARGET 1</div><div id="t1" class="v">--</div></div>
  <div class="kpi"><div class="t">TARGET 2</div><div id="t2" class="v">--</div></div>
</div>

<div class="card">
  <div class="small">V4.1 SERVER TRADE MONITOR</div>
  <div id="tradeState" style="font-size:27px;font-weight:900;margin-top:5px">NO ACTIVE TRADE</div>
  <div id="tradeDetail" class="small">Waiting for a locked trade.</div>
  <div class="grid2" style="margin-top:12px">
    <div class="kpi"><div class="t">LIVE P/L</div><div id="pnl" class="v">--</div></div>
    <div class="kpi"><div class="t">CURRENT OPTION LTP</div><div id="currentLtp" class="v">--</div></div>
  </div>
</div>

<div class="card">
  <div class="small">V4.1 PROFESSIONAL TRADE HEALTH</div>
  <div id="health" style="font-size:29px;font-weight:900;margin-top:5px">NO ACTIVE TRADE</div>
  <div id="healthAction" style="font-size:18px;font-weight:800;margin-top:5px;color:#9bb0c9">Waiting for a locked trade.</div>
  <div id="healthReasons" class="small" style="margin-top:7px">--</div>
</div>

<div class="card">
  <div class="small">V4.1 END-OF-DAY POSITION DECISION</div>
  <div id="eod" style="font-size:29px;font-weight:900;margin-top:5px">NO ACTIVE POSITION</div>
  <div id="eodScore" class="small">Carry score -- / 100</div>
  <div id="eodAction" style="font-size:18px;font-weight:800;margin-top:5px;color:#9bb0c9">Waiting for a locked trade.</div>
  <div id="eodReasons" class="small" style="margin-top:7px">--</div>
  <div id="eodWindow" class="small" style="margin-top:7px">Review starts 3:05 PM IST.</div>
</div>

<div class="card">
  <div class="small">CURRENT SCANNER TRIGGER LEVELS</div>
  <div class="grid2" style="margin-top:12px">
    <div class="kpi"><div class="t">NIFTY BUY ABOVE</div><div id="buyAbove" class="v">--</div></div>
    <div class="kpi"><div class="t">NIFTY SELL BELOW</div><div id="sellBelow" class="v">--</div></div>
  </div>
  <div id="triggerDetail" class="small" style="margin-top:8px">Frozen trigger: --</div>
</div>

<div class="card">
  <div class="small">CURRENT MARKET INDICATORS</div>
  <div class="grid3" style="margin-top:12px">
    <div><div class="small">Rating 5m</div><b id="rating5">--</b></div>
    <div><div class="small">Rating 15m</div><b id="rating15">--</b></div>
    <div><div class="small">Rating 30m</div><b id="rating30">--</b></div>

    <div><div class="small">RSI 5m</div><b id="rsi5">--</b></div>
    <div><div class="small">RSI 15m</div><b id="rsi15">--</b></div>
    <div><div class="small">RSI 30m</div><b id="rsi30">--</b></div>

    <div><div class="small">ADX 5m</div><b id="adx5">--</b></div>
    <div><div class="small">ADX 15m</div><b id="adx15">--</b></div>
    <div><div class="small">ADX 30m</div><b id="adx30">--</b></div>
  </div>
  <div id="checks" style="margin-top:12px"></div>
</div>

<div class="card">
  <div class="small">V4.1 SESSION JOURNAL</div>
  <div class="grid2" style="margin-top:12px">
    <div class="kpi"><div class="t">EVENTS</div><div id="journalCount" class="v">0</div></div>
    <div class="kpi"><div class="t">LAST EVENT</div><div id="journalLast" class="v" style="font-size:15px">--</div></div>
  </div>
  <div id="journalList"></div>
</div>

<div class="card">
  <div class="small">BACKGROUND / WHATSAPP STATUS</div>
  <div id="monitorStatus" style="font-size:19px;font-weight:800;margin-top:5px">--</div>
  <div id="waStatus" class="small">WhatsApp: --</div>
  <button id="testWa" class="secondary">SEND WHATSAPP TEST</button>
  <button id="resetTrade" class="danger">RESET SERVER TRADE MONITOR</button>
</div>

<button id="refresh">REFRESH NOW</button>

</div>

<script>
"use strict";

const $=id=>document.getElementById(id);

function fmt(x){
  if(x===null||x===undefined||!Number.isFinite(Number(x)))return "--";
  return Number(x).toLocaleString("en-IN",{minimumFractionDigits:2,maximumFractionDigits:2});
}
function rupee(x){
  if(x===null||x===undefined||!Number.isFinite(Number(x)))return "--";
  return "\u20B9"+fmt(x);
}

let lastServerEventKey = "";

async function getJson(url,opts={}){
  const r=await fetch(url,{cache:"no-store",...opts});
  const j=await r.json();
  if(!r.ok||j.error)throw new Error(j.error||("HTTP "+r.status));
  return j;
}

function browserAlertForEvent(s){
  const e=s.last_event;
  if(!e)return;
  const key=(e.time||"")+"|"+(e.kind||"")+"|"+(e.detail||"");
  if(!lastServerEventKey){
    lastServerEventKey=key;
    return;
  }
  if(key===lastServerEventKey)return;
  lastServerEventKey=key;

  if(navigator.vibrate)navigator.vibrate([200,80,200]);

  if("Notification" in window && Notification.permission==="granted"){
    try{
      new Notification("NIFTY V4.1 - "+e.kind,{body:(e.contract||"")+" "+(e.detail||"")});
    }catch(err){}
  }
}

function renderSignal(d){
  $("dot").className="dot on";
  $("status").textContent="Connected - V4.1";
  $("marketStatus").textContent=d.market_open?"MARKET OPEN":"MARKET CLOSED";

  $("spot").textContent=fmt(d.spot);
  $("signal").textContent=d.signal;
  $("signal").className="signal "+(
    d.signal==="BUY"?"buy":
    d.signal==="SELL"?"sell":
    d.signal.includes("WATCH")?"watch":"neutral"
  );

  $("bias").textContent=d.bias;
  $("updated").textContent=`Updated ${d.updated} | ${d.data_source}`;
  $("error").textContent=d.warning||"";

  $("marketView").textContent=`${d.signal} | 5m ${d.rating5} | 15m ${d.rating15} | 30m ${d.rating30}`;
  $("confidence").textContent=d.confidence+" / 100";
  $("confBar").style.width=d.confidence+"%";
  $("reason").textContent=d.reason;

  const g=d.quality_grade||"--";
  $("qualityGrade").textContent=
    g==="A+"?"GRADE A+ - PREMIUM SETUP":
    g==="A"?"GRADE A - CONFIRMED":
    g==="B"?"GRADE B - PREPARE ONLY":
    g==="C"?"GRADE C - WAIT":"NO NEW ENTRY";

  $("qualityGrade").style.color=
    (g==="A+"||g==="A")?"#00d66b":
    g==="B"?"#f59e0b":
    g==="C"?"#cbd5e1":"#ff7690";

  $("qualityState").textContent=d.entry_state||"WAIT";
  $("qualitySummary").textContent=`New-entry filters passed ${d.quality_passed||0}/${d.quality_total||0}. ${d.entry_state_detail||""}`;
  $("regime").textContent=`Market regime: ${d.market_regime||"--"} | Fresh entry window: ${d.new_entry_window?"OPEN":"CLOSED"}`;

  $("qualityChecks").innerHTML=(d.quality_checks||[]).map(
    x=>`<span class="pill ${x.ok?"ok":"bad"}">${x.ok?"PASS":"FAIL"} - ${x.label}</span>`
  ).join("");

  $("buyAbove").textContent=fmt(d.buy_above);
  $("sellBelow").textContent=fmt(d.sell_below);
  $("triggerDetail").textContent=`Frozen trigger: ${d.trigger_level==null?"--":fmt(d.trigger_level)} | confirmation ${d.trigger_confirmations||0}/2 | started ${d.trigger_started_at||"--"}`;

  $("rating5").textContent=d.rating5;
  $("rating15").textContent=d.rating15;
  $("rating30").textContent=d.rating30;

  $("rsi5").textContent=fmt(d.rsi5);
  $("rsi15").textContent=fmt(d.rsi15);
  $("rsi30").textContent=fmt(d.rsi30);

  $("adx5").textContent=fmt(d.adx5);
  $("adx15").textContent=fmt(d.adx15);
  $("adx30").textContent=fmt(d.adx30);

  $("checks").innerHTML=(d.checks||[]).map(
    x=>`<span class="pill ${x.ok?"ok":"bad"}">${x.ok?"PASS":"FAIL"} - ${x.label}</span>`
  ).join("");

  if(d.option){
    $("contract").textContent=d.option.contract;
    $("contract").className="contract "+(d.option.type==="CE"?"buy":"sell");
    $("expiry").textContent=`Expiry ${d.option.expiry} | Strike ${d.option.strike} | ${d.option.type}`;
    $("liquidity").textContent=`Liquidity ${d.option.liquidity} | Volume ${d.option.volume} | OI ${d.option.oi} | Spread ${fmt(d.option.spread_pct)}% | Risk ${fmt(d.option.risk_pct)}%`;

    $("optionLtp").textContent=rupee(d.option.ltp);
    $("bid").textContent=rupee(d.option.bid);
    $("ask").textContent=rupee(d.option.ask);

    $("entry").textContent=rupee(d.option.entry);
    $("sl").textContent=rupee(d.option.sl);
    $("t1").textContent=rupee(d.option.target1);
    $("t2").textContent=rupee(d.option.target2);
  }
}

function renderState(s){
  browserAlertForEvent(s);

  const t=s.trade;

  if(t){
    $("contract").textContent=t.contract;
    $("contract").className="contract "+(t.type==="CE"?"buy":"sell");
    $("expiry").textContent=`LOCKED | Expiry ${t.expiry} | Strike ${t.strike} | ${t.type}`;

    $("entry").textContent=rupee(t.entry);
    $("sl").textContent=rupee(t.trailing_sl||t.sl);
    $("t1").textContent=rupee(t.t1);
    $("t2").textContent=rupee(t.t2);
    $("optionLtp").textContent=rupee(t.current_ltp);

    $("tradeState").textContent=t.state;
    const pnl=(Number(t.current_ltp)>0&&Number(t.entry)>0)?((Number(t.current_ltp)-Number(t.entry))/Number(t.entry)*100):null;
    $("pnl").textContent=pnl==null?"--":`${pnl>=0?"+":""}${pnl.toFixed(1)}%`;
    $("currentLtp").textContent=rupee(t.current_ltp);
    $("tradeDetail").textContent=`${t.contract} | Entry ${rupee(t.entry)} | Trail ${rupee(t.trailing_sl||t.sl)} | T1 ${rupee(t.t1)} | T2 ${rupee(t.t2)}`;
  }else{
    $("tradeState").textContent="NO ACTIVE TRADE";
    $("tradeDetail").textContent="Waiting for a fully confirmed entry.";
    $("pnl").textContent="--";
    $("currentLtp").textContent="--";
  }

  const h=s.health||{};
  $("health").textContent=h.score==null?(h.label||"NO ACTIVE TRADE"):`${h.label} | ${h.score}/100`;
  $("healthAction").textContent=h.action||"Waiting for a locked trade.";
  $("healthReasons").textContent=(h.reasons||[]).join(" | ")||"--";

  const e=s.eod||{};
  $("eod").textContent=e.label||"NO ACTIVE POSITION";
  $("eodScore").textContent=e.score==null?"Carry score -- / 100":`Carry score ${e.score} / 100`;
  $("eodAction").textContent=e.action||"Waiting for a locked trade.";
  $("eodReasons").textContent=(e.reasons||[]).join(" | ")||"--";
  $("eodWindow").textContent=e.window||"Review starts 3:05 PM IST.";

  $("journalCount").textContent=s.journal_count||0;
  $("journalLast").textContent=s.journal_last?`${s.journal_last.kind} | ${s.journal_last.time}`:"--";
  $("journalList").innerHTML=(s.journal||[]).slice(0,8).map(
    x=>`<div class="journal">${x.time} | ${x.kind} | ${x.contract} | ${x.detail}</div>`
  ).join("");

  $("monitorStatus").textContent=s.last_monitor_at?`Last server scan: ${s.last_monitor_at}`:"Server monitor starting...";
  $("waStatus").textContent=`WhatsApp configured: ${s.whatsapp_configured?"YES":"NO"} | last send OK: ${s.whatsapp_last_ok?"YES":"not yet"}${s.last_monitor_error?(" | "+s.last_monitor_error):""}`;
}

async function refresh(){
  $("dot").className="dot warn";
  $("status").textContent="Updating...";

  try{
    const d=await getJson("/api/signal");
    renderSignal(d);

    const s=await getJson("/api/trade-state");
    renderState(s);

    if(!d.warning&&!s.last_monitor_error)$("error").textContent="";
  }catch(e){
    $("dot").className="dot warn";
    $("status").textContent="DATA CONNECTION INTERRUPTED";
    $("error").textContent=e.message;
  }
}

$("refresh").onclick=refresh;

$("testWa").onclick=async()=>{
  $("testWa").textContent="SENDING...";
  try{
    const r=await getJson("/api/test-whatsapp");
    alert(r.ok?"WhatsApp test sent. Check WhatsApp.":"WhatsApp test failed.");
  }catch(e){
    alert("WhatsApp test failed: "+e.message);
  }
  $("testWa").textContent="SEND WHATSAPP TEST";
  refresh();
};

$("resetTrade").onclick=async()=>{
  if(!confirm("Reset the SERVER-SIDE locked trade monitor?"))return;
  try{
    await getJson("/api/reset-trade",{method:"POST"});
    await refresh();
  }catch(e){
    alert("Reset failed: "+e.message);
  }
};

if("Notification" in window && Notification.permission==="default"){
  // Browser notification is optional; WhatsApp/server monitoring does not depend on this.
  document.body.addEventListener("click",async()=>{
    try{await Notification.requestPermission()}catch(e){}
  },{once:true});
}

refresh();
setInterval(refresh,15000);
</script>
</body>
</html>
"""


@app.route("/", methods=["GET"])
def home():
    start_background_monitor()
    return PAGE, 200, {"Content-Type": "text/html; charset=utf-8"}


start_background_monitor()


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "10000")),
        debug=False,
        threaded=True,
    )
