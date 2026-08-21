# Professional Signals V3.7.2
# Decision-support only. This app does not place trades.
#
# Data policy:
# - NIFTY: Yahoo Finance public chart endpoint.
# - MCX: TradingView WebSocket chart data for the exact selected symbol.
# - NO cross-instrument fallback. If MCX data is unavailable, signals are disabled.
#
# Render start command:
#   gunicorn app:app
#
# requirements.txt:
# Flask
# gunicorn
# pandas
# numpy
# requests
# websocket-client
# yfinance

import os
import json
import time
import math
import random
import string
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests
from flask import Flask, jsonify, request, render_template_string
from websocket import create_connection

app = Flask(__name__)

IST = ZoneInfo("Asia/Kolkata")

INSTRUMENTS = {
    "NIFTY": {
        "label": "NIFTY 50",
        "market": "NIFTY OPTIONS",
        "source": "yahoo",
        "symbol": "^NSEI",
        "tv_symbol": "NSE:NIFTY",
        "tick": 0.05,
    },
    "CRUDEOILM": {
        "label": "CRUDEOIL MINI",
        "market": "MCX COMMODITIES",
        "source": "tradingview",
        "symbol": "MCX:CRUDEOILM1!",
        "tick": 1.0,
    },
    "CRUDEOIL": {
        "label": "CRUDEOIL",
        "market": "MCX COMMODITIES",
        "source": "tradingview",
        "symbol": "MCX:CRUDEOIL1!",
        "tick": 1.0,
    },
    "GOLDM": {
        "label": "GOLD MINI",
        "market": "MCX COMMODITIES",
        "source": "tradingview",
        "symbol": "MCX:GOLDM1!",
        "tick": 1.0,
    },
    "GOLD": {
        "label": "GOLD",
        "market": "MCX COMMODITIES",
        "source": "tradingview",
        "symbol": "MCX:GOLD1!",
        "tick": 1.0,
    },
    "SILVERM": {
        "label": "SILVER MINI",
        "market": "MCX COMMODITIES",
        "source": "tradingview",
        "symbol": "MCX:SILVERM1!",
        "tick": 1.0,
    },
    "NATURALGAS": {
        "label": "NATURAL GAS",
        "market": "MCX COMMODITIES",
        "source": "tradingview",
        "symbol": "MCX:NATURALGAS1!",
        "tick": 0.10,
    },
}

CACHE = {}
CACHE_TTL = 20


def now_ist():
    return datetime.now(IST)


def fmt_num(x, digits=2):
    if x is None or not np.isfinite(x):
        return None
    return round(float(x), digits)


def make_session(prefix):
    chars = string.ascii_lowercase
    return prefix + "_" + "".join(random.choice(chars) for _ in range(12))


def tv_frame(method, params):
    payload = json.dumps({"m": method, "p": params}, separators=(",", ":"))
    return f"~m~{len(payload)}~m~{payload}"


def tv_messages(raw):
    out = []
    for part in re.split(r"~m~\d+~m~", raw):
        part = part.strip()
        if not part or not part.startswith("{"):
            continue
        try:
            out.append(json.loads(part))
        except Exception:
            pass
    return out


def fetch_tradingview(symbol, interval_min=5, bars=260, timeout=12):
    """
    Fetch chart bars from TradingView's WebSocket interface for the exact symbol.
    No other instrument is substituted if this fails.
    """
    ws = None
    chart_session = make_session("cs")
    quote_session = make_session("qs")
    try:
        ws = create_connection(
            "wss://data.tradingview.com/socket.io/websocket",
            timeout=timeout,
            origin="https://www.tradingview.com",
            header=[
                "User-Agent: Mozilla/5.0",
                "Pragma: no-cache",
                "Cache-Control: no-cache",
            ],
        )
        ws.send(tv_frame("set_auth_token", ["unauthorized_user_token"]))
        ws.send(tv_frame("set_locale", ["en", "US"]))
        ws.send(tv_frame("chart_create_session", [chart_session, ""]))
        ws.send(tv_frame("quote_create_session", [quote_session]))

        alias = "symbol_1"
        encoded = "=" + json.dumps(
            {"symbol": symbol, "adjustment": "splits", "session": "regular"},
            separators=(",", ":"),
        )
        ws.send(tv_frame("resolve_symbol", [chart_session, alias, encoded]))
        ws.send(tv_frame(
            "create_series",
            [chart_session, "s1", "s1", alias, str(int(interval_min)), int(bars)]
        ))

        rows = []
        deadline = time.time() + timeout
        symbol_error = None

        while time.time() < deadline:
            try:
                raw = ws.recv()
            except Exception:
                break
            if not isinstance(raw, str):
                continue

            for msg in tv_messages(raw):
                method = msg.get("m")
                params = msg.get("p", [])

                if method in ("symbol_error", "series_error"):
                    symbol_error = str(params)
                    raise RuntimeError(f"TradingView {method}: {symbol_error}")

                if method == "timescale_update" and len(params) >= 2:
                    payload = params[1]
                    series = payload.get("s1") or payload.get("sds_1")
                    if isinstance(series, dict):
                        items = series.get("s", [])
                        temp = []
                        for item in items:
                            v = item.get("v") if isinstance(item, dict) else None
                            if not isinstance(v, list) or len(v) < 6:
                                continue
                            # TV commonly returns: index, timestamp, open, high, low, close, volume
                            try:
                                ts = float(v[1])
                                op = float(v[2])
                                hi = float(v[3])
                                lo = float(v[4])
                                cl = float(v[5])
                                vol = float(v[6]) if len(v) > 6 and v[6] is not None else 0.0
                                temp.append((ts, op, hi, lo, cl, vol))
                            except Exception:
                                continue
                        if temp:
                            rows = temp

                if method == "series_completed" and rows:
                    deadline = 0
                    break

        if not rows:
            raise RuntimeError(
                f"TradingView returned no chart bars for {symbol}. "
                "MCX market data may require an authenticated/licensed TradingView feed."
            )

        df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
        df["datetime"] = pd.to_datetime(df["ts"], unit="s", utc=True).dt.tz_convert(IST)
        df = df.drop_duplicates("datetime").sort_values("datetime").set_index("datetime")
        return df[["open", "high", "low", "close", "volume"]].astype(float)

    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass


def fetch_yahoo(symbol, interval="5m", range_="5d", timeout=10):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{requests.utils.quote(symbol, safe='')}"
    params = {
        "interval": interval,
        "range": range_,
        "includePrePost": "false",
        "events": "div,splits",
    }
    r = requests.get(
        url,
        params=params,
        timeout=timeout,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    r.raise_for_status()
    j = r.json()
    result = (j.get("chart") or {}).get("result")
    if not result:
        err = (j.get("chart") or {}).get("error")
        raise RuntimeError(f"Yahoo returned no data: {err}")
    result = result[0]
    ts = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    if not ts:
        raise RuntimeError("Yahoo returned no timestamps")
    df = pd.DataFrame({
        "datetime": pd.to_datetime(ts, unit="s", utc=True).tz_convert(IST),
        "open": quote.get("open", []),
        "high": quote.get("high", []),
        "low": quote.get("low", []),
        "close": quote.get("close", []),
        "volume": quote.get("volume", []),
    }).set_index("datetime")
    df = df.apply(pd.to_numeric, errors="coerce").dropna(subset=["open","high","low","close"])
    if len(df) < 30:
        raise RuntimeError("Yahoo returned too few usable candles")
    return df


def resample_15m(df):
    out = pd.DataFrame({
        "open": df["open"].resample("15min").first(),
        "high": df["high"].resample("15min").max(),
        "low": df["low"].resample("15min").min(),
        "close": df["close"].resample("15min").last(),
        "volume": df["volume"].resample("15min").sum(),
    }).dropna()
    return out


def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def rsi(s, n=14):
    d = s.diff()
    up = d.clip(lower=0)
    dn = -d.clip(upper=0)
    avg_up = up.ewm(alpha=1/n, adjust=False).mean()
    avg_dn = dn.ewm(alpha=1/n, adjust=False).mean()
    rs = avg_up / avg_dn.replace(0, np.nan)
    return (100 - (100 / (1 + rs))).fillna(50)


def atr(df, n=14):
    prev = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev).abs(),
        (df["low"] - prev).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False).mean()


def adx(df, n=14):
    high = df["high"]
    low = df["low"]
    close = df["close"]
    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index)

    prev_close = close.shift()
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    atr_s = tr.ewm(alpha=1/n, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1/n, adjust=False).mean() / atr_s.replace(0, np.nan)
    minus_di = 100 * minus_dm.ewm(alpha=1/n, adjust=False).mean() / atr_s.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1/n, adjust=False).mean().fillna(0)


def add_indicators(df):
    x = df.copy()
    x["ema10"] = ema(x["close"], 10)
    x["ema20"] = ema(x["close"], 20)
    x["ema50"] = ema(x["close"], 50)
    x["rsi"] = rsi(x["close"], 14)
    x["atr"] = atr(x, 14)
    x["adx"] = adx(x, 14)

    fast = ema(x["close"], 12)
    slow = ema(x["close"], 26)
    x["macd"] = fast - slow
    x["macd_signal"] = ema(x["macd"], 9)

    pv = x["close"] * x["volume"].fillna(0)
    day = pd.Series(x.index.date, index=x.index)
    cum_pv = pv.groupby(day).cumsum()
    cum_v = x["volume"].fillna(0).groupby(day).cumsum().replace(0, np.nan)
    x["vwap"] = (cum_pv / cum_v).fillna(x["close"].expanding().mean())
    return x


def rating(row):
    score = 0
    score += 1 if row["close"] > row["ema10"] else -1
    score += 1 if row["ema10"] > row["ema20"] else -1
    score += 1 if row["close"] > row["ema50"] else -1
    score += 1 if row["macd"] > row["macd_signal"] else -1
    if row["rsi"] >= 58:
        score += 1
    elif row["rsi"] <= 42:
        score -= 1

    if score >= 4:
        return "STRONG BUY"
    if score >= 2:
        return "BUY"
    if score <= -4:
        return "STRONG SELL"
    if score <= -2:
        return "SELL"
    return "NEUTRAL"


def direction_from_rating(rt):
    if "BUY" in rt:
        return "BUY"
    if "SELL" in rt:
        return "SELL"
    return "NEUTRAL"


def build_signal(df5):
    if len(df5) < 60:
        raise RuntimeError("Not enough candles to calculate indicators")
    df15 = resample_15m(df5)
    if len(df15) < 25:
        raise RuntimeError("Not enough 15-minute candles")

    a5 = add_indicators(df5)
    a15 = add_indicators(df15)
    r5 = a5.iloc[-1]
    r15 = a15.iloc[-1]

    rt5 = rating(r5)
    rt15 = rating(r15)
    d5 = direction_from_rating(rt5)
    d15 = direction_from_rating(rt15)

    last = float(r5["close"])
    atr5 = float(r5["atr"])
    adx5 = float(r5["adx"])
    adx15 = float(r15["adx"])

    same_dir = d5 == d15 and d5 != "NEUTRAL"
    ema_align = (
        (d5 == "BUY" and r5["ema10"] > r5["ema20"] and r15["ema10"] > r15["ema20"]) or
        (d5 == "SELL" and r5["ema10"] < r5["ema20"] and r15["ema10"] < r15["ema20"])
    )
    ema50_align = (
        (d5 == "BUY" and last > r5["ema50"] and last > r15["ema50"]) or
        (d5 == "SELL" and last < r5["ema50"] and last < r15["ema50"])
    )
    vwap_align = (
        (d5 == "BUY" and last > r5["vwap"]) or
        (d5 == "SELL" and last < r5["vwap"])
    )
    macd_align = (
        (d5 == "BUY" and r5["macd"] > r5["macd_signal"] and r15["macd"] > r15["macd_signal"]) or
        (d5 == "SELL" and r5["macd"] < r5["macd_signal"] and r15["macd"] < r15["macd_signal"])
    )
    rsi_healthy = (
        (d5 == "BUY" and 50 <= r5["rsi"] <= 72 and 50 <= r15["rsi"] <= 72) or
        (d5 == "SELL" and 28 <= r5["rsi"] <= 50 and 28 <= r15["rsi"] <= 50)
    )
    trend_strong = adx5 >= 18 and adx15 >= 18

    # Closed-candle confirmation
    prev = a5.iloc[-2]
    candle_confirm = (
        (d5 == "BUY" and r5["close"] > r5["open"] and r5["close"] > prev["high"]) or
        (d5 == "SELL" and r5["close"] < r5["open"] and r5["close"] < prev["low"])
    )

    recent = a5.iloc[-7:-1]
    swing_high = float(recent["high"].max())
    swing_low = float(recent["low"].min())
    buffer_ = max(atr5 * 0.15, last * 0.0005)
    breakout = (
        (d5 == "BUY" and last > swing_high + buffer_) or
        (d5 == "SELL" and last < swing_low - buffer_)
    )

    checks = [
        ("5m + 15m direction agree", same_dir),
        ("EMA 10/20 aligned", ema_align),
        ("Price beyond EMA50s", ema50_align),
        ("Price on correct side of VWAP", vwap_align),
        ("MACD confirms 5m + 15m", macd_align),
        ("Trend regime strong enough", trend_strong),
        ("RSI healthy / not stretched", rsi_healthy),
        ("5m candle confirms", candle_confirm),
        ("Breakout clears structure + buffer", breakout),
    ]
    passed = sum(1 for _, ok in checks if ok)

    base = {
        "STRONG BUY": 86, "BUY": 72, "NEUTRAL": 50,
        "SELL": 72, "STRONG SELL": 86
    }.get(rt5, 50)
    confidence = int(max(0, min(100, base + (passed - 4) * 4)))

    if passed >= 8 and same_dir and trend_strong and breakout and candle_confirm:
        grade, action = "GRADE A", "READY"
    elif passed >= 6:
        grade, action = "GRADE B", "PREPARE ONLY"
    elif passed >= 4:
        grade, action = "GRADE C", "WAIT"
    else:
        grade, action = "BLOCKED", "NO TRADE"

    # ATR-based scanner trigger. This is a reference level, not an order.
    buy_above = max(swing_high + buffer_, last + atr5 * 0.25)
    sell_below = min(swing_low - buffer_, last - atr5 * 0.25)

    return {
        "last": fmt_num(last, 2),
        "rating5": rt5,
        "rating15": rt15,
        "rsi5": fmt_num(r5["rsi"], 2),
        "rsi15": fmt_num(r15["rsi"], 2),
        "ema10_5": fmt_num(r5["ema10"], 2),
        "ema20_5": fmt_num(r5["ema20"], 2),
        "ema10_15": fmt_num(r15["ema10"], 2),
        "ema20_15": fmt_num(r15["ema20"], 2),
        "ema50_5": fmt_num(r5["ema50"], 2),
        "ema50_15": fmt_num(r15["ema50"], 2),
        "macd5": fmt_num(r5["macd"], 2),
        "macdSig5": fmt_num(r5["macd_signal"], 2),
        "macd15": fmt_num(r15["macd"], 2),
        "macdSig15": fmt_num(r15["macd_signal"], 2),
        "adx5": fmt_num(adx5, 2),
        "adx15": fmt_num(adx15, 2),
        "atr5": fmt_num(atr5, 2),
        "vwap5": fmt_num(r5["vwap"], 2),
        "confidence": confidence,
        "grade": grade,
        "action": action,
        "direction": d5 if same_dir else "NO TRADE",
        "checks": [{"label": x, "ok": bool(ok)} for x, ok in checks],
        "buyAbove": fmt_num(buy_above, 2),
        "sellBelow": fmt_num(sell_below, 2),
        "candleTime": a5.index[-1].strftime("%d-%b-%Y %I:%M %p"),
    }


def get_data(key):
    info = INSTRUMENTS[key]
    ck = f"{key}"
    cached = CACHE.get(ck)
    if cached and time.time() - cached["ts"] < CACHE_TTL:
        return cached["value"]

    if info["source"] == "yahoo":
        df = fetch_yahoo(info["symbol"], "5m", "5d")
    else:
        df = fetch_tradingview(info["symbol"], 5, 320)

    sig = build_signal(df)
    sig["instrumentKey"] = key
    sig["instrument"] = info["label"]
    sig["market"] = info["market"]
    sig["source"] = "Yahoo Finance" if info["source"] == "yahoo" else "TradingView"
    sig["sourceSymbol"] = info["symbol"]
    sig["updated"] = now_ist().strftime("%d-%b-%Y %I:%M:%S %p")
    CACHE[ck] = {"ts": time.time(), "value": sig}
    return sig


HTML = r"""
<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Professional Signals V3.7.2</title>
<style>
:root{--bg:#06182a;--card:#0d2339;--line:#1c4265;--text:#edf5ff;--muted:#9eb3ca;--green:#00d46a;--red:#ff6688;--orange:#ff9d00;--blue:#08aeed}
*{box-sizing:border-box}body{margin:0;background:linear-gradient(#041424,#06182a);color:var(--text);font-family:Arial,Helvetica,sans-serif}
.wrap{max-width:760px;margin:auto;padding:22px 20px 90px}.title{font-size:31px;font-weight:800;margin:8px 0}.sub{color:var(--muted);font-size:17px;line-height:1.5}
.card{background:var(--card);border:1px solid var(--line);border-radius:28px;padding:24px;margin:18px 0}.warn{background:#473000;border-color:#8a6200;color:#ffe3a1}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}.box{background:#102c48;border:1px solid var(--line);border-radius:22px;padding:18px}.lbl{color:var(--muted);font-size:15px}.val{font-size:28px;font-weight:800;margin-top:6px}
.big{font-size:52px;font-weight:800;margin:8px 0}.status{font-size:33px;font-weight:800}.green{color:var(--green)}.red{color:var(--red)}.orange{color:var(--orange)}
select{width:100%;padding:17px;background:#102c48;color:white;border:1px solid var(--line);border-radius:18px;font-size:18px;font-weight:700}
.row{display:grid;grid-template-columns:1fr 1fr;gap:16px}.pill{display:inline-block;border:1px solid var(--line);border-radius:22px;padding:9px 13px;margin:6px 5px 0 0;font-size:15px}.ok{border-color:#087b48;color:#72f1ad}.bad{border-color:#8b3650;color:#ff8da7}
.bar{height:18px;border:1px solid var(--line);border-radius:20px;overflow:hidden;margin-top:14px}.fill{height:100%;background:linear-gradient(90deg,#06afea,#00d46a);width:0%}
.btn{width:100%;padding:18px;border:0;border-radius:18px;background:var(--blue);font-weight:800;font-size:20px;margin-top:16px}
.err{white-space:pre-wrap;color:#ff9bb0;line-height:1.45}.muted{color:var(--muted)}.small{font-size:14px}
@media(max-width:560px){.wrap{padding:18px 20px 70px}.title{font-size:28px}.big{font-size:45px}.grid,.row{gap:12px}.box{padding:16px}.val{font-size:24px}}
</style>
</head>
<body>
<div class="wrap">
  <div class="title">📈 Professional Signals V3.7.2</div>
  <div class="sub">NIFTY options + MCX futures • 5m + 15m confirmation • quality filters • no cross-instrument fallback</div>

  <div class="card warn">
    Decision-support only. Public feeds may be delayed, restricted or unavailable. Verify the exact contract, expiry, price and order in your broker before any real trade.
  </div>

  <div class="card">
    <div class="lbl">V3.7.2 MARKET / INSTRUMENT</div>
    <div class="row" style="margin-top:14px">
      <select id="market" onchange="rebuildInstruments()">
        <option>NIFTY OPTIONS</option>
        <option>MCX COMMODITIES</option>
      </select>
      <select id="instrument" onchange="refreshData()"></select>
    </div>
    <div id="sourceNote" class="sub" style="margin-top:12px"></div>
  </div>

  <div class="card">
    <div id="conn" class="sub">● Loading...</div>
    <div id="price" class="big">—</div>
    <div id="headline" class="status">WAITING</div>
    <div id="updated" class="sub"></div>
    <div id="error" class="err"></div>
  </div>

  <div class="card">
    <div class="lbl">CURRENT MARKET VIEW</div>
    <div id="marketView" style="font-size:26px;font-weight:800;margin-top:10px">NO VERIFIED SIGNAL</div>
    <div class="sub" style="margin-top:10px">Signal confidence</div>
    <div id="confidence" class="big">—</div>
    <div class="bar"><div id="fill" class="fill"></div></div>
  </div>

  <div class="card">
    <div class="lbl">V3.7.2 PROFESSIONAL ENTRY QUALITY ENGINE</div>
    <div id="grade" class="status" style="margin-top:10px">DATA REQUIRED</div>
    <div id="action" class="status" style="font-size:26px">WAIT</div>
    <div id="checks" style="margin-top:14px"></div>
  </div>

  <div class="card">
    <div class="lbl">CURRENT MARKET INDICATORS</div>
    <div class="grid" style="margin-top:16px">
      <div><div class="lbl">Rating 5m</div><div id="rating5" class="val">—</div></div>
      <div><div class="lbl">Rating 15m</div><div id="rating15" class="val">—</div></div>
      <div><div class="lbl">RSI 5m</div><div id="rsi5" class="val">—</div></div>
      <div><div class="lbl">RSI 15m</div><div id="rsi15" class="val">—</div></div>
      <div><div class="lbl">EMA 10/20 5m</div><div id="ema5" class="val" style="font-size:22px">—</div></div>
      <div><div class="lbl">EMA 10/20 15m</div><div id="ema15" class="val" style="font-size:22px">—</div></div>
      <div><div class="lbl">MACD 5m</div><div id="macd5" class="val" style="font-size:22px">—</div></div>
      <div><div class="lbl">MACD 15m</div><div id="macd15" class="val" style="font-size:22px">—</div></div>
      <div><div class="lbl">ADX 5m</div><div id="adx5" class="val">—</div></div>
      <div><div class="lbl">ADX 15m</div><div id="adx15" class="val">—</div></div>
      <div><div class="lbl">ATR 5m</div><div id="atr5" class="val">—</div></div>
      <div><div class="lbl">VWAP 5m</div><div id="vwap5" class="val">—</div></div>
      <div><div class="lbl">EMA 50 5m</div><div id="ema50_5" class="val">—</div></div>
      <div><div class="lbl">EMA 50 15m</div><div id="ema50_15" class="val">—</div></div>
    </div>
  </div>

  <div class="card">
    <div class="lbl">CURRENT SCANNER TRIGGER LEVELS</div>
    <div class="grid" style="margin-top:16px">
      <div class="box"><div class="lbl">BUY ABOVE</div><div id="buyAbove" class="val">—</div></div>
      <div class="box"><div class="lbl">SELL BELOW</div><div id="sellBelow" class="val">—</div></div>
    </div>
  </div>

  <button class="btn" onclick="refreshData()">REFRESH NOW</button>
</div>

<script>
const instruments = {
  "NIFTY OPTIONS":[["NIFTY","NIFTY 50"]],
  "MCX COMMODITIES":[
    ["CRUDEOILM","CRUDEOIL MINI"],
    ["CRUDEOIL","CRUDEOIL"],
    ["GOLDM","GOLD MINI"],
    ["GOLD","GOLD"],
    ["SILVERM","SILVER MINI"],
    ["NATURALGAS","NATURAL GAS"]
  ]
};
function el(id){return document.getElementById(id)}
function rebuildInstruments(){
  const m=el("market").value, s=el("instrument"); s.innerHTML="";
  instruments[m].forEach(x=>{let o=document.createElement("option");o.value=x[0];o.textContent=x[1];s.appendChild(o)});
  el("sourceNote").textContent = m==="NIFTY OPTIONS"
    ? "NIFTY mode uses Yahoo Finance public chart data."
    : "MCX mode requests the exact TradingView MCX symbol. If MCX access is restricted, no other instrument is substituted.";
  refreshData();
}
function clearValues(){
  ["price","confidence","rating5","rating15","rsi5","rsi15","ema5","ema15","macd5","macd15","adx5","adx15","atr5","vwap5","ema50_5","ema50_15","buyAbove","sellBelow"].forEach(x=>el(x).textContent="—");
  el("fill").style.width="0%"; el("checks").innerHTML="";
}
async function refreshData(){
  clearValues();
  el("conn").textContent="● Updating...";
  el("headline").textContent="WAITING";
  el("marketView").textContent="NO VERIFIED SIGNAL";
  el("grade").textContent="DATA REQUIRED";
  el("action").textContent="WAIT";
  el("error").textContent="";
  const key=el("instrument").value;
  try{
    const r=await fetch("/api/data?instrument="+encodeURIComponent(key),{cache:"no-store"});
    const j=await r.json();
    if(!r.ok || !j.ok) throw new Error(j.error||"Data unavailable");

    el("conn").textContent="● Connected • V3.7.2";
    el("conn").className="sub green";
    el("price").textContent=j.last?.toLocaleString("en-IN") ?? "—";
    el("headline").textContent=j.direction==="BUY"?"BUY WATCH":j.direction==="SELL"?"SELL WATCH":"NO TRADE";
    el("headline").className="status "+(j.direction==="BUY"?"green":j.direction==="SELL"?"red":"orange");
    el("updated").textContent="Updated "+j.updated+" • "+j.source+" • "+j.sourceSymbol;
    el("marketView").textContent=(j.direction==="NO TRADE"?"NO TRADE":j.direction+" WATCH")+" • 5m "+j.rating5+" • 15m "+j.rating15;
    el("confidence").textContent=j.confidence+" / 100"; el("fill").style.width=j.confidence+"%";
    el("grade").textContent=j.grade; el("action").textContent=j.action;

    j.checks.forEach(c=>{let x=document.createElement("span");x.className="pill "+(c.ok?"ok":"bad");x.textContent=(c.ok?"✓ ":"✕ ")+c.label;el("checks").appendChild(x)});

    el("rating5").textContent=j.rating5; el("rating15").textContent=j.rating15;
    el("rsi5").textContent=j.rsi5; el("rsi15").textContent=j.rsi15;
    el("ema5").textContent=j.ema10_5+" / "+j.ema20_5; el("ema15").textContent=j.ema10_15+" / "+j.ema20_15;
    el("macd5").textContent=j.macd5+" / "+j.macdSig5; el("macd15").textContent=j.macd15+" / "+j.macdSig15;
    el("adx5").textContent=j.adx5; el("adx15").textContent=j.adx15;
    el("atr5").textContent=j.atr5; el("vwap5").textContent=j.vwap5;
    el("ema50_5").textContent=j.ema50_5; el("ema50_15").textContent=j.ema50_15;
    el("buyAbove").textContent=j.buyAbove; el("sellBelow").textContent=j.sellBelow;
  }catch(e){
    el("conn").textContent="● Data connection interrupted";
    el("conn").className="sub orange";
    el("headline").textContent="DATA UNAVAILABLE";
    el("headline").className="status red";
    el("error").textContent="\n"+e.message+"\n\nSignals are disabled until valid data for the selected instrument is received.";
  }
}
rebuildInstruments();
setInterval(refreshData, 30000);
</script>
</body>
</html>
"""


@app.get("/")
def index():
    return render_template_string(HTML)


@app.get("/health")
def health():
    return jsonify({"ok": True, "version": "3.7.2", "time": now_ist().isoformat()})


@app.get("/api/data")
def api_data():
    key = request.args.get("instrument", "NIFTY").upper()
    if key not in INSTRUMENTS:
        return jsonify({"ok": False, "error": "Unknown instrument"}), 400
    try:
        data = get_data(key)
        return jsonify({"ok": True, **data})
    except Exception as e:
        return jsonify({
            "ok": False,
            "instrument": INSTRUMENTS[key]["label"],
            "sourceSymbol": INSTRUMENTS[key]["symbol"],
            "error": str(e),
        }), 503


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, debug=False)
