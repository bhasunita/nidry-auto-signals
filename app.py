
from flask import Flask, jsonify
import requests
import math
from datetime import datetime

app = Flask(__name__)

YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/%5ENSEI"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/126 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#08111e">
<title>NIFTY Auto Signals</title>
<style>
:root{--card:#0f1c2e;--card2:#12233a;--text:#eef5ff;--muted:#9bb0c9;
--green:#22c55e;--red:#ef4444;--amber:#f59e0b;--line:#223855}
*{box-sizing:border-box}
body{margin:0;background:linear-gradient(180deg,#06101d,#0a1627);color:var(--text);
font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;min-height:100vh}
.wrap{max-width:760px;margin:auto;padding:14px 12px 46px}
h1{font-size:22px;margin:4px 0}.sub{color:var(--muted);font-size:13px;margin-bottom:12px}
.card{background:rgba(15,28,46,.97);border:1px solid var(--line);border-radius:18px;padding:14px;margin:10px 0}
.status{display:flex;gap:8px;align-items:center;font-size:13px;color:var(--muted)}
.dot{width:9px;height:9px;border-radius:50%;background:#64748b}
.dot.on{background:var(--green);box-shadow:0 0 9px var(--green)}.dot.warn{background:var(--amber)}
.price{font-size:40px;font-weight:850;letter-spacing:-1.2px;margin:4px 0}.signal{font-size:28px;font-weight:900;margin-top:5px}
.buy{color:var(--green)}.sell{color:var(--red)}.watch{color:var(--amber)}.neutral{color:#cbd5e1}
.row{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.row3{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
@media(max-width:500px){.row3{grid-template-columns:1fr 1fr}}
.kpi{background:var(--card2);border:1px solid var(--line);border-radius:14px;padding:11px}
.kpi .t{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.5px}
.kpi .v{font-size:20px;font-weight:800;margin-top:3px}
.section-title{font-size:14px;font-weight:800;margin:0 0 9px}.small{color:var(--muted);font-size:12px;line-height:1.45}
.pill{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:5px 9px;font-size:12px;margin:3px}
.ok{border-color:#23673d;color:#8ef0ab}.bad{border-color:#73323a;color:#ff9fa9}
button{width:100%;border:0;border-radius:12px;padding:13px;font-weight:800;font-size:15px;background:#0ea5e9;color:#00101a}
.banner{background:#372b12;border:1px solid #7a5b17;border-radius:13px;padding:10px;color:#ffe4a3;font-size:12px}
#error{white-space:pre-wrap;color:#ffb4bc;font-size:12px;margin-top:8px}
</style>
</head>
<body>
<div class="wrap">
<h1>📈 NIFTY Auto Signals</h1>
<div class="sub">No API key • 5-minute public data • auto refresh</div>

<div class="banner">Public market data can be delayed or unavailable. This app gives technical signals only and does not place trades.</div>

<div class="card">
  <div class="status">
    <span id="dot" class="dot"></span>
    <span id="status">Starting…</span>
    <span style="margin-left:auto" id="updated">—</span>
  </div>
  <div class="price" id="ltp">—</div>
  <div id="signal" class="signal neutral">WAITING</div>
  <div class="small" id="bias">Loading NIFTY 50…</div>
  <div id="error"></div>
</div>

<div class="row">
  <div class="kpi"><div class="t">BUY ABOVE</div><div class="v" id="buyAbove">—</div></div>
  <div class="kpi"><div class="t">SELL BELOW</div><div class="v" id="sellBelow">—</div></div>
</div>

<div style="height:8px"></div>

<div class="row3">
  <div class="kpi"><div class="t">STOP LOSS</div><div class="v" id="sl">—</div></div>
  <div class="kpi"><div class="t">TARGET 1</div><div class="v" id="t1">—</div></div>
  <div class="kpi"><div class="t">TARGET 2</div><div class="v" id="t2">—</div></div>
</div>

<div class="card">
<div class="section-title">Indicator score</div>
<div id="checks"></div>
<div style="height:10px"></div>
<div class="row3">
<div><div class="small">RSI 14</div><b id="rsi">—</b></div>
<div><div class="small">EMA 9</div><b id="ema9">—</b></div>
<div><div class="small">EMA 21</div><b id="ema21">—</b></div>
<div><div class="small">ATR 14</div><b id="atr">—</b></div>
<div><div class="small">Bull Score</div><b id="bullScore">—</b></div>
<div><div class="small">Bear Score</div><b id="bearScore">—</b></div>
</div>
</div>

<button id="refresh">REFRESH NOW</button>

<div class="card"><div class="small">
Strategy: EMA 9/21 + RSI + Supertrend + ATR + 3-candle breakout. BUY/SELL requires at least 3 of 4 conditions.
</div></div>
</div>

<script>
"use strict";
const $=id=>document.getElementById(id);
const fmt=x=>(x==null||!Number.isFinite(Number(x)))?"—":Number(x).toLocaleString("en-IN",{minimumFractionDigits:2,maximumFractionDigits:2});
let running=false;

function put(d){
  $("ltp").textContent=fmt(d.ltp);
  $("signal").textContent=d.signal;
  $("signal").className="signal "+(d.signal==="BUY"?"buy":d.signal==="SELL"?"sell":d.signal.includes("WATCH")?"watch":"neutral");
  $("bias").textContent=`${d.bias} • ${Math.max(d.bull_score,d.bear_score)}/4 conditions`;
  $("buyAbove").textContent=fmt(d.buy_above);
  $("sellBelow").textContent=fmt(d.sell_below);
  $("sl").textContent=fmt(d.sl);
  $("t1").textContent=fmt(d.target1);
  $("t2").textContent=fmt(d.target2);
  $("rsi").textContent=d.rsi.toFixed(1);
  $("ema9").textContent=fmt(d.ema9);
  $("ema21").textContent=fmt(d.ema21);
  $("atr").textContent=fmt(d.atr);
  $("bullScore").textContent=d.bull_score+"/4";
  $("bearScore").textContent=d.bear_score+"/4";
  $("checks").innerHTML=d.checks.map(x=>`<span class="pill ${x.ok?"ok":"bad"}">${x.ok?"✓":"✕"} ${x.label}</span>`).join("");
  $("dot").className="dot on";
  $("status").textContent="Connected • server feed";
  $("updated").textContent=d.updated;
  $("error").textContent="";
}

async function refresh(){
  if(running)return;
  running=true;
  $("dot").className="dot warn";
  $("status").textContent="Updating…";
  try{
    const r=await fetch("/api/signal",{cache:"no-store"});
    const d=await r.json();
    if(!r.ok || d.error) throw new Error(d.error||("HTTP "+r.status));
    put(d);
  }catch(e){
    $("dot").className="dot";
    $("status").textContent="Data unavailable";
    $("error").textContent=e.message;
  }finally{
    running=false;
  }
}
$("refresh").onclick=refresh;
refresh();
setInterval(refresh,15000);
</script>
</body>
</html>"""

def ema(values, span):
    a = 2/(span+1)
    out = []
    prev = values[0]
    for i,v in enumerate(values):
        prev = v if i == 0 else v*a + prev*(1-a)
        out.append(prev)
    return out

def rsi(values, period=14):
    out = [50.0] * len(values)
    ag = al = 0.0
    for i in range(1, len(values)):
        ch = values[i] - values[i-1]
        g, l = max(ch,0), max(-ch,0)
        if i <= period:
            ag += g
            al += l
            if i == period:
                ag /= period
                al /= period
        else:
            ag = (ag*(period-1)+g)/period
            al = (al*(period-1)+l)/period
        if i >= period:
            out[i] = 100.0 if al == 0 else 100 - 100/(1+ag/al)
    return out

def atr(candles, period=14):
    tr = []
    for i,x in enumerate(candles):
        if i == 0:
            t = x["h"] - x["l"]
        else:
            pc = candles[i-1]["c"]
            t = max(x["h"]-x["l"], abs(x["h"]-pc), abs(x["l"]-pc))
        tr.append(t)

    out = []
    prev = tr[0]
    for i,t in enumerate(tr):
        prev = t if i == 0 else (prev*(period-1)+t)/period
        out.append(prev)
    return out

def supertrend(candles, period=10, mult=3.0):
    av = atr(candles, period)
    direction = [1]*len(candles)
    fu = fl = 0.0

    for i,x in enumerate(candles):
        mid = (x["h"]+x["l"])/2
        ub = mid + mult*av[i]
        lb = mid - mult*av[i]

        if i == 0:
            fu, fl = ub, lb
            continue

        fu = ub if (ub < fu or candles[i-1]["c"] > fu) else fu
        fl = lb if (lb > fl or candles[i-1]["c"] < fl) else fl

        if direction[i-1] == 1:
            direction[i] = -1 if x["c"] < fl else 1
        else:
            direction[i] = 1 if x["c"] > fu else -1

    return direction

def fetch_nifty():
    params = {
        "interval":"5m",
        "range":"5d",
        "includePrePost":"false",
        "events":"div,splits"
    }
    r = requests.get(YAHOO_URL, params=params, headers=HEADERS, timeout=12)
    r.raise_for_status()
    j = r.json()

    result = j.get("chart",{}).get("result",[None])[0]
    if not result:
        raise RuntimeError("Public NIFTY source returned no chart data.")

    q = result["indicators"]["quote"][0]
    ts = result["timestamp"]

    candles = []
    for i,t in enumerate(ts):
        vals = [q["open"][i], q["high"][i], q["low"][i], q["close"][i]]
        if all(v is not None and math.isfinite(float(v)) for v in vals):
            candles.append({
                "t":t,
                "o":float(vals[0]),
                "h":float(vals[1]),
                "l":float(vals[2]),
                "c":float(vals[3]),
            })

    if len(candles) < 30:
        raise RuntimeError("Not enough 5-minute candles were returned.")

    return candles

def build_signal(candles):
    closes = [x["c"] for x in candles]
    e9 = ema(closes,9)
    e21 = ema(closes,21)
    rs = rsi(closes,14)
    av = atr(candles,14)
    st = supertrend(candles,10,3.0)

    i = len(candles)-1
    ltp = closes[i]

    bull = [
        ("EMA 9 > EMA 21", e9[i] > e21[i]),
        ("Price > EMA 21", ltp > e21[i]),
        ("RSI > 55", rs[i] > 55),
        ("Supertrend bullish", st[i] == 1),
    ]
    bear = [
        ("EMA 9 < EMA 21", e9[i] < e21[i]),
        ("Price < EMA 21", ltp < e21[i]),
        ("RSI < 45", rs[i] < 45),
        ("Supertrend bearish", st[i] == -1),
    ]

    bs = sum(int(x[1]) for x in bull)
    ss = sum(int(x[1]) for x in bear)

    recent = candles[max(0,i-3):i]
    hi = max(x["h"] for x in recent)
    lo = min(x["l"] for x in recent)

    buy = hi + 0.10*av[i]
    sell = lo - 0.10*av[i]

    signal = "NO TRADE"
    bias = "NEUTRAL"
    side = 0
    checks = bull if bs >= ss else bear

    if bs >= 3 and bs > ss:
        bias = "BULLISH"
        signal = "BUY" if ltp >= buy else "BUY WATCH"
        side = 1
        checks = bull
    elif ss >= 3 and ss > bs:
        bias = "BEARISH"
        signal = "SELL" if ltp <= sell else "SELL WATCH"
        side = -1
        checks = bear

    entry = buy if side == 1 else sell if side == -1 else None
    sl = t1 = t2 = None

    if side == 1:
        sl = entry - av[i]
        t1 = entry + 1.5*av[i]
        t2 = entry + 2.5*av[i]
    elif side == -1:
        sl = entry + av[i]
        t1 = entry - 1.5*av[i]
        t2 = entry - 2.5*av[i]

    return {
        "ltp":ltp,
        "signal":signal,
        "bias":bias,
        "buy_above":buy,
        "sell_below":sell,
        "sl":sl,
        "target1":t1,
        "target2":t2,
        "rsi":rs[i],
        "ema9":e9[i],
        "ema21":e21[i],
        "atr":av[i],
        "bull_score":bs,
        "bear_score":ss,
        "checks":[{"label":x[0],"ok":bool(x[1])} for x in checks],
        "updated":datetime.now().strftime("%H:%M:%S"),
    }

@app.get("/")
def home():
    return PAGE

@app.get("/api/signal")
def signal():
    try:
        candles = fetch_nifty()
        return jsonify(build_signal(candles))
    except Exception as e:
        return jsonify({"error":str(e)}), 503

if __name__ == "__main__":
    import os
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT","10000")))
