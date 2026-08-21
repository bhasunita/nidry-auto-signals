
from flask import Flask, jsonify
import requests, time, math
from datetime import datetime
from zoneinfo import ZoneInfo

app = Flask(__name__)
IST = ZoneInfo("Asia/Kolkata")
TV_URL = "https://scanner.tradingview.com/india/scan"
NSE_HOME = "https://www.nseindia.com/"
NSE_OC_PAGE = "https://www.nseindia.com/option-chain"
NSE_OC_CONTRACT = "https://www.nseindia.com/api/option-chain-contract-info"
NSE_OC_V3 = "https://www.nseindia.com/api/option-chain-v3"

TV_HEADERS = {
    "User-Agent":"Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/126 Safari/537.36",
    "Accept":"application/json,text/plain,*/*","Accept-Language":"en-US,en;q=0.9",
    "Origin":"https://www.tradingview.com","Referer":"https://www.tradingview.com/"
}
NSE_HEADERS = {
    "User-Agent":TV_HEADERS["User-Agent"],"Accept":"application/json,text/plain,*/*",
    "Accept-Language":"en-US,en;q=0.9","Referer":NSE_OC_PAGE,
    "X-Requested-With":"XMLHttpRequest","sec-fetch-dest":"empty",
    "sec-fetch-mode":"cors","sec-fetch-site":"same-origin"
}
_cache={"signal":None,"ts":0}

PAGE=r"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#07111f"><title>NIFTY Professional V2</title>
<style>
:root{--card:#0f1c2e;--card2:#12233a;--text:#eef5ff;--muted:#9bb0c9;--green:#22c55e;--red:#ef4444;--amber:#f59e0b;--line:#223855}
*{box-sizing:border-box}body{margin:0;background:linear-gradient(180deg,#06101d,#0a1627);color:var(--text);font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;min-height:100vh}
.wrap{max-width:820px;margin:auto;padding:14px 12px 46px}h1{font-size:22px;margin:4px 0}.sub,.small{color:var(--muted);font-size:12px;line-height:1.45}.sub{font-size:13px;margin-bottom:12px}
.card{background:rgba(15,28,46,.98);border:1px solid var(--line);border-radius:18px;padding:14px;margin:10px 0}.status{display:flex;gap:8px;align-items:center;font-size:13px;color:var(--muted);flex-wrap:wrap}
.dot{width:9px;height:9px;border-radius:50%;background:#64748b}.dot.on{background:var(--green);box-shadow:0 0 9px var(--green)}.dot.warn{background:var(--amber)}
.price{font-size:39px;font-weight:850;margin:4px 0}.signal{font-size:28px;font-weight:900;margin-top:5px}.buy{color:var(--green)}.sell{color:var(--red)}.watch{color:var(--amber)}.neutral{color:#cbd5e1}
.row{display:grid;grid-template-columns:1fr 1fr;gap:10px}.row3{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}@media(max-width:540px){.row3{grid-template-columns:1fr 1fr}}
.kpi{background:var(--card2);border:1px solid var(--line);border-radius:14px;padding:11px}.kpi .t{color:var(--muted);font-size:11px;text-transform:uppercase}.kpi .v{font-size:20px;font-weight:800;margin-top:3px}
.contract{font-size:26px;font-weight:900;margin:4px 0}.banner{background:#372b12;border:1px solid #7a5b17;border-radius:13px;padding:10px;color:#ffe4a3;font-size:12px}
.pill{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:5px 9px;font-size:12px;margin:3px}.ok{border-color:#23673d;color:#8ef0ab}.bad{border-color:#73323a;color:#ff9fa9}
button{width:100%;border:0;border-radius:12px;padding:13px;font-weight:800;font-size:15px;background:#0ea5e9;color:#00101a;margin-top:8px}.secondary{background:#1d304a;color:#eaf3ff}.danger{background:#40202a;color:#ffd9df}
.progress{height:12px;background:#071421;border:1px solid #29415f;border-radius:999px;overflow:hidden;margin-top:7px}.bar{height:100%;width:0%;background:linear-gradient(90deg,#0ea5e9,#22c55e)}
.state{font-size:18px;font-weight:850;margin-top:5px}#error{white-space:pre-wrap;color:#ffb4bc;font-size:12px;margin-top:8px}
</style></head><body><div class="wrap">
<h1>📈 NIFTY Professional Signals V2</h1><div class="sub">5m + 15m confirmation • confidence scoring • option liquidity • trade alerts</div>
<div class="banner">Decision-support only. Public feeds may be delayed. Verify contract and premium in your broker before any real order.</div>

<div class="card"><div class="status"><span id="dot" class="dot"></span><span id="status">Starting…</span><span id="marketStatus" style="margin-left:auto">—</span></div>
<div class="price" id="spot">—</div><div id="signal" class="signal neutral">WAITING</div><div class="small" id="bias">Loading market data…</div><div class="small" id="updated">—</div><div id="error"></div></div>

<div class="card"><div class="small">Signal confidence</div><div id="confidence" style="font-size:28px;font-weight:900">—</div><div class="progress"><div class="bar" id="confBar"></div></div><div class="small" id="reason">—</div></div>

<div class="card"><div class="small">Automatically selected option</div><div id="contract" class="contract">—</div><div class="small" id="expiry">—</div><div class="small" id="liquidity">—</div><div style="height:10px"></div>
<div class="row3"><div class="kpi"><div class="t">OPTION LTP</div><div class="v" id="optionLtp">—</div></div><div class="kpi"><div class="t">BID</div><div class="v" id="bid">—</div></div><div class="kpi"><div class="t">ASK / ENTRY</div><div class="v" id="ask">—</div></div></div></div>

<div class="row"><div class="kpi"><div class="t">ENTRY</div><div class="v" id="entry">—</div></div><div class="kpi"><div class="t">STOP LOSS</div><div class="v" id="sl">—</div></div></div>
<div style="height:8px"></div><div class="row"><div class="kpi"><div class="t">TARGET 1</div><div class="v" id="t1">—</div></div><div class="kpi"><div class="t">TARGET 2 / EXIT</div><div class="v" id="t2">—</div></div></div>

<div class="card"><div class="small">Trade monitor</div><div id="tradeState" class="state">NO ACTIVE TRADE</div><div class="small" id="tradeDetail">Alerts work while this page stays open.</div>
<button class="secondary" id="notifyBtn">ENABLE NOTIFICATIONS / VIBRATION</button><button class="danger" id="resetTradeBtn">RESET TRADE MONITOR</button></div>

<div class="card"><div class="row3">
<div><div class="small">Rating 5m</div><b id="rating5">—</b></div><div><div class="small">Rating 15m</div><b id="rating15">—</b></div>
<div><div class="small">RSI 5m</div><b id="rsi5">—</b></div><div><div class="small">RSI 15m</div><b id="rsi15">—</b></div>
<div><div class="small">EMA 10/20 5m</div><b id="ema5">—</b></div><div><div class="small">EMA 10/20 15m</div><b id="ema15">—</b></div>
<div><div class="small">MACD 5m</div><b id="macd5">—</b></div><div><div class="small">ADX 5m</div><b id="adx5">—</b></div>
<div><div class="small">ATR 5m</div><b id="atr5">—</b></div></div><div style="height:10px"></div><div id="checks"></div></div>

<div class="card"><div class="row"><div class="kpi"><div class="t">NIFTY BUY ABOVE</div><div class="v" id="buyAbove">—</div></div><div class="kpi"><div class="t">NIFTY SELL BELOW</div><div class="v" id="sellBelow">—</div></div></div></div>
<button id="refresh">REFRESH NOW</button></div>

<script>
"use strict";const $=id=>document.getElementById(id);const fmt=x=>(x==null||!Number.isFinite(Number(x)))?"—":Number(x).toLocaleString("en-IN",{minimumFractionDigits:2,maximumFractionDigits:2});let busy=false,notificationsEnabled=false;
function notify(title,body){if(!notificationsEnabled)return;if(navigator.vibrate)navigator.vibrate([180,80,180]);try{const c=new(window.AudioContext||window.webkitAudioContext)(),o=c.createOscillator(),g=c.createGain();o.frequency.value=title.includes("STOP")?360:title.includes("TARGET")?980:760;g.gain.value=.06;o.connect(g);g.connect(c.destination);o.start();o.stop(c.currentTime+.25)}catch(e){}if("Notification"in window&&Notification.permission==="granted"){try{new Notification(title,{body})}catch(e){}}}
function readTrade(){try{return JSON.parse(localStorage.getItem("niftyV2Trade")||"null")}catch(e){return null}}function saveTrade(t){localStorage.setItem("niftyV2Trade",JSON.stringify(t))}function clearTrade(){localStorage.removeItem("niftyV2Trade");updateTradeUI(null)}
function updateTradeUI(t){if(!t){$("tradeState").textContent="NO ACTIVE TRADE";$("tradeDetail").textContent="Alerts work while this page stays open.";return}$("tradeState").textContent=t.state;$("tradeDetail").textContent=`${t.contract} • Entry ₹${fmt(t.entry)} • SL ₹${fmt(t.sl)} • T1 ₹${fmt(t.t1)} • T2 ₹${fmt(t.t2)}`}
function monitorTrade(d){if(!d.option)return;let t=readTrade();const active=d.signal==="BUY"||d.signal==="SELL";if(!t&&active){t={contract:d.option.contract,entry:d.option.entry,sl:d.option.sl,t1:d.option.target1,t2:d.option.target2,state:"WAITING FOR ENTRY"};saveTrade(t);updateTradeUI(t);notify("NEW TRADE SETUP",`${t.contract} • Entry ₹${fmt(t.entry)}`)}if(!t||d.option.contract!==t.contract)return;const p=Number(d.option.ltp);if(!Number.isFinite(p))return;
if(t.state==="WAITING FOR ENTRY"&&p>=t.entry){t.state="ENTRY HIT";saveTrade(t);updateTradeUI(t);notify("ENTRY HIT",`${t.contract} at ₹${fmt(p)} • SL ₹${fmt(t.sl)}`)}
if((t.state==="ENTRY HIT"||t.state==="TARGET 1 HIT")&&p<=t.sl){t.state="STOP LOSS HIT";saveTrade(t);updateTradeUI(t);notify("STOP LOSS HIT",`${t.contract} at ₹${fmt(p)}`)}
else if((t.state==="ENTRY HIT"||t.state==="TARGET 1 HIT")&&p>=t.t2){t.state="TARGET 2 HIT / CLOSED";saveTrade(t);updateTradeUI(t);notify("TARGET 2 HIT",`${t.contract} at ₹${fmt(p)}`)}
else if(t.state==="ENTRY HIT"&&p>=t.t1){t.state="TARGET 1 HIT";saveTrade(t);updateTradeUI(t);notify("TARGET 1 HIT",`${t.contract} at ₹${fmt(p)}`)}}
function render(d){$("spot").textContent=fmt(d.spot);$("signal").textContent=d.signal;$("signal").className="signal "+(d.signal==="BUY"?"buy":d.signal==="SELL"?"sell":d.signal.includes("WATCH")?"watch":"neutral");$("bias").textContent=d.bias;$("updated").textContent=`Updated ${d.updated} • ${d.data_source}`;$("marketStatus").textContent=d.market_open?"MARKET OPEN":"MARKET CLOSED";
$("confidence").textContent=d.confidence+" / 100";$("confBar").style.width=d.confidence+"%";$("reason").textContent=d.reason;$("buyAbove").textContent=fmt(d.buy_above);$("sellBelow").textContent=fmt(d.sell_below);$("rating5").textContent=d.rating5;$("rating15").textContent=d.rating15;$("rsi5").textContent=fmt(d.rsi5);$("rsi15").textContent=fmt(d.rsi15);$("ema5").textContent=`${fmt(d.ema10_5)} / ${fmt(d.ema20_5)}`;$("ema15").textContent=`${fmt(d.ema10_15)} / ${fmt(d.ema20_15)}`;$("macd5").textContent=`${fmt(d.macd_5)} / ${fmt(d.macd_signal_5)}`;$("adx5").textContent=fmt(d.adx5);$("atr5").textContent=fmt(d.atr5);$("checks").innerHTML=d.checks.map(x=>`<span class="pill ${x.ok?"ok":"bad"}">${x.ok?"✓":"✕"} ${x.label}</span>`).join("");
if(d.option){$("contract").textContent=d.option.contract;$("contract").className="contract "+(d.option.type==="CE"?"buy":"sell");$("expiry").textContent=`Expiry ${d.option.expiry} • Strike ${d.option.strike} • ${d.option.type}`;$("liquidity").textContent=`Liquidity ${d.option.liquidity} • Volume ${d.option.volume} • OI ${d.option.oi} • Spread ${fmt(d.option.spread_pct)}%`;$("optionLtp").textContent="₹"+fmt(d.option.ltp);$("bid").textContent="₹"+fmt(d.option.bid);$("ask").textContent="₹"+fmt(d.option.ask);$("entry").textContent="₹"+fmt(d.option.entry);$("sl").textContent="₹"+fmt(d.option.sl);$("t1").textContent="₹"+fmt(d.option.target1);$("t2").textContent="₹"+fmt(d.option.target2)}else{["contract","expiry","liquidity","optionLtp","bid","ask","entry","sl","t1","t2"].forEach(id=>$(id).textContent="—")}
$("dot").className="dot on";$("status").textContent="Connected • V2";$("error").textContent=d.warning||"";monitorTrade(d);updateTradeUI(readTrade())}
async function refresh(){if(busy)return;busy=true;$("dot").className="dot warn";$("status").textContent="Updating…";try{const r=await fetch("/api/signal",{cache:"no-store"});const d=await r.json();if(!r.ok||d.error)throw new Error(d.error||("HTTP "+r.status));render(d)}catch(e){$("dot").className="dot";$("status").textContent="Data unavailable";$("error").textContent=e.message}finally{busy=false}}
$("refresh").onclick=refresh;$("resetTradeBtn").onclick=clearTrade;$("notifyBtn").onclick=async()=>{notificationsEnabled=true;if("Notification"in window&&Notification.permission==="default"){try{await Notification.requestPermission()}catch(e){}}$("notifyBtn").textContent="NOTIFICATIONS / VIBRATION ENABLED";notify("NIFTY ALERTS ENABLED","Entry, SL and target alerts enabled while page is open.")};updateTradeUI(readTrade());refresh();setInterval(refresh,15000);
</script></body></html>"""

def market_open_now():
    now=datetime.now(IST)
    if now.weekday()>=5:return False
    m=now.hour*60+now.minute
    return 555<=m<=930

def tv_rating(v):
    if v>=.5:return "STRONG BUY"
    if v>=.1:return "BUY"
    if v<=-.5:return "STRONG SELL"
    if v<=-.1:return "SELL"
    return "NEUTRAL"

def fetch_tv():
    cols=["close|5","Recommend.All|5","RSI|5","EMA10|5","EMA20|5","MACD.macd|5","MACD.signal|5","ADX|5","ATR|5","high|5","low|5","Recommend.All|15","RSI|15","EMA10|15","EMA20|15"]
    payload={"symbols":{"tickers":["NSE:NIFTY"],"query":{"types":[]}},"columns":cols,"range":[0,1]}
    r=requests.post(TV_URL,json=payload,headers=TV_HEADERS,timeout=12);r.raise_for_status();j=r.json()
    if not j.get("data"):raise RuntimeError("TradingView returned no NIFTY data.")
    vals=j["data"][0]["d"]
    if len(vals)!=len(cols):raise RuntimeError("TradingView returned incomplete indicator data.")
    x=dict(zip(cols,vals))
    def f(k,d=0):
        try:return float(x[k]) if x[k] is not None else float(d)
        except:return float(d)
    spot=f("close|5")
    return {"spot":spot,"rec5":f("Recommend.All|5"),"rsi5":f("RSI|5",50),"ema10_5":f("EMA10|5",spot),"ema20_5":f("EMA20|5",spot),"macd5":f("MACD.macd|5"),"macds5":f("MACD.signal|5"),"adx5":f("ADX|5"),"atr5":max(f("ATR|5",1),.01),"high5":f("high|5",spot),"low5":f("low|5",spot),"rec15":f("Recommend.All|15"),"rsi15":f("RSI|15",50),"ema10_15":f("EMA10|15",spot),"ema20_15":f("EMA20|15",spot)}

def score(tv,bull=True):
    s=0;checks=[]
    def add(label,ok,pts):
        nonlocal s
        checks.append({"label":label,"ok":bool(ok)})
        if ok:s+=pts
    add("5m rating confirms",tv["rec5"]>=.1 if bull else tv["rec5"]<=-.1,20)
    add("15m rating confirms",tv["rec15"]>=.1 if bull else tv["rec15"]<=-.1,20)
    add("5m EMA trend",tv["ema10_5"]>tv["ema20_5"] if bull else tv["ema10_5"]<tv["ema20_5"],15)
    add("15m EMA trend",tv["ema10_15"]>tv["ema20_15"] if bull else tv["ema10_15"]<tv["ema20_15"],15)
    add("RSI zone",(52<=tv["rsi5"]<=75) if bull else (25<=tv["rsi5"]<=48),10)
    add("MACD confirms",tv["macd5"]>tv["macds5"] if bull else tv["macd5"]<tv["macds5"],10)
    add("ADX ≥ 20",tv["adx5"]>=20,10)
    return s,checks

def fetch_oc():
    """
    NSE retired the old /api/option-chain-indices endpoint.
    Current flow:
      1) seed NSE cookies
      2) fetch available expiries from option-chain-contract-info
      3) fetch the nearest-expiry chain from option-chain-v3
    """
    s=requests.Session()
    s.headers.update(NSE_HEADERS)

    # Seed cookies used by NSE market-data endpoints.
    s.get(NSE_HOME,timeout=10)
    s.get(NSE_OC_PAGE,timeout=10)

    # Resolve the nearest valid NIFTY expiry.
    ci=s.get(
        NSE_OC_CONTRACT,
        params={"symbol":"NIFTY"},
        timeout=12
    )
    ci.raise_for_status()
    info=ci.json()
    expiries=info.get("expiryDates",[]) or info.get("records",{}).get("expiryDates",[])
    if not expiries:
        raise RuntimeError("NSE returned no NIFTY expiry dates.")

    ex=expiry(expiries)

    # Fetch the current v3 option chain for that expiry.
    r=s.get(
        NSE_OC_V3,
        params={"type":"Indices","symbol":"NIFTY","expiry":ex},
        timeout=12
    )

    # One clean retry with fresh NSE cookies for 401/403.
    if r.status_code in (401,403):
        s=requests.Session()
        s.headers.update(NSE_HEADERS)
        s.get(NSE_HOME,timeout=10)
        s.get(NSE_OC_PAGE,timeout=10)
        r=s.get(
            NSE_OC_V3,
            params={"type":"Indices","symbol":"NIFTY","expiry":ex},
            timeout=12
        )

    r.raise_for_status()
    j=r.json()
    if not j.get("records",{}).get("data"):
        raise RuntimeError("NSE v3 option chain returned no contracts.")
    return j

def pexp(x):
    for f in ("%d-%b-%Y","%d-%b-%y"):
        try:return datetime.strptime(x,f).date()
        except:pass

def expiry(xs):
    today=datetime.now(IST).date();p=[(pexp(x),x) for x in xs];p=[x for x in p if x[0]];f=[x for x in p if x[0]>=today];u=f or p
    if not u:raise RuntimeError("No usable expiry.")
    return sorted(u)[0][1]

def choose_option(oc,spot,bull):
    rec=oc["records"];ex=expiry(rec.get("expiryDates",[]));typ="CE" if bull else "PE";atm=round(spot/50)*50;c=[]
    for row in rec["data"]:
        # option-chain-v3 is already filtered by expiry and some NSE responses
        # omit expiryDate on each strike row. Only reject a row when an
        # expiryDate is actually present and is different from the selected one.
        row_exp=row.get("expiryDate")
        if (row_exp and row_exp!=ex) or not row.get(typ):continue
        side=row[typ];st=float(row.get("strikePrice",0) or 0)
        if abs(st-atm)>150:continue
        l=float(side.get("lastPrice",0) or 0);b=float(side.get("bidprice",side.get("bidPrice",0)) or 0);a=float(side.get("askPrice",side.get("askprice",0)) or 0);v=int(side.get("totalTradedVolume",0) or 0);oi=int(side.get("openInterest",0) or 0);mid=(a+b)/2 if a>0 and b>0 else l;sp=((a-b)/mid*100) if mid>0 and a>=b and b>0 else 99
        rank=abs(st-atm)/50*8+min(sp,30)*2-min(math.log10(v+1),5)*4-min(math.log10(oi+1),6)*3;c.append((rank,st,l,b,a,v,oi,sp))
    if not c:raise RuntimeError("No nearby liquid option found.")
    _,st,l,b,a,v,oi,sp=sorted(c)[0];entry=a if a>0 else l
    if entry<=0:raise RuntimeError("No usable option premium.")
    spread=max(a-b,0) if a>0 and b>0 else 0;risk=min(max(entry*.18,spread*3),entry*.25)
    liq="GOOD" if sp<=5 and v>=1000 else "FAIR" if sp<=10 and v>=100 else "WEAK"
    return {"contract":f"NIFTY {int(st)} {typ}","expiry":ex,"strike":st,"type":typ,"ltp":round(l,2),"bid":round(b,2),"ask":round(a,2),"entry":round(entry,2),"sl":round(max(entry-risk,.05),2),"target1":round(entry+1.5*risk,2),"target2":round(entry+2.5*risk,2),"volume":v,"oi":oi,"spread_pct":round(sp,2),"liquidity":liq}

def build_signal():
    tv=fetch_tv();bs,bc=score(tv,True);ss,sc=score(tv,False)
    bull=bs>=ss;conf=bs if bull else ss;checks=bc if bull else sc;diff=abs(bs-ss)
    if conf>=70 and diff>=15:signal="BUY" if bull else "SELL";bias="BULLISH" if bull else "BEARISH"
    elif conf>=55 and diff>=10:signal="BUY WATCH" if bull else "SELL WATCH";bias="BULLISH WATCH" if bull else "BEARISH WATCH"
    else:signal="NO TRADE";bias="MIXED / LOW CONFIDENCE"
    active=signal!="NO TRADE";opt=None;warn=""
    if active:
        try:
            opt=choose_option(fetch_oc(),tv["spot"],bull)
            if opt["liquidity"]=="WEAK" and signal in ("BUY","SELL"):signal+=" WATCH";bias+=" • WEAK OPTION LIQUIDITY"
        except Exception as e:warn="Signal available, option chain failed: "+str(e)
    return {"spot":round(tv["spot"],2),"signal":signal,"bias":bias,"confidence":conf,"reason":f"{conf}/100 • 5m {tv_rating(tv['rec5'])} • 15m {tv_rating(tv['rec15'])} • ADX {tv['adx5']:.1f}","rating5":tv_rating(tv["rec5"]),"rating15":tv_rating(tv["rec15"]),"rsi5":tv["rsi5"],"rsi15":tv["rsi15"],"ema10_5":tv["ema10_5"],"ema20_5":tv["ema20_5"],"ema10_15":tv["ema10_15"],"ema20_15":tv["ema20_15"],"macd_5":tv["macd5"],"macd_signal_5":tv["macds5"],"adx5":tv["adx5"],"atr5":tv["atr5"],"buy_above":round(tv["high5"]+.1*tv["atr5"],2),"sell_below":round(tv["low5"]-.1*tv["atr5"],2),"checks":checks,"option":opt,"market_open":market_open_now(),"data_source":"TradingView + NSE","warning":warn,"updated":datetime.now(IST).strftime("%d-%b %I:%M:%S %p")}

@app.route("/",methods=["GET"])
@app.route("/<path:p>",methods=["GET"])
def home(p=""):
    if p=="api/signal":return api_signal()
    if p=="health":return jsonify({"ok":True})
    return PAGE,200,{"Content-Type":"text/html; charset=utf-8"}

@app.route("/api/signal",methods=["GET"])
def api_signal():
    now=time.time()
    if _cache["signal"] is not None and now-_cache["ts"]<12:return jsonify(_cache["signal"])
    try:
        r=build_signal();_cache["signal"]=r;_cache["ts"]=now;return jsonify(r)
    except Exception as e:
        if _cache["signal"] is not None:
            x=dict(_cache["signal"]);x["warning"]="Using cached data: "+str(e);return jsonify(x)
        return jsonify({"error":str(e)}),503

if __name__=="__main__":
    import os
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT","10000")))
