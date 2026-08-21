# Professional Signals V3.7.2
# Decision-support only. This app does not place broker orders.
#
# V3.7.2 goals:
# 1) Keep V3.7.1's "NO CROSS-INSTRUMENT FALLBACK" safety rule.
# 2) Fetch MCX continuous futures directly from TradingView websocket history.
# 3) Keep NIFTY on NIFTY data only.
# 4) Disable signals whenever the selected instrument has no verified data.
#
# Recommended packages:
# flask
# pandas
# numpy
# requests
# websocket-client
# yfinance
#
# Run:
#   python app.py
#
# Render start command:
#   gunicorn app:app
#
# Optional environment variables:
#   PORT=10000
#   TV_USERNAME=            # optional
#   TV_PASSWORD=            # optional; anonymous TV usually works for public symbols
#   MCX_EOD_HOUR=           # optional integer, e.g. 23
#   MCX_EOD_MINUTE=         # optional integer, e.g. 10

import os
import json
import math
import time
import random
import string
import threading
import traceback
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests
from flask import Flask, jsonify, request, render_template_string

try:
    import websocket
except Exception:
    websocket = None

try:
    import yfinance as yf
except Exception:
    yf = None

VERSION = "3.7.2"
IST = ZoneInfo("Asia/Kolkata")

app = Flask(__name__)

# ---------------------------------------------------------------------
# Instrument configuration
# ---------------------------------------------------------------------

INSTRUMENTS = {
    "NIFTY_OPTIONS": {
        "market": "NIFTY OPTIONS",
        "label": "NIFTY OPTIONS",
        "kind": "nifty",
        "exchange": "NSE",
        "tv_symbol": "NIFTY",
        "yf_symbol": "^NSEI",
        "price_decimals": 2,
        "trigger_buffer_atr": 0.15,
    },
    "CRUDEOIL_MINI": {
        "market": "MCX COMMODITIES",
        "label": "CRUDEOIL MINI",
        "kind": "mcx",
        "exchange": "MCX",
        # Correct continuous symbol form tried by V3.7.2:
        "tv_symbol": "CRUDEOILM1!",
        "price_decimals": 1,
        "trigger_buffer_atr": 0.12,
    },
    "CRUDEOIL": {
        "market": "MCX COMMODITIES",
        "label": "CRUDEOIL",
        "kind": "mcx",
        "exchange": "MCX",
        "tv_symbol": "CRUDEOIL1!",
        "price_decimals": 1,
        "trigger_buffer_atr": 0.12,
    },
    "GOLD_MINI": {
        "market": "MCX COMMODITIES",
        "label": "GOLD MINI",
        "kind": "mcx",
        "exchange": "MCX",
        "tv_symbol": "GOLDM1!",
        "price_decimals": 1,
        "trigger_buffer_atr": 0.10,
    },
    "GOLD": {
        "market": "MCX COMMODITIES",
        "label": "GOLD",
        "kind": "mcx",
        "exchange": "MCX",
        "tv_symbol": "GOLD1!",
        "price_decimals": 1,
        "trigger_buffer_atr": 0.10,
    },
    "SILVER_MINI": {
        "market": "MCX COMMODITIES",
        "label": "SILVER MINI",
        "kind": "mcx",
        "exchange": "MCX",
        "tv_symbol": "SILVERM1!",
        "price_decimals": 1,
        "trigger_buffer_atr": 0.10,
    },
    "SILVER": {
        "market": "MCX COMMODITIES",
        "label": "SILVER",
        "kind": "mcx",
        "exchange": "MCX",
        "tv_symbol": "SILVER1!",
        "price_decimals": 1,
        "trigger_buffer_atr": 0.10,
    },
    "NATURALGAS": {
        "market": "MCX COMMODITIES",
        "label": "NATURAL GAS",
        "kind": "mcx",
        "exchange": "MCX",
        "tv_symbol": "NATURALGAS1!",
        "price_decimals": 2,
        "trigger_buffer_atr": 0.12,
    },
    "COPPER": {
        "market": "MCX COMMODITIES",
        "label": "COPPER",
        "kind": "mcx",
        "exchange": "MCX",
        "tv_symbol": "COPPER1!",
        "price_decimals": 2,
        "trigger_buffer_atr": 0.12,
    },
}

MARKETS = {
    "NIFTY OPTIONS": ["NIFTY_OPTIONS"],
    "MCX COMMODITIES": [
        "CRUDEOIL_MINI",
        "CRUDEOIL",
        "GOLD_MINI",
        "GOLD",
        "SILVER_MINI",
        "SILVER",
        "NATURALGAS",
        "COPPER",
    ],
}

# ---------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------

STATE_LOCK = threading.Lock()
STATE = {
    "selected": "NIFTY_OPTIONS",
    "data_ok": False,
    "last_error": "",
    "last_refresh": None,
    "last_good": None,
    "bars": None,
    "analysis": None,
    "trade": None,
    "journal": [],
    "notifications_enabled": False,
}

# ---------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------

def now_ist():
    return datetime.now(IST)

def iso_now():
    return now_ist().isoformat(timespec="seconds")

def safe_float(x, default=None):
    try:
        if x is None or (isinstance(x, float) and math.isnan(x)):
            return default
        return float(x)
    except Exception:
        return default

def fmt_num(x, decimals=2):
    if x is None:
        return "—"
    try:
        return f"{float(x):,.{decimals}f}"
    except Exception:
        return "—"

def random_id(prefix="qs"):
    return prefix + "_" + "".join(random.choice(string.ascii_lowercase) for _ in range(12))

def add_journal(event, detail=""):
    item = {
        "time": iso_now(),
        "event": str(event),
        "detail": str(detail or ""),
    }
    with STATE_LOCK:
        STATE["journal"].append(item)
        STATE["journal"] = STATE["journal"][-100:]

# ---------------------------------------------------------------------
# TradingView websocket history fetcher
# ---------------------------------------------------------------------

def _tv_frame(message: str) -> str:
    return f"~m~{len(message)}~m~{message}"

def _tv_send(ws, method, params):
    payload = json.dumps({"m": method, "p": params}, separators=(",", ":"))
    ws.send(_tv_frame(payload))

def _tv_parse_messages(raw: str):
    """Yield decoded TradingView protocol messages from one websocket read."""
    out = []
    if not raw:
        return out

    # respond to heartbeat at caller; still parse standard frames
    chunks = raw.split("~m~")
    i = 0
    while i < len(chunks):
        part = chunks[i]
        if not part:
            i += 1
            continue

        # protocol pattern after split often becomes: length, payload
        if part.isdigit() and i + 1 < len(chunks):
            payload = chunks[i + 1]
            if payload.startswith("~h~"):
                out.append(("heartbeat", payload))
            else:
                try:
                    out.append(("json", json.loads(payload)))
                except Exception:
                    pass
            i += 2
        else:
            if part.startswith("~h~"):
                out.append(("heartbeat", part))
            else:
                try:
                    out.append(("json", json.loads(part)))
                except Exception:
                    pass
            i += 1
    return out

def fetch_tradingview_bars(exchange, symbol, interval="5", bars=350, timeout=12):
    """
    Fetch OHLCV bars from TradingView websocket for EXACT selected symbol.
    No fallback to another instrument is performed here.
    """
    if websocket is None:
        raise RuntimeError("websocket-client is not installed")

    session = random_id("qs")
    chart_session = random_id("cs")
    ws = None

    try:
        ws = websocket.create_connection(
            "wss://data.tradingview.com/socket.io/websocket",
            timeout=timeout,
            origin="https://www.tradingview.com",
            header=["User-Agent: Mozilla/5.0"],
        )

        _tv_send(ws, "set_auth_token", ["unauthorized_user_token"])
        _tv_send(ws, "chart_create_session", [chart_session, ""])
        _tv_send(ws, "quote_create_session", [session])
        _tv_send(
            ws,
            "quote_set_fields",
            [
                session,
                "lp",
                "ch",
                "chp",
                "currency_code",
                "exchange",
                "description",
                "type",
                "volume",
            ],
        )

        full_symbol = f"{exchange}:{symbol}"
        _tv_send(ws, "quote_add_symbols", [session, full_symbol, {"flags": ["force_permission"]}])

        symbol_payload = {
            "symbol": full_symbol,
            "adjustment": "splits",
            "session": "regular",
        }
        resolve_arg = "=" + json.dumps(symbol_payload, separators=(",", ":"))
        _tv_send(ws, "resolve_symbol", [chart_session, "symbol_1", resolve_arg])
        _tv_send(
            ws,
            "create_series",
            [chart_session, "s1", "s1", "symbol_1", str(interval), int(bars)],
        )

        deadline = time.time() + timeout
        series_rows = {}

        while time.time() < deadline:
            raw = ws.recv()
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", "ignore")

            if "~h~" in raw:
                # echo heartbeat exactly
                try:
                    hb = raw[raw.index("~h~"):]
                    if "~m~" in hb:
                        hb = hb.split("~m~")[0]
                    ws.send(_tv_frame(hb))
                except Exception:
                    pass

            for typ, msg in _tv_parse_messages(raw):
                if typ != "json" or not isinstance(msg, dict):
                    continue

                method = msg.get("m")
                params = msg.get("p", [])

                if method == "critical_error":
                    raise RuntimeError(f"TradingView critical error: {params}")

                if method == "series_error":
                    raise RuntimeError(f"TradingView series error: {params}")

                if method == "timescale_update" and len(params) >= 2:
                    payload = params[1]
                    if not isinstance(payload, dict):
                        continue
                    s1 = payload.get("s1")
                    if not isinstance(s1, dict):
                        continue
                    node = s1.get("s")
                    if not isinstance(node, list):
                        continue

                    for item in node:
                        try:
                            vals = item.get("v") if isinstance(item, dict) else None
                            if not isinstance(vals, list) or len(vals) < 6:
                                continue
                            # TV series v usually: [index, timestamp, open, high, low, close, volume]
                            if len(vals) >= 7:
                                ts, op, hi, lo, cl, vol = vals[1:7]
                            else:
                                ts, op, hi, lo, cl = vals[0:5]
                                vol = vals[5] if len(vals) > 5 else 0
                            ts = int(float(ts))
                            series_rows[ts] = {
                                "Datetime": pd.to_datetime(ts, unit="s", utc=True),
                                "Open": float(op),
                                "High": float(hi),
                                "Low": float(lo),
                                "Close": float(cl),
                                "Volume": float(vol or 0),
                            }
                        except Exception:
                            continue

                if method == "series_completed":
                    if series_rows:
                        df = pd.DataFrame(list(series_rows.values()))
                        df = df.sort_values("Datetime").set_index("Datetime")
                        df = df[~df.index.duplicated(keep="last")]
                        if len(df) >= 60:
                            return df
                        raise RuntimeError(
                            f"TradingView returned only {len(df)} valid bars for {full_symbol}"
                        )

        if series_rows:
            df = pd.DataFrame(list(series_rows.values()))
            df = df.sort_values("Datetime").set_index("Datetime")
            if len(df) >= 60:
                return df

        raise RuntimeError(f"TradingView returned no usable bars for {full_symbol}")

    finally:
        try:
            if ws:
                ws.close()
        except Exception:
            pass

# ---------------------------------------------------------------------
# NIFTY fetcher
# ---------------------------------------------------------------------

def fetch_nifty_bars():
    if yf is None:
        raise RuntimeError("yfinance is not installed")
    df = yf.download(
        "^NSEI",
        period="5d",
        interval="5m",
        progress=False,
        auto_adjust=False,
        threads=False,
    )
    if df is None or len(df) < 60:
        raise RuntimeError("NIFTY 5m data unavailable")

    # yfinance may return a MultiIndex
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]

    keep = ["Open", "High", "Low", "Close", "Volume"]
    df = df[[c for c in keep if c in df.columns]].copy()
    for c in keep:
        if c not in df.columns:
            df[c] = 0.0

    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    if len(df) < 60:
        raise RuntimeError("NIFTY data incomplete")
    return df

def resample_15m(df):
    x = df.copy()
    if not isinstance(x.index, pd.DatetimeIndex):
        raise RuntimeError("Data index is not datetime")
    x = x.sort_index()
    out = x.resample("15min").agg(
        {
            "Open": "first",
            "High": "max",
            "Low": "min",
            "Close": "last",
            "Volume": "sum",
        }
    )
    return out.dropna(subset=["Open", "High", "Low", "Close"])

# ---------------------------------------------------------------------
# Indicators
# ---------------------------------------------------------------------

def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()

def rsi(s, n=14):
    d = s.diff()
    up = d.clip(lower=0)
    down = -d.clip(upper=0)
    au = up.ewm(alpha=1 / n, adjust=False).mean()
    ad = down.ewm(alpha=1 / n, adjust=False).mean()
    rs = au / ad.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def atr(df, n=14):
    prev = df["Close"].shift(1)
    tr = pd.concat(
        [
            df["High"] - df["Low"],
            (df["High"] - prev).abs(),
            (df["Low"] - prev).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()

def macd(s):
    m = ema(s, 12) - ema(s, 26)
    sig = ema(m, 9)
    hist = m - sig
    return m, sig, hist

def adx(df, n=14):
    high = df["High"]
    low = df["Low"]
    close = df["Close"]

    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index)

    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    atr_sm = tr.ewm(alpha=1 / n, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / n, adjust=False).mean() / atr_sm.replace(0, np.nan)
    minus_di = 100 * minus_dm.ewm(alpha=1 / n, adjust=False).mean() / atr_sm.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean(), plus_di, minus_di

def vwap(df):
    tp = (df["High"] + df["Low"] + df["Close"]) / 3.0
    vol = df["Volume"].replace(0, np.nan)
    # reset daily when timezone information exists
    try:
        local_dates = df.index.tz_convert(IST).date
    except Exception:
        local_dates = df.index.date

    out = pd.Series(index=df.index, dtype=float)
    temp = pd.DataFrame({"tp": tp, "vol": vol, "d": local_dates}, index=df.index)
    for _, g in temp.groupby("d"):
        pv = (g["tp"] * g["vol"]).cumsum()
        vv = g["vol"].cumsum()
        calc = pv / vv
        if calc.isna().all():
            calc = g["tp"].expanding().mean()
        out.loc[g.index] = calc
    return out

def enrich(df):
    x = df.copy()
    x["EMA10"] = ema(x["Close"], 10)
    x["EMA20"] = ema(x["Close"], 20)
    x["EMA50"] = ema(x["Close"], 50)
    x["RSI"] = rsi(x["Close"], 14)
    x["ATR"] = atr(x, 14)
    m, s, h = macd(x["Close"])
    x["MACD"] = m
    x["MACD_SIG"] = s
    x["MACD_HIST"] = h
    ax, pdi, mdi = adx(x, 14)
    x["ADX"] = ax
    x["+DI"] = pdi
    x["-DI"] = mdi
    x["VWAP"] = vwap(x)
    return x

def rating_for(row):
    close = row["Close"]
    score = 0
    score += 1 if row["EMA10"] > row["EMA20"] else -1
    score += 1 if close > row["EMA50"] else -1
    score += 1 if close > row["VWAP"] else -1
    score += 1 if row["MACD"] > row["MACD_SIG"] else -1
    score += 1 if row["+DI"] > row["-DI"] else -1
    rr = row["RSI"]
    if rr >= 55:
        score += 1
    elif rr <= 45:
        score -= 1

    if score >= 5:
        return "STRONG BUY"
    if score >= 2:
        return "BUY"
    if score <= -5:
        return "STRONG SELL"
    if score <= -2:
        return "SELL"
    return "NEUTRAL"

def direction_from_ratings(r5, r15):
    buys = {"BUY", "STRONG BUY"}
    sells = {"SELL", "STRONG SELL"}
    if r5 in buys and r15 in buys:
        return "BUY"
    if r5 in sells and r15 in sells:
        return "SELL"
    return "NONE"

def healthy_rsi(v, direction):
    if v is None:
        return False
    if direction == "BUY":
        return 48 <= v <= 68
    if direction == "SELL":
        return 32 <= v <= 52
    return 38 <= v <= 62

def analyze(df5, cfg):
    df5 = enrich(df5)
    df15 = enrich(resample_15m(df5))
    if len(df15) < 25:
        raise RuntimeError("Not enough 15-minute bars")

    a = df5.iloc[-1]
    b = df15.iloc[-1]
    prev5 = df5.iloc[-2]

    rating5 = rating_for(a)
    rating15 = rating_for(b)
    direction = direction_from_ratings(rating5, rating15)

    close = safe_float(a["Close"])
    atr5 = safe_float(a["ATR"])
    atr15 = safe_float(b["ATR"])
    if not close or not atr5:
        raise RuntimeError("Indicator calculation incomplete")

    recent_high = safe_float(df5["High"].iloc[-7:-1].max())
    recent_low = safe_float(df5["Low"].iloc[-7:-1].min())
    buf = atr5 * float(cfg.get("trigger_buffer_atr", 0.12))
    buy_above = max(recent_high, close) + buf
    sell_below = min(recent_low, close) - buf

    bullish_candle = a["Close"] > a["Open"] and a["Close"] > prev5["High"] * 0.999
    bearish_candle = a["Close"] < a["Open"] and a["Close"] < prev5["Low"] * 1.001

    if direction == "BUY":
        checks = {
            "direction_agree": True,
            "ema_aligned": bool(a["EMA10"] > a["EMA20"] and b["EMA10"] > b["EMA20"]),
            "price_ema50": bool(a["Close"] > a["EMA50"] and b["Close"] > b["EMA50"]),
            "vwap": bool(a["Close"] > a["VWAP"]),
            "macd_both": bool(a["MACD"] > a["MACD_SIG"] and b["MACD"] > b["MACD_SIG"]),
            "trend_strength": bool(a["ADX"] >= 18 and b["ADX"] >= 18),
            "rsi_healthy": bool(healthy_rsi(a["RSI"], "BUY") and healthy_rsi(b["RSI"], "BUY")),
            "candle_confirm": bool(bullish_candle),
            "breakout": bool(a["Close"] >= recent_high - 0.10 * atr5),
        }
    elif direction == "SELL":
        checks = {
            "direction_agree": True,
            "ema_aligned": bool(a["EMA10"] < a["EMA20"] and b["EMA10"] < b["EMA20"]),
            "price_ema50": bool(a["Close"] < a["EMA50"] and b["Close"] < b["EMA50"]),
            "vwap": bool(a["Close"] < a["VWAP"]),
            "macd_both": bool(a["MACD"] < a["MACD_SIG"] and b["MACD"] < b["MACD_SIG"]),
            "trend_strength": bool(a["ADX"] >= 18 and b["ADX"] >= 18),
            "rsi_healthy": bool(healthy_rsi(a["RSI"], "SELL") and healthy_rsi(b["RSI"], "SELL")),
            "candle_confirm": bool(bearish_candle),
            "breakout": bool(a["Close"] <= recent_low + 0.10 * atr5),
        }
    else:
        checks = {
            "direction_agree": False,
            "ema_aligned": False,
            "price_ema50": False,
            "vwap": False,
            "macd_both": False,
            "trend_strength": bool(a["ADX"] >= 18 and b["ADX"] >= 18),
            "rsi_healthy": bool(healthy_rsi(a["RSI"], "NONE") and healthy_rsi(b["RSI"], "NONE")),
            "candle_confirm": False,
            "breakout": False,
        }

    passed = sum(bool(v) for v in checks.values())

    # Grade / action
    if direction == "NONE":
        grade = "C"
        action = "WAIT"
    elif passed >= 8:
        grade = "A"
        action = "READY"
    elif passed >= 6:
        grade = "B"
        action = "PREPARE"
    else:
        grade = "C"
        action = "AVOID"

    # Confidence deliberately requires quality; raw rating cannot create a 90+ score by itself.
    base = passed / 9.0 * 75.0
    rating_bonus = 0
    if "STRONG" in rating5:
        rating_bonus += 10
    elif rating5 in ("BUY", "SELL"):
        rating_bonus += 6
    if "STRONG" in rating15:
        rating_bonus += 10
    elif rating15 in ("BUY", "SELL"):
        rating_bonus += 6
    if a["ADX"] >= 20 and b["ADX"] >= 20:
        rating_bonus += 5
    confidence = int(max(0, min(100, round(base + rating_bonus))))

    # Market regime
    if a["ADX"] < 16 and b["ADX"] < 18:
        regime = "CHOP / LOW TREND"
    elif a["ADX"] >= 22 and b["ADX"] >= 22:
        regime = "TRENDING"
    else:
        regime = "MIXED"

    # Trigger confirmation = structure + candle.
    if direction == "BUY":
        trigger_hits = int(a["Close"] >= buy_above) + int(bullish_candle)
    elif direction == "SELL":
        trigger_hits = int(a["Close"] <= sell_below) + int(bearish_candle)
    else:
        trigger_hits = 0

    return {
        "price": close,
        "rating5": rating5,
        "rating15": rating15,
        "direction": direction,
        "confidence": confidence,
        "quality_passed": passed,
        "grade": grade,
        "action": action,
        "regime": regime,
        "trigger_hits": trigger_hits,
        "buy_above": buy_above,
        "sell_below": sell_below,
        "checks": checks,
        "indicators": {
            "rsi5": safe_float(a["RSI"]),
            "rsi15": safe_float(b["RSI"]),
            "ema10_5": safe_float(a["EMA10"]),
            "ema20_5": safe_float(a["EMA20"]),
            "ema10_15": safe_float(b["EMA10"]),
            "ema20_15": safe_float(b["EMA20"]),
            "ema50_5": safe_float(a["EMA50"]),
            "ema50_15": safe_float(b["EMA50"]),
            "macd5": safe_float(a["MACD"]),
            "macd_sig5": safe_float(a["MACD_SIG"]),
            "macd15": safe_float(b["MACD"]),
            "macd_sig15": safe_float(b["MACD_SIG"]),
            "adx5": safe_float(a["ADX"]),
            "adx15": safe_float(b["ADX"]),
            "atr5": atr5,
            "atr15": atr15,
            "vwap5": safe_float(a["VWAP"]),
        },
        "latest_bar_time": str(df5.index[-1]),
    }

# ---------------------------------------------------------------------
# NIFTY option chain
# ---------------------------------------------------------------------

def get_nse_session():
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/124 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "application/json,text/plain,*/*",
            "Referer": "https://www.nseindia.com/option-chain",
        }
    )
    try:
        s.get("https://www.nseindia.com", timeout=5)
    except Exception:
        pass
    return s

def fetch_nifty_option_chain():
    s = get_nse_session()
    r = s.get(
        "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY",
        timeout=8,
    )
    if r.status_code != 200:
        raise RuntimeError(f"NSE option-chain HTTP {r.status_code}")
    data = r.json()
    records = data.get("records", {})
    rows = records.get("data", [])
    if not rows:
        raise RuntimeError("NSE option chain returned no rows")
    return data

def select_nifty_option(analysis):
    direction = analysis["direction"]
    if direction not in ("BUY", "SELL"):
        return None

    chain = fetch_nifty_option_chain()
    records = chain.get("records", {})
    underlying = safe_float(records.get("underlyingValue"), analysis["price"])
    expiries = records.get("expiryDates", [])
    if not expiries:
        return None
    expiry = expiries[0]
    strike = int(round(underlying / 50.0) * 50)
    side = "CE" if direction == "BUY" else "PE"

    best = None
    for row in records.get("data", []):
        if int(row.get("strikePrice", -1)) != strike:
            continue
        if str(row.get("expiryDate")) != expiry:
            continue
        leg = row.get(side)
        if leg:
            best = leg
            break

    if not best:
        return None

    ltp = safe_float(best.get("lastPrice"))
    bid = safe_float(best.get("bidprice"))
    ask = safe_float(best.get("askPrice"))
    if not ask or ask <= 0:
        ask = ltp
    if not ltp or ltp <= 0:
        ltp = ask
    if not bid or bid <= 0:
        bid = ltp

    if not ask or ask <= 0:
        return None

    risk_pct = 0.14
    entry = ask
    sl = entry * (1 - risk_pct)
    risk_amt = entry - sl
    t1 = entry + risk_amt * 1.50
    t2 = entry + risk_amt * 2.40
    spread = ((ask - bid) / ask * 100) if ask else None

    return {
        "name": f"NIFTY {strike} {side}",
        "expiry": expiry,
        "strike": strike,
        "side": side,
        "ltp": ltp,
        "bid": bid,
        "ask": ask,
        "entry": entry,
        "sl": sl,
        "t1": t1,
        "t2": t2,
        "volume": best.get("totalTradedVolume"),
        "oi": best.get("openInterest"),
        "bidqty": best.get("bidQty"),
        "askqty": best.get("askQty"),
        "spread_pct": spread,
        "risk_pct": risk_pct * 100,
        "rr1": 1.50,
        "rr2": 2.40,
    }

def build_mcx_contract(cfg, analysis):
    # MCX continuous-futures mode is a reference signal only.
    # Exact broker expiry must be verified by the user.
    price = analysis["price"]
    atr5 = analysis["indicators"]["atr5"]
    if not price or not atr5:
        return None

    direction = analysis["direction"]
    # Structure/ATR based levels for futures reference.
    if direction == "SELL":
        entry = min(price, analysis["sell_below"])
        sl = entry + max(atr5 * 1.35, entry * 0.0035)
        risk = sl - entry
        t1 = entry - risk * 1.5
        t2 = entry - risk * 2.4
    else:
        entry = max(price, analysis["buy_above"])
        sl = entry - max(atr5 * 1.35, entry * 0.0035)
        risk = entry - sl
        t1 = entry + risk * 1.5
        t2 = entry + risk * 2.4

    return {
        "name": cfg["label"],
        "expiry": "VERIFY ACTIVE MCX EXPIRY",
        "strike": None,
        "side": "FUT",
        "ltp": price,
        "bid": None,
        "ask": price,
        "entry": entry,
        "sl": sl,
        "t1": t1,
        "t2": t2,
        "volume": None,
        "oi": None,
        "bidqty": None,
        "askqty": None,
        "spread_pct": None,
        "risk_pct": abs(entry - sl) / entry * 100 if entry else None,
        "rr1": 1.50,
        "rr2": 2.40,
    }

# ---------------------------------------------------------------------
# Trade monitor / health / EOD
# ---------------------------------------------------------------------

def maybe_auto_lock(analysis, contract, cfg):
    if not analysis or not contract:
        return

    # Only A-grade READY + 2/2 trigger can auto-lock.
    if analysis["grade"] != "A" or analysis["action"] != "READY":
        return
    if analysis["trigger_hits"] < 2:
        return
    if analysis["direction"] not in ("BUY", "SELL"):
        return

    with STATE_LOCK:
        if STATE["trade"] is not None:
            return
        STATE["trade"] = {
            "instrument_key": STATE["selected"],
            "name": contract["name"],
            "direction": analysis["direction"],
            "entry": contract["entry"],
            "sl": contract["sl"],
            "t1": contract["t1"],
            "t2": contract["t2"],
            "locked_at": iso_now(),
            "status": "ACTIVE",
        }
    add_journal("ENTRY LOCKED", f'{contract["name"]} @ {contract["entry"]:.2f}')

def trade_dashboard(trade, current_price):
    if not trade or current_price is None:
        return None

    entry = trade["entry"]
    direction = trade.get("direction", "BUY")
    sl = trade["sl"]
    t1 = trade["t1"]
    t2 = trade["t2"]

    if direction == "SELL":
        pl = (entry - current_price) / entry * 100
        to_sl = (sl - current_price) / current_price * 100
        to_t1 = (current_price - t1) / current_price * 100
        to_t2 = (current_price - t2) / current_price * 100
    else:
        pl = (current_price - entry) / entry * 100
        to_sl = (current_price - sl) / current_price * 100
        to_t1 = (t1 - current_price) / current_price * 100
        to_t2 = (t2 - current_price) / current_price * 100

    risk = abs(entry - sl)
    reward = abs(t1 - entry)
    rr = reward / risk if risk else None

    return {
        "pl_pct": pl,
        "current": current_price,
        "to_sl_pct": max(0.0, to_sl),
        "to_t1_pct": max(0.0, to_t1),
        "to_t2_pct": max(0.0, to_t2),
        "rr": rr,
    }

def trade_health(trade, analysis, current_price):
    if not trade or not analysis or current_price is None:
        return None

    direction = trade.get("direction", "BUY")
    score = 100
    reasons = []

    dash = trade_dashboard(trade, current_price)
    if dash and dash["pl_pct"] < -4:
        score -= 15
        reasons.append("position materially below reference entry")
    if dash and dash["pl_pct"] < -8:
        score -= 15

    if analysis["direction"] not in (direction, "NONE"):
        score -= 25
        reasons.append("5m + 15m direction against locked position")
    elif analysis["direction"] == "NONE":
        score -= 10
        reasons.append("current market direction is neutral/mixed")

    c = analysis["checks"]
    if not c.get("ema_aligned"):
        score -= 15
        reasons.append("EMA alignment weakened")
    if not c.get("vwap"):
        score -= 15
        reasons.append("price crossed adverse side of VWAP")
    if not c.get("macd_both"):
        score -= 10
        reasons.append("MACD momentum not confirmed")
    if not c.get("trend_strength"):
        score -= 5
        reasons.append("trend strength weak")

    score = max(0, min(100, score))
    if score >= 70:
        label = "HEALTHY"
        action = "MONITOR"
    elif score >= 45:
        label = "CAUTION"
        action = "REVIEW"
    else:
        label = "EXIT REVIEW"
        action = "REVIEW BROKER POSITION / STOP"

    return {
        "score": score,
        "label": label,
        "action": action,
        "reasons": reasons[:5],
    }

def eod_decision(trade, analysis, current_price, cfg):
    if not trade or not analysis or current_price is None:
        return None

    now = now_ist()
    kind = cfg["kind"]

    if kind == "nifty":
        active = (now.hour > 15) or (now.hour == 15 and now.minute >= 5)
    else:
        # Avoid pretending one MCX closing time applies to all contracts.
        h = os.getenv("MCX_EOD_HOUR")
        m = os.getenv("MCX_EOD_MINUTE")
        if h is None:
            return {
                "active": False,
                "score": None,
                "decision": "MCX EOD TIME NOT CONFIGURED",
                "detail": "Set MCX_EOD_HOUR / MCX_EOD_MINUTE only after verifying the selected contract/session with your broker.",
            }
        try:
            h = int(h)
            m = int(m or 0)
            active = (now.hour > h) or (now.hour == h and now.minute >= m)
        except Exception:
            active = False

    if not active:
        return {
            "active": False,
            "score": None,
            "decision": "WAITING FOR EOD REVIEW WINDOW",
            "detail": "Carry/exit review activates near the configured market close.",
        }

    health = trade_health(trade, analysis, current_price)
    score = 50
    reasons = []

    if health:
        score = int(health["score"])
        reasons.extend(health["reasons"])

    dash = trade_dashboard(trade, current_price)
    if dash and dash["pl_pct"] > 3:
        score += 10
    elif dash and dash["pl_pct"] < -6:
        score -= 10

    if analysis["regime"] == "TRENDING" and analysis["direction"] == trade["direction"]:
        score += 10
    if analysis["direction"] not in (trade["direction"], "NONE"):
        score -= 15

    score = max(0, min(100, score))

    if score >= 72:
        decision = "HOLD ONLY WITH CAUTION"
        detail = "Trend/health still supports the position, but overnight gap risk remains."
    elif score >= 55:
        decision = "REDUCE / REVIEW BEFORE CLOSE"
        detail = "Carry quality is mixed. Review broker position, margin, expiry and event risk."
    else:
        decision = "EXIT BEFORE CLOSE"
        detail = "Overnight carry quality is weak. Review the broker position and consider closing before market close."

    return {
        "active": True,
        "score": score,
        "decision": decision,
        "detail": detail,
        "reasons": reasons[:5],
    }

# ---------------------------------------------------------------------
# Refresh engine
# ---------------------------------------------------------------------

def fetch_selected_bars(selected):
    cfg = INSTRUMENTS[selected]
    if cfg["kind"] == "nifty":
        return fetch_nifty_bars()

    # V3.7.2: MCX exact-symbol only.
    # First use the configured canonical continuous symbol.
    # Then, ONLY for the same instrument, try a small set of spelling variants.
    variants = [cfg["tv_symbol"]]

    # Same-instrument aliases only. No NIFTY/global commodity fallback.
    alias_map = {
        "CRUDEOIL_MINI": ["CRUDEOILM1!", "CRUDEOILM"],
        "CRUDEOIL": ["CRUDEOIL1!", "CRUDEOIL"],
        "GOLD_MINI": ["GOLDM1!", "GOLDM"],
        "GOLD": ["GOLD1!", "GOLD"],
        "SILVER_MINI": ["SILVERM1!", "SILVERM"],
        "SILVER": ["SILVER1!", "SILVER"],
        "NATURALGAS": ["NATURALGAS1!", "NATURALGAS"],
        "COPPER": ["COPPER1!", "COPPER"],
    }
    variants = alias_map.get(selected, variants)

    errors = []
    for sym in variants:
        try:
            df = fetch_tradingview_bars(cfg["exchange"], sym, interval="5", bars=350)
            if df is not None and len(df) >= 60:
                return df
        except Exception as e:
            errors.append(f"{cfg['exchange']}:{sym} -> {e}")

    raise RuntimeError(
        f"{cfg['label']} DATA UNAVAILABLE. No valid same-instrument TradingView source. "
        + " | ".join(errors[-3:])
    )

def refresh_once():
    with STATE_LOCK:
        selected = STATE["selected"]
    cfg = INSTRUMENTS[selected]

    try:
        bars = fetch_selected_bars(selected)
        analysis = analyze(bars, cfg)

        if cfg["kind"] == "nifty":
            try:
                contract = select_nifty_option(analysis)
            except Exception as e:
                contract = None
                # NIFTY underlying signal remains valid even if option chain is unavailable.
                option_error = str(e)
            else:
                option_error = ""
        else:
            contract = build_mcx_contract(cfg, analysis)
            option_error = ""

        with STATE_LOCK:
            STATE["bars"] = bars
            STATE["analysis"] = analysis
            STATE["data_ok"] = True
            STATE["last_error"] = option_error
            STATE["last_refresh"] = iso_now()
            STATE["last_good"] = iso_now()

        maybe_auto_lock(analysis, contract, cfg)

        return True

    except Exception as e:
        with STATE_LOCK:
            # Keep last_good timestamp for display, but DO NOT reuse old analysis/price
            # for the currently selected instrument after a failed refresh.
            STATE["bars"] = None
            STATE["analysis"] = None
            STATE["data_ok"] = False
            STATE["last_error"] = str(e)
            STATE["last_refresh"] = iso_now()
        return False

def refresh_loop():
    while True:
        try:
            refresh_once()
        except Exception:
            traceback.print_exc()
        time.sleep(10)

# ---------------------------------------------------------------------
# View model
# ---------------------------------------------------------------------

def build_view():
    with STATE_LOCK:
        selected = STATE["selected"]
        data_ok = STATE["data_ok"]
        error = STATE["last_error"]
        last_refresh = STATE["last_refresh"]
        last_good = STATE["last_good"]
        analysis = dict(STATE["analysis"]) if STATE["analysis"] else None
        trade = dict(STATE["trade"]) if STATE["trade"] else None
        journal = list(STATE["journal"])

    cfg = INSTRUMENTS[selected]

    contract = None
    if data_ok and analysis:
        try:
            if cfg["kind"] == "nifty":
                contract = select_nifty_option(analysis)
            else:
                contract = build_mcx_contract(cfg, analysis)
        except Exception:
            contract = None

    current_price = None
    if contract and contract.get("ltp"):
        current_price = contract["ltp"]
    elif analysis:
        current_price = analysis.get("price")

    dash = trade_dashboard(trade, current_price) if trade else None
    health = trade_health(trade, analysis, current_price) if trade and analysis else None
    eod = eod_decision(trade, analysis, current_price, cfg) if trade and analysis else None

    return {
        "version": VERSION,
        "selected": selected,
        "cfg": cfg,
        "markets": MARKETS,
        "instruments": INSTRUMENTS,
        "data_ok": data_ok,
        "error": error,
        "last_refresh": last_refresh,
        "last_good": last_good,
        "analysis": analysis,
        "contract": contract,
        "trade": trade,
        "dashboard": dash,
        "health": health,
        "eod": eod,
        "journal": journal,
        "now": iso_now(),
    }

# ---------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------

HTML = r"""
<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Professional Signals V{{ version }}</title>
<style>
:root{
  --bg:#061827; --card:#0d253b; --card2:#102b46; --line:#1f4666;
  --txt:#f1f5f9; --muted:#9fb4ca; --green:#00d66b; --red:#ff668a;
  --orange:#ff9800; --cyan:#05b8f0; --gold:#d78d00;
}
*{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--txt);
font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif}
.wrap{max-width:780px;margin:auto;padding:22px}
h1{font-size:34px;margin:18px 0 4px}.sub{color:var(--muted);font-size:18px}
.card{background:var(--card);border:1px solid var(--line);border-radius:28px;padding:25px;margin:18px 0}
.warn{background:#493306;border-color:#926b0d;color:#ffe4a5}
.bad{border-color:#805c12}.title{color:var(--muted);font-size:17px;margin-bottom:7px}
.big{font-size:48px;font-weight:800}.signal{font-size:40px;font-weight:800;margin:4px 0}
.green{color:var(--green)}.red{color:var(--red)}.orange{color:var(--orange)}
.muted{color:var(--muted)}.err{color:#ff9ab0}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.box{background:var(--card2);border:1px solid var(--line);border-radius:22px;padding:20px}
.box .v{font-size:29px;font-weight:800}
select,button{width:100%;font-size:18px;font-weight:700;padding:18px;border-radius:18px;border:1px solid var(--line);background:var(--card2);color:var(--txt)}
button{cursor:pointer}.btn{background:#173b5c}.reset{background:#5a1e2c}.refresh{background:var(--cyan);color:#00131e}
.pills{display:flex;flex-wrap:wrap;gap:10px;margin-top:16px}.pill{padding:9px 13px;border-radius:999px;border:1px solid}
.ok{color:#6df0a8;border-color:#0c7644}.no{color:#ff8ca3;border-color:#8b3044}
.progress{height:20px;background:#071a29;border:1px solid var(--line);border-radius:20px;overflow:hidden}
.progress>div{height:100%;background:linear-gradient(90deg,#08b7ef,#00d66b)}
.small{font-size:16px;line-height:1.5}.center{text-align:center}
@media(max-width:520px){.wrap{padding:16px}.card{padding:20px;border-radius:24px}h1{font-size:29px}.big{font-size:42px}.signal{font-size:34px}.box .v{font-size:25px}}
</style>
</head>
<body>
<div class="wrap">
  <h1>📈 Professional Signals V{{ version }}</h1>
  <div class="sub">NIFTY options + MCX futures • 5m + 15m confirmation • quality filters • trade alerts</div>

  <div class="card warn small">
    Decision-support only. Public feeds may be delayed. Verify exact active contract,
    expiry, price and order in your broker before any real trade.
  </div>

  <div class="card">
    <div class="title">V{{version}} MARKET / INSTRUMENT</div>
    <div class="grid">
      <select id="market" onchange="marketChanged()">
        {% for m, keys in markets.items() %}
        <option value="{{m}}" {% if cfg.market==m %}selected{% endif %}>{{m}}</option>
        {% endfor %}
      </select>
      <select id="instrument" onchange="instrumentChanged()">
        {% for key,c in instruments.items() %}
          {% if c.market==cfg.market %}
          <option value="{{key}}" {% if key==selected %}selected{% endif %}>{{c.label}}</option>
          {% endif %}
        {% endfor %}
      </select>
    </div>
    <div class="muted small" style="margin-top:12px">
      {% if cfg.kind=="mcx" %}
      MCX mode fetches the selected TradingView MCX instrument only. No NIFTY or other-commodity fallback is allowed.
      {% else %}
      NIFTY mode uses NIFTY underlying data and NSE option-chain quotes.
      {% endif %}
    </div>
  </div>

  {% if not data_ok %}
  <div class="card bad">
    <div class="orange">● Reconnecting • V{{version}}</div>
    <div class="big">—</div>
    <div class="signal">DATA UNAVAILABLE</div>
    <div class="muted">{{cfg.label}} • Live source unavailable. No cross-instrument fallback.</div>
    <div class="err small" style="margin-top:14px">{{error}}</div>
  </div>
  <div class="card bad">
    <div class="signal" style="font-size:28px">DATA CONNECTION INTERRUPTED</div>
    <div class="muted">Signals are disabled until valid data for {{cfg.label}} is received.</div>
  </div>
  {% else %}
  <div class="card">
    <div class="green">● Connected • V{{version}}</div>
    <div class="big">{{ "%.2f"|format(analysis.price) }}</div>
    {% set d=analysis.direction %}
    <div class="signal {% if d=='BUY' %}green{% elif d=='SELL' %}red{% else %}orange{% endif %}">
      {% if d=='BUY' %}BUY WATCH{% elif d=='SELL' %}SELL WATCH{% else %}NO TRADE{% endif %}
    </div>
    <div class="muted">Updated {{last_refresh}}</div>
  </div>

  <div class="card">
    <div class="title">Current market view</div>
    <div class="signal" style="font-size:28px">
      {% if analysis.direction=="NONE" %}NO TRADE{% else %}{{analysis.direction}} WATCH{% endif %}
      • 5m {{analysis.rating5}} • 15m {{analysis.rating15}}
    </div>
    <div class="title">Signal confidence</div>
    <div class="big">{{analysis.confidence}} / 100</div>
    <div class="progress"><div style="width:{{analysis.confidence}}%"></div></div>
    <div class="muted small">
      Quality {{analysis.quality_passed}}/9 • Grade {{analysis.grade}} • {{analysis.action}}
      • {{analysis.regime}} • Trigger {{analysis.trigger_hits}}/2
    </div>
  </div>

  <div class="card">
    <div class="title">V{{version}} PROFESSIONAL ENTRY QUALITY ENGINE</div>
    <div class="signal {% if analysis.grade=='A' %}green{% elif analysis.grade=='B' %}orange{% else %}red{% endif %}">
      GRADE {{analysis.grade}} • {{analysis.action}}
    </div>
    <div class="muted small">New entries require direction, trend, structure and momentum confirmation.</div>
    <div class="pills">
      {% for k,v in analysis.checks.items() %}
      <span class="pill {% if v %}ok{% else %}no{% endif %}">
        {% if v %}✓{% else %}✕{% endif %} {{k.replace('_',' ').upper()}}
      </span>
      {% endfor %}
    </div>
  </div>

  {% if contract %}
  <div class="card">
    <div class="title">{% if cfg.kind=="nifty" %}Automatically selected contract{% else %}Selected futures reference{% endif %}</div>
    <div class="signal green">{{contract.name}}</div>
    <div class="muted">{{contract.expiry}} • {{contract.side}}</div>
    <div class="grid" style="margin-top:16px">
      <div class="box"><div class="title">LTP</div><div class="v">{{fmt(contract.ltp)}}</div></div>
      <div class="box"><div class="title">BID</div><div class="v">{{fmt(contract.bid)}}</div></div>
      <div class="box"><div class="title">ASK / ENTRY</div><div class="v">{{fmt(contract.ask)}}</div></div>
      <div class="box"><div class="title">RISK</div><div class="v">{{fmt(contract.risk_pct)}}%</div></div>
    </div>
  </div>

  <div class="grid">
    <div class="box"><div class="title">ENTRY</div><div class="v">{{fmt(contract.entry)}}</div></div>
    <div class="box"><div class="title">STOP LOSS</div><div class="v">{{fmt(contract.sl)}}</div></div>
    <div class="box"><div class="title">TARGET 1</div><div class="v">{{fmt(contract.t1)}}</div></div>
    <div class="box"><div class="title">TARGET 2 / EXIT</div><div class="v">{{fmt(contract.t2)}}</div></div>
  </div>
  {% endif %}

  {% if health %}
  <div class="card">
    <div class="title">V{{version}} PROFESSIONAL TRADE HEALTH</div>
    <div class="signal {% if health.score>=70 %}green{% elif health.score>=45 %}orange{% else %}red{% endif %}">
      {{health.label}} • {{health.score}}/100
    </div>
    <div class="muted"><b>{{health.action}}</b></div>
    <div class="small muted">{{health.reasons|join(' • ')}}</div>
  </div>
  {% endif %}

  {% if eod %}
  <div class="card">
    <div class="title">V{{version}} END-OF-DAY POSITION DECISION</div>
    <div class="signal {% if eod.score is not none and eod.score<55 %}red{% elif eod.score is not none %}orange{% endif %}">
      {{eod.decision}}
    </div>
    {% if eod.score is not none %}<div class="muted">Carry score {{eod.score}} / 100</div>{% endif %}
    <div class="muted small">{{eod.detail}}</div>
  </div>
  {% endif %}

  <div class="card">
    <div class="title">Trade monitor</div>
    {% if trade %}
      <div class="signal green" style="font-size:28px">ENTRY LOCKED / MONITORING</div>
      <div class="muted">{{trade.name}} • Locked Entry {{fmt(trade.entry)}} • SL {{fmt(trade.sl)}} • T1 {{fmt(trade.t1)}} • T2 {{fmt(trade.t2)}}</div>
    {% else %}
      <div class="signal" style="font-size:28px">NO ACTIVE TRADE</div>
      <div class="muted">Only Grade A + READY + Trigger 2/2 can auto-lock a reference trade.</div>
    {% endif %}
    <button class="btn" style="margin-top:16px" onclick="enableNotifications()">NOTIFICATIONS / VIBRATION</button>
    <button class="reset" style="margin-top:12px" onclick="resetTrade()">RESET TRADE MONITOR</button>
  </div>

  <div class="card">
    <div class="title">Current market indicators</div>
    <div class="grid">
      <div>
        <div class="title">Rating 5m</div><div class="v">{{analysis.rating5}}</div>
        <div class="title">RSI 5m</div><div class="v">{{fmt(analysis.indicators.rsi5)}}</div>
        <div class="title">EMA 10/20 5m</div><div class="v">{{fmt(analysis.indicators.ema10_5)}} / {{fmt(analysis.indicators.ema20_5)}}</div>
        <div class="title">MACD 5m</div><div class="v">{{fmt(analysis.indicators.macd5)}} / {{fmt(analysis.indicators.macd_sig5)}}</div>
        <div class="title">ATR 5m</div><div class="v">{{fmt(analysis.indicators.atr5)}}</div>
        <div class="title">VWAP 5m</div><div class="v">{{fmt(analysis.indicators.vwap5)}}</div>
        <div class="title">EMA 50 5m</div><div class="v">{{fmt(analysis.indicators.ema50_5)}}</div>
      </div>
      <div>
        <div class="title">Rating 15m</div><div class="v">{{analysis.rating15}}</div>
        <div class="title">RSI 15m</div><div class="v">{{fmt(analysis.indicators.rsi15)}}</div>
        <div class="title">EMA 10/20 15m</div><div class="v">{{fmt(analysis.indicators.ema10_15)}} / {{fmt(analysis.indicators.ema20_15)}}</div>
        <div class="title">MACD 15m</div><div class="v">{{fmt(analysis.indicators.macd15)}} / {{fmt(analysis.indicators.macd_sig15)}}</div>
        <div class="title">ADX 5m</div><div class="v">{{fmt(analysis.indicators.adx5)}}</div>
        <div class="title">ADX 15m</div><div class="v">{{fmt(analysis.indicators.adx15)}}</div>
        <div class="title">EMA 50 15m</div><div class="v">{{fmt(analysis.indicators.ema50_15)}}</div>
      </div>
    </div>
  </div>

  <div class="card">
    <div class="title">Current scanner trigger levels</div>
    <div class="grid">
      <div class="box"><div class="title">BUY ABOVE</div><div class="v">{{fmt(analysis.buy_above)}}</div></div>
      <div class="box"><div class="title">SELL BELOW</div><div class="v">{{fmt(analysis.sell_below)}}</div></div>
    </div>
  </div>
  {% endif %}

  <button class="refresh" onclick="manualRefresh()">REFRESH NOW</button>
</div>

<script>
const MARKET_MAP = {{ markets|tojson }};
const INSTRUMENTS = {{ instruments|tojson }};

function marketChanged(){
  const m=document.getElementById('market').value;
  const sel=document.getElementById('instrument');
  sel.innerHTML='';
  (MARKET_MAP[m]||[]).forEach(k=>{
    const o=document.createElement('option');
    o.value=k; o.textContent=INSTRUMENTS[k].label; sel.appendChild(o);
  });
  instrumentChanged();
}
async function instrumentChanged(){
  const key=document.getElementById('instrument').value;
  await fetch('/api/select',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({instrument:key})});
  location.reload();
}
async function manualRefresh(){
  const b=document.querySelector('.refresh'); b.textContent='UPDATING...';
  await fetch('/api/refresh',{method:'POST'});
  setTimeout(()=>location.reload(),900);
}
async function resetTrade(){
  if(!confirm('Reset locked trade monitor?')) return;
  await fetch('/api/reset',{method:'POST'});
  location.reload();
}
async function enableNotifications(){
  if(!('Notification' in window)){ alert('Browser notifications not supported.'); return; }
  const p=await Notification.requestPermission();
  if(p==='granted'){
    try{navigator.vibrate([150,80,150])}catch(e){}
    new Notification('Professional Signals V{{version}}',{body:'Notifications enabled.'});
  }
}
setTimeout(()=>location.reload(),15000);
</script>
</body>
</html>
"""

app.jinja_env.globals["fmt"] = lambda x: "—" if x is None else f"{float(x):,.2f}"

# ---------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------

@app.route("/")
def index():
    view = build_view()
    return render_template_string(HTML, **view)

@app.route("/health")
def health_route():
    return jsonify({"ok": True, "version": VERSION, "time": iso_now()})

@app.route("/api/state")
def api_state():
    v = build_view()
    # Strip dataframes / large internal objects from JSON view.
    return jsonify({
        "version": v["version"],
        "selected": v["selected"],
        "data_ok": v["data_ok"],
        "error": v["error"],
        "last_refresh": v["last_refresh"],
        "analysis": v["analysis"],
        "contract": v["contract"],
        "trade": v["trade"],
        "dashboard": v["dashboard"],
        "health": v["health"],
        "eod": v["eod"],
        "journal": v["journal"][-10:],
    })

@app.route("/api/select", methods=["POST"])
def api_select():
    data = request.get_json(silent=True) or {}
    key = data.get("instrument")
    if key not in INSTRUMENTS:
        return jsonify({"ok": False, "error": "Invalid instrument"}), 400

    with STATE_LOCK:
        STATE["selected"] = key
        STATE["data_ok"] = False
        STATE["bars"] = None
        STATE["analysis"] = None
        STATE["last_error"] = "Loading selected instrument..."
        # IMPORTANT: a locked trade is NOT automatically switched to another instrument.
        # User must reset it explicitly if needed.
    threading.Thread(target=refresh_once, daemon=True).start()
    return jsonify({"ok": True, "selected": key})

@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    threading.Thread(target=refresh_once, daemon=True).start()
    return jsonify({"ok": True})

@app.route("/api/reset", methods=["POST"])
def api_reset():
    with STATE_LOCK:
        old = STATE["trade"]
        STATE["trade"] = None
    if old:
        add_journal("TRADE RESET", old.get("name", ""))
    return jsonify({"ok": True})

# ---------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------

def start_background():
    t = threading.Thread(target=refresh_loop, daemon=True)
    t.start()

start_background()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, debug=False)
