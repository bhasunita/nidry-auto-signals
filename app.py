from flask import Flask, jsonify
import requests
import time
from datetime import datetime
from zoneinfo import ZoneInfo

app = Flask(__name__)
IST = ZoneInfo("Asia/Kolkata")

VERSION = "3.8-TV"
TV_URL = "https://scanner.tradingview.com/india/scan"

TV_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/126 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.tradingview.com",
    "Referer": "https://www.tradingview.com/"
}

_cache = {"data": None, "ts": 0}

def market_open_now():
    now = datetime.now(IST)
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    return 555 <= minutes <= 930

def fetch_tradingview():
    cols = [
        "close|5","open|5","high|5","low|5","RSI|5","EMA9|5","EMA21|5","ATR|5",
        "Recommend.All|5","MACD.macd|5","MACD.signal|5","ADX|5",
        "close|15","RSI|15","EMA9|15","EMA21|15","Recommend.All|15",
        "MACD.macd|15","MACD.signal|15","ADX|15"
    ]
    payload = {
        "symbols": {"tickers": ["NSE:NIFTY"], "query": {"types": []}},
        "columns": cols,
        "range": [0, 1]
    }
    r = requests.post(TV_URL, json=payload, headers=TV_HEADERS, timeout=12)
    r.raise_for_status()
    j = r.json()
    if not j.get("data"):
        raise RuntimeError("TradingView returned no NIFTY data.")
    vals = j["data"][0]["d"]
    if len(vals) != len(cols):
        raise RuntimeError("TradingView returned incomplete data.")
    x = dict(zip(cols, vals))

    def f(key, default=0.0):
        try:
            v = x.get(key)
            return float(v) if v is not None else float(default)
        except Exception:
            return float(default)

    spot = f("close|5")
    if spot <= 0:
        raise RuntimeError("TradingView returned an invalid NIFTY price.")

    return {
        "spot": spot,
        "open5": f("open|5", spot),
        "high5": f("high|5", spot),
        "low5": f("low|5", spot),
        "rsi5": f("RSI|5", 50),
        "ema9_5": f("EMA9|5", spot),
        "ema21_5": f("EMA21|5", spot),
        "atr5": max(f("ATR|5", 1), 0.01),
        "rec5": f("Recommend.All|5"),
        "macd5": f("MACD.macd|5"),
        "macds5": f("MACD.signal|5"),
        "adx5": f("ADX|5"),
        "close15": f("close|15", spot),
        "rsi15": f("RSI|15", 50),
        "ema9_15": f("EMA9|15", spot),
        "ema21_15": f("EMA21|15", spot),
        "rec15": f("Recommend.All|15"),
        "macd15": f("MACD.macd|15"),
        "macds15": f("MACD.signal|15"),
        "adx15": f("ADX|15"),
    }

def rating(v):
    if v >= 0.5: return "STRONG BUY"
    if v >= 0.1: return "BUY"
    if v <= -0.5: return "STRONG SELL"
    if v <= -0.1: return "SELL"
    return "NEUTRAL"

def build_signal():
    tv = fetch_tradingview()
    spot = tv["spot"]
    atr = tv["atr5"]

    bull_checks = [
        ("EMA 9 above EMA 21", tv["ema9_5"] > tv["ema21_5"]),
        ("RSI bullish", 52 <= tv["rsi5"] <= 70),
        ("5m rating bullish", tv["rec5"] >= 0.1),
        ("15m confirms bullish", tv["rec15"] >= 0.1),
        ("MACD bullish", tv["macd5"] > tv["macds5"]),
        ("ADX trend strength", tv["adx5"] >= 18),
    ]
    bear_checks = [
        ("EMA 9 below EMA 21", tv["ema9_5"] < tv["ema21_5"]),
        ("RSI bearish", 30 <= tv["rsi5"] <= 48),
        ("5m rating bearish", tv["rec5"] <= -0.1),
        ("15m confirms bearish", tv["rec15"] <= -0.1),
        ("MACD bearish", tv["macd5"] < tv["macds5"]),
        ("ADX trend strength", tv["adx5"] >= 18),
    ]

    bull_score = sum(1 for _, ok in bull_checks if ok)
    bear_score = sum(1 for _, ok in bear_checks if ok)

    trigger_buffer = max(atr * 0.20, 5.0)
    buy_above = spot + trigger_buffer
    sell_below = spot - trigger_buffer

    if bull_score >= 5 and bull_score >= bear_score + 2:
        signal = "BUY WATCH"
        direction = "BUY"
        entry = buy_above
        stop = entry - max(atr * 1.20, 12.0)
        risk = entry - stop
        target1 = entry + risk * 1.5
        target2 = entry + risk * 2.4
    elif bear_score >= 5 and bear_score >= bull_score + 2:
        signal = "SELL WATCH"
        direction = "SELL"
        entry = sell_below
        stop = entry + max(atr * 1.20, 12.0)
        risk = stop - entry
        target1 = entry - risk * 1.5
        target2 = entry - risk * 2.4
    else:
        signal = "WAITING"
        direction = "NONE"
        stop = target1 = target2 = None

    return {
        "ok": True,
        "version": VERSION,
        "market_open": market_open_now(),
        "spot": round(spot, 2),
        "signal": signal,
        "direction": direction,
        "buy_above": round(buy_above, 2),
        "sell_below": round(sell_below, 2),
        "stop_loss": round(stop, 2) if stop is not None else None,
        "target1": round(target1, 2) if target1 is not None else None,
        "target2": round(target2, 2) if target2 is not None else None,
        "rsi5": round(tv["rsi5"], 2),
        "rsi15": round(tv["rsi15"], 2),
        "ema9_5": round(tv["ema9_5"], 2),
        "ema21_5": round(tv["ema21_5"], 2),
        "ema9_15": round(tv["ema9_15"], 2),
        "ema21_15": round(tv["ema21_15"], 2),
        "atr5": round(atr, 2),
        "adx5": round(tv["adx5"], 2),
        "adx15": round(tv["adx15"], 2),
        "rating5": rating(tv["rec5"]),
        "rating15": rating(tv["rec15"]),
        "bull_score": bull_score,
        "bear_score": bear_score,
        "bull_checks": [{"label": a, "ok": b} for a, b in bull_checks],
        "bear_checks": [{"label": a, "ok": b} for a, b in bear_checks],
        "updated": datetime.now(IST).strftime("%d-%b-%Y %I:%M:%S %p"),
        "source": "TradingView public scanner"
    }

PAGE = r"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#07111f">
<title>NIFTY Auto Signals</title>
<style>
:root{--card:#0f1c2e;--card2:#12233a;--text:#eef5ff;--muted:#9bb0c9;--green:#22c55e;--red:#ef4444;--line:#223855}
*{box-sizing:border-box}
body{margin:0;background:linear-gradient(180deg,#06101d,#0a1627);color:var(--text);font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif}
.wrap{max-width:760px;margin:auto;padding:20px 20px 44px}
h1{font-size:28px;margin:6px 0 2px}.sub{color:var(--muted);font-size:16px;margin-bottom:18px}
.banner{background:#3a2c0c;border:1px solid #896514;border-radius:18px;padding:16px;color:#ffe7a8;font-size:14px;line-height:1.45;margin-bottom:18px}
.card{background:rgba(15,28,46,.98);border:1px solid var(--line);border-radius:24px;padding:24px;margin:16px 0}
.status{display:flex;align-items:center;gap:10px;color:var(--muted);font-size:14px}
.dot{width:12px;height:12px;border-radius:50%;background:#64748b}.dot.on{background:var(--green);box-shadow:0 0 12px var(--green)}
.price{font-size:54px;font-weight:850;margin:8px 0}.signal{font-size:34px;font-weight:900;margin-top:4px}
.buy{color:var(--green)}.sell{color:var(--red)}.wait{color:#dbe5f2}
.small{color:var(--muted);font-size:14px;line-height:1.45}.error{color:#ff9fa9;white-space:pre-wrap;margin-top:10px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.kpi{background:var(--card2);border:1px solid var(--line);border-radius:20px;padding:18px}
.kpi .t{font-size:13px;color:var(--muted);text-transform:uppercase}.kpi .v{font-size:28px;font-weight:850;margin-top:5px}
.indicators{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:14px}.indicators b{font-size:20px}
.pill{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:7px 10px;margin:4px;font-size:13px}
.ok{border-color:#23673d;color:#8ef0ab}.bad{border-color:#73323a;color:#ff9fa9}
button{width:100%;border:0;border-radius:18px;padding:18px;font-size:18px;font-weight:850;background:#0ea5e9;color:#00101a;margin-top:8px}
</style>
</head>
<body>
<div class="wrap">
<h1>NIFTY Auto Signals</h1>
<div class="sub">No API key required - TradingView public scanner - auto refresh</div>
<div class="banner">Public market data can be delayed or unavailable. This app provides technical decision-support only and does not place trades.</div>

<div class="card">
<div class="status"><span id="dot" class="dot"></span><span id="status">Loading market data...</span><span id="market" style="margin-left:auto">--</span></div>
<div id="spot" class="price">--</div>
<div id="signal" class="signal wait">WAITING</div>
<div id="updated" class="small">--</div>
<div id="error" class="error"></div>
</div>

<div class="grid">
<div class="kpi"><div class="t">BUY ABOVE</div><div id="buyAbove" class="v">--</div></div>
<div class="kpi"><div class="t">SELL BELOW</div><div id="sellBelow" class="v">--</div></div>
<div class="kpi"><div class="t">STOP LOSS</div><div id="sl" class="v">--</div></div>
<div class="kpi"><div class="t">TARGET 1</div><div id="t1" class="v">--</div></div>
<div class="kpi"><div class="t">TARGET 2</div><div id="t2" class="v">--</div></div>
</div>

<div class="card">
<div style="font-size:20px;font-weight:800">Indicator score</div>
<div class="indicators">
<div><div class="small">RSI 5m</div><b id="rsi5">--</b></div>
<div><div class="small">RSI 15m</div><b id="rsi15">--</b></div>
<div><div class="small">EMA 9 / 21 5m</div><b id="ema5">--</b></div>
<div><div class="small">EMA 9 / 21 15m</div><b id="ema15">--</b></div>
<div><div class="small">ATR 5m</div><b id="atr">--</b></div>
<div><div class="small">ADX 5m / 15m</div><b id="adx">--</b></div>
<div><div class="small">Bull Score</div><b id="bull">--</b></div>
<div><div class="small">Bear Score</div><b id="bear">--</b></div>
</div>
<div style="height:14px"></div><div id="checks"></div>
</div>

<button id="refresh">REFRESH NOW</button>

<div class="card">
<div class="small">Strategy: EMA 9/21 + RSI + TradingView 5m/15m rating + MACD + ADX + ATR-based levels. BUY/SELL watch requires strong multi-factor agreement.</div>
</div>
</div>

<script>
"use strict";
const $=id=>document.getElementById(id);
function fmt(x){
  if(x===null||x===undefined||!Number.isFinite(Number(x))) return "--";
  return Number(x).toLocaleString("en-IN",{minimumFractionDigits:2,maximumFractionDigits:2});
}
function money(x){
  if(x===null||x===undefined||!Number.isFinite(Number(x))) return "--";
  return "&#8377;"+fmt(x);
}
function render(d){
  $("dot").className="dot on";
  $("status").textContent="Connected - "+d.source;
  $("market").textContent=d.market_open?"MARKET OPEN":"MARKET CLOSED";
  $("spot").textContent=fmt(d.spot);
  $("signal").textContent=d.signal;
  $("signal").className="signal "+(d.direction==="BUY"?"buy":d.direction==="SELL"?"sell":"wait");
  $("updated").textContent="Updated "+d.updated+" - "+d.version;
  $("error").textContent="";
  $("buyAbove").textContent=fmt(d.buy_above);
  $("sellBelow").textContent=fmt(d.sell_below);
  $("sl").innerHTML=money(d.stop_loss);
  $("t1").innerHTML=money(d.target1);
  $("t2").innerHTML=money(d.target2);
  $("rsi5").textContent=fmt(d.rsi5);
  $("rsi15").textContent=fmt(d.rsi15);
  $("ema5").textContent=fmt(d.ema9_5)+" / "+fmt(d.ema21_5);
  $("ema15").textContent=fmt(d.ema9_15)+" / "+fmt(d.ema21_15);
  $("atr").textContent=fmt(d.atr5);
  $("adx").textContent=fmt(d.adx5)+" / "+fmt(d.adx15);
  $("bull").textContent=d.bull_score+" / 6";
  $("bear").textContent=d.bear_score+" / 6";
  const checks=d.direction==="SELL"?d.bear_checks:d.bull_checks;
  $("checks").innerHTML=checks.map(x=>`<span class="pill ${x.ok?"ok":"bad"}">${x.ok?"PASS":"FAIL"} - ${x.label}</span>`).join("");
}
async function refresh(){
  $("status").textContent="Updating...";
  try{
    const r=await fetch("/api/signal",{cache:"no-store"});
    const d=await r.json();
    if(!r.ok||d.error) throw new Error(d.error||"Request failed");
    render(d);
  }catch(e){
    $("dot").className="dot";
    $("status").textContent="Data unavailable";
    $("signal").textContent="WAITING";
    $("signal").className="signal wait";
    $("error").textContent=e.message;
  }
}
$("refresh").onclick=refresh;
refresh();
setInterval(refresh,15000);
</script>
</body>
</html>"""

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True, "version": VERSION})

@app.route("/api/signal", methods=["GET"])
def api_signal():
    now = time.time()
    if _cache["data"] is not None and now - _cache["ts"] < 12:
        return jsonify(_cache["data"])
    try:
        data = build_signal()
        _cache["data"] = data
        _cache["ts"] = now
        return jsonify(data)
    except Exception as e:
        if _cache["data"] is not None:
            old = dict(_cache["data"])
            old["warning"] = "Using cached data: " + str(e)
            return jsonify(old)
        return jsonify({"error": str(e)}), 503

@app.route("/", methods=["GET"])
@app.route("/<path:p>", methods=["GET"])
def home(p=""):
    return PAGE, 200, {"Content-Type":"text/html; charset=utf-8"}

if __name__ == "__main__":
    import os
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT","10000")))
