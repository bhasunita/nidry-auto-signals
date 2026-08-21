
from flask import Flask, jsonify, request
import requests, time, math
from datetime import datetime
from zoneinfo import ZoneInfo

app = Flask(__name__)

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True, "version": "2.9"})

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
_trigger_state={"direction":None,"level":None,"count":0,"confirmed":False,"misses":0,"started_at":None}
_locked_quote_cache={}

PAGE=r"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#07111f"><title>NIFTY Professional V2.9</title>
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
<h1>📈 NIFTY Professional Signals V2.9</h1><div class="sub">5m + 15m confirmation • confidence scoring • option liquidity • trade alerts</div>
<div class="banner">Decision-support only. Public feeds may be delayed. Verify contract and premium in your broker before any real order.</div>

<div class="card"><div class="status"><span id="dot" class="dot"></span><span id="status">Starting…</span><span id="marketStatus" style="margin-left:auto">—</span></div>
<div class="price" id="spot">—</div><div id="signal" class="signal neutral">WAITING</div><div class="small" id="bias">Loading market data…</div><div class="small" id="updated">—</div><div id="error"></div></div>

<div class="card"><div class="small" id="marketViewTitle">Current market view</div><div id="marketViewSignal" style="font-size:18px;font-weight:850;margin-top:3px">—</div><div class="small">Signal confidence</div><div id="confidence" style="font-size:28px;font-weight:900">—</div><div class="progress"><div class="bar" id="confBar"></div></div><div class="small" id="reason">—</div></div>

<div class="card"><div class="small">Automatically selected option</div><div id="contract" class="contract">—</div><div class="small" id="expiry">—</div><div class="small" id="liquidity">—</div><div style="height:10px"></div>
<div class="row3"><div class="kpi"><div class="t">OPTION LTP</div><div class="v" id="optionLtp">—</div></div><div class="kpi"><div class="t">BID</div><div class="v" id="bid">—</div></div><div class="kpi"><div class="t">ASK / ENTRY</div><div class="v" id="ask">—</div></div></div></div>

<div class="row"><div class="kpi"><div class="t">ENTRY</div><div class="v" id="entry">—</div></div><div class="kpi"><div class="t">STOP LOSS</div><div class="v" id="sl">—</div></div></div>
<div style="height:8px"></div><div class="row"><div class="kpi"><div class="t">TARGET 1</div><div class="v" id="t1">—</div></div><div class="kpi"><div class="t">TARGET 2 / EXIT</div><div class="v" id="t2">—</div></div></div>

<div class="card"><div class="small">Trade monitor</div><div id="tradeState" class="state">NO ACTIVE TRADE</div><div class="small" id="tradeDetail">Alerts work while this page stays open.</div>
<button class="secondary" id="notifyBtn">ENABLE NOTIFICATIONS / VIBRATION</button><button class="danger" id="resetTradeBtn">RESET TRADE MONITOR</button></div>

<div class="card"><div class="small" id="indicatorSectionTitle" style="margin-bottom:8px">Current market indicators</div><div class="row3">
<div><div class="small">Rating 5m</div><b id="rating5">—</b></div><div><div class="small">Rating 15m</div><b id="rating15">—</b></div>
<div><div class="small">RSI 5m</div><b id="rsi5">—</b></div><div><div class="small">RSI 15m</div><b id="rsi15">—</b></div>
<div><div class="small">EMA 10/20 5m</div><b id="ema5">—</b></div><div><div class="small">EMA 10/20 15m</div><b id="ema15">—</b></div>
<div><div class="small">MACD 5m</div><b id="macd5">—</b></div><div><div class="small">ADX 5m</div><b id="adx5">—</b></div>
<div><div class="small">ATR 5m</div><b id="atr5">—</b></div></div><div style="height:10px"></div><div id="checks"></div></div>

<div class="card"><div class="small" id="triggerSectionTitle" style="margin-bottom:8px">Current scanner trigger levels</div><div class="row"><div class="kpi"><div class="t">NIFTY BUY ABOVE</div><div class="v" id="buyAbove">—</div></div><div class="kpi"><div class="t">NIFTY SELL BELOW</div><div class="v" id="sellBelow">—</div></div></div></div>
<button id="refresh">REFRESH NOW</button></div>

<script>
"use strict";const $=id=>document.getElementById(id);const fmt=x=>(x==null||!Number.isFinite(Number(x)))?"—":Number(x).toLocaleString("en-IN",{minimumFractionDigits:2,maximumFractionDigits:2});let busy=false;
if(!localStorage.getItem("niftyV29Notify")){
 const oldNotify=localStorage.getItem("niftyV28Notify")||localStorage.getItem("niftyV27Notify")||localStorage.getItem("niftyV26Notify");
 if(oldNotify==="1")localStorage.setItem("niftyV29Notify","1");
}
let notificationsEnabled=localStorage.getItem("niftyV29Notify")==="1";
function notify(title,body,kind="info"){
if(!notificationsEnabled)return;
const patterns={setup:[120,70,120],entry:[240,80,240],target1:[120,60,120,60,220],target2:[180,60,180,60,320],stop:[500,120,500]};
if(navigator.vibrate)navigator.vibrate(patterns[kind]||[180,80,180]);
try{
 const c=new(window.AudioContext||window.webkitAudioContext)(),o=c.createOscillator(),g=c.createGain();
 const hz=kind==="stop"?320:kind==="target2"?1100:kind==="target1"?940:kind==="entry"?760:620;
 o.frequency.value=hz;g.gain.value=.07;o.connect(g);g.connect(c.destination);o.start();o.stop(c.currentTime+.30)
}catch(e){}
if("Notification"in window&&Notification.permission==="granted"){
 try{new Notification(title,{body,tag:"nifty-"+kind,renotify:true})}catch(e){}
}}

function readTrade(){
try{
 let raw=localStorage.getItem("niftyV29Trade");
 if(!raw){
   const old=localStorage.getItem("niftyV28Trade")||localStorage.getItem("niftyV27Trade")||localStorage.getItem("niftyV26Trade");
   if(old){localStorage.setItem("niftyV29Trade",old);raw=old}
 }
 let t=JSON.parse(raw||"null");
 if(t){
   // V2.8: once the NIFTY trigger has confirmed and the option ask was locked,
   // treat that locked ask as the monitoring reference immediately.
   if(t.state==="ORDER READY / WAITING FOR ENTRY" || t.state==="ENTRY / ORDER PRICE HIT"){
     t.state="ENTRY LOCKED / MONITORING";
     t.entryLocked=true;
     localStorage.setItem("niftyV29Trade",JSON.stringify(t));
   }
 }
 return t
}catch(e){return null}
}
function saveTrade(t){localStorage.setItem("niftyV29Trade",JSON.stringify(t))}
function clearTrade(){localStorage.removeItem("niftyV29Trade");localStorage.removeItem("niftyV28Trade");localStorage.removeItem("niftyV27Trade");localStorage.removeItem("niftyV26Trade");updateTradeUI(null)}

function isClosedTrade(t){
 return !!t && (t.state==="STOP LOSS HIT / CLOSED" || t.state==="TARGET 2 HIT / CLOSED");
}

function updateTradeUI(t){
 if(!t){
   $("tradeState").textContent="NO ACTIVE TRADE";
   $("tradeDetail").textContent="Alerts work while this page stays open.";
   return
 }
 $("tradeState").textContent=t.state;
 const cp=Number(t.currentLtp||0);
 const pnl=(cp>0&&Number(t.entry)>0)?((cp-Number(t.entry))/Number(t.entry))*100:null;
 const cpText=cp>0?` • Current ₹${fmt(cp)}`:"";
 const pnlText=pnl!==null?` • Ref P/L ${pnl>=0?"+":""}${pnl.toFixed(1)}%`:"";
 $("tradeDetail").textContent=`${t.contract} • Locked Entry ₹${fmt(t.entry)}${cpText}${pnlText} • SL ₹${fmt(t.sl)} • T1 ₹${fmt(t.t1)} • T2 ₹${fmt(t.t2)}`
}

function renderLockedTrade(t,q=null){
 // While a trade exists, the option/risk cards show ONLY the locked trade.
 $("signal").textContent=isClosedTrade(t)?"TRADE CLOSED":"ACTIVE TRADE";
 $("signal").className="signal "+(isClosedTrade(t)?"neutral":"buy");
 $("bias").textContent=isClosedTrade(t)?"LOCKED TRADE COMPLETE • RESET TO SCAN AGAIN":"LOCKED TRADE • SL/TARGET MONITORING • CURRENT MARKET VIEW SHOWN SEPARATELY";
 $("contract").textContent=t.contract;
 $("contract").className="contract "+(t.type==="PE"?"sell":"buy");
 $("expiry").textContent=`Locked ${t.expiry||""} • Strike ${t.strike||""} • ${t.type||""}`;
 $("liquidity").textContent=q
   ?`LOCKED CONTRACT • ${q.stale?"STALE/FALLBACK":"LIVE"} • Volume ${q.volume||0} • OI ${q.oi||0} • BidQty ${q.bid_qty||0} • AskQty ${q.ask_qty||0}${q.spread_pct!=null?` • Spread ${fmt(q.spread_pct)}%`:""}${q.quote_time?` • ${q.quote_time}`:""}`
   :"LOCKED CONTRACT • waiting for live quote";
 const ltp=q&&Number(q.ltp)>0?Number(q.ltp):Number(t.currentLtp||0);
 $("optionLtp").textContent=ltp>0?"₹"+fmt(ltp):"—";
 $("bid").textContent=q&&Number(q.bid)>0?"₹"+fmt(q.bid):"—";
 $("ask").textContent=q&&Number(q.ask)>0?"₹"+fmt(q.ask):"—";
 $("entry").textContent="₹"+fmt(t.entry);
 $("sl").textContent="₹"+fmt(t.sl);
 $("t1").textContent="₹"+fmt(t.t1);
 $("t2").textContent="₹"+fmt(t.t2);
}

function applyTradePrice(t,p){
 if(!Number.isFinite(p)||p<=0)return t;
 t.currentLtp=p;

 // V2.8 monitors the locked setup immediately after NIFTY trigger confirmation.
 // The locked ask is the reference entry; it is not a future stop-entry condition.
 if(t.state==="ORDER READY / WAITING FOR ENTRY" || t.state==="ENTRY / ORDER PRICE HIT"){
   t.state="ENTRY LOCKED / MONITORING";
   t.entryLocked=true;
 }

 if((t.state==="ENTRY LOCKED / MONITORING"||t.state==="TARGET 1 HIT") && p<=t.sl){
   t.state="STOP LOSS HIT / CLOSED";
   notify("🛑 STOP LOSS HIT",`${t.contract} • LTP ₹${fmt(p)} • Locked SL ₹${fmt(t.sl)}`,"stop");
 }
 else if((t.state==="ENTRY LOCKED / MONITORING"||t.state==="TARGET 1 HIT") && p>=t.t2){
   t.state="TARGET 2 HIT / CLOSED";
   notify("🏆 TARGET 2 HIT",`${t.contract} • LTP ₹${fmt(p)} • Locked final target ₹${fmt(t.t2)}`,"target2");
 }
 else if(t.state==="ENTRY LOCKED / MONITORING" && p>=t.t1){
   t.state="TARGET 1 HIT";
   notify("✅ TARGET 1 HIT",`${t.contract} • LTP ₹${fmt(p)} • Locked T1 ₹${fmt(t.t1)} • Next ₹${fmt(t.t2)}`,"target1");
 }
 saveTrade(t);
 return t;
}

async function refreshLockedTrade(){
 let t=readTrade();
 if(!t)return;
 try{
   const u=`/api/locked-option?strike=${encodeURIComponent(t.strike)}&type=${encodeURIComponent(t.type)}&expiry=${encodeURIComponent(t.expiry||"")}`;
   const r=await fetch(u,{cache:"no-store"}),q=await r.json();
   if(!r.ok||q.error)throw new Error(q.error||("HTTP "+r.status));
   t=applyTradePrice(t,Number(q.ltp));
   updateTradeUI(t);
   renderLockedTrade(t,q);
   $("error").textContent=q.stale?"Locked trade using last good NSE quote temporarily.":"";
 }catch(e){
   updateTradeUI(t);
   renderLockedTrade(t,null);
   $("error").textContent="Locked trade quote: "+e.message;
 }
}

function monitorTrade(d){
 let t=readTrade();

 // Existing trade always wins over new scanner output.
 if(t){
   updateTradeUI(t);
   renderLockedTrade(t,null);
   refreshLockedTrade();
   return;
 }

 const active=Boolean(d.option&&d.execution_ready&&d.trigger_confirmed&&d.option.tradable);
 if(!active)return;

 // First confirmed setup: lock contract, exact ask entry and all risk levels.
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
   state:"ENTRY LOCKED / MONITORING",
   locked:true,
   entryLocked:true,
   lockedAt:d.updated
 };
 saveTrade(t);
 updateTradeUI(t);
 renderLockedTrade(t,d.option);
 notify("✅ ENTRY REFERENCE LOCKED",`${t.contract} • Entry ref ₹${fmt(t.entry)} • SL ₹${fmt(t.sl)} • T1 ₹${fmt(t.t1)} • T2 ₹${fmt(t.t2)} • monitoring started`,"entry");

 // Start monitoring SL/targets immediately from the confirmation refresh.
 t=applyTradePrice(t,Number(d.option.ltp));
 updateTradeUI(t);
 renderLockedTrade(t,d.option);
}

function render(d){
 $("spot").textContent=fmt(d.spot);
 $("updated").textContent=`Updated ${d.updated} • ${d.data_source}`;
 $("marketStatus").textContent=d.market_open?"MARKET OPEN":"MARKET CLOSED";
 $("confidence").textContent=d.confidence+" / 100";$("confBar").style.width=d.confidence+"%";
 $("marketViewSignal").textContent=`${d.signal} • 5m ${d.rating5} • 15m ${d.rating15}`;
 $("marketViewSignal").className="small";
 $("reason").textContent=d.reason;
 $("buyAbove").textContent=fmt(d.buy_above);$("sellBelow").textContent=fmt(d.sell_below);
 $("rating5").textContent=d.rating5;$("rating15").textContent=d.rating15;
 $("rsi5").textContent=fmt(d.rsi5);$("rsi15").textContent=fmt(d.rsi15);
 $("ema5").textContent=`${fmt(d.ema10_5)} / ${fmt(d.ema20_5)}`;
 $("ema15").textContent=`${fmt(d.ema10_15)} / ${fmt(d.ema20_15)}`;
 $("macd5").textContent=`${fmt(d.macd_5)} / ${fmt(d.macd_signal_5)}`;
 $("adx5").textContent=fmt(d.adx5);$("atr5").textContent=fmt(d.atr5);
 $("checks").innerHTML=d.checks.map(x=>`<span class="pill ${x.ok?"ok":"bad"}">${x.ok?"✓":"✕"} ${x.label}</span>`).join("");
 $("dot").className="dot on";$("status").textContent="Connected • V2.9";$("error").textContent=d.warning||"";

 const t=readTrade();
 if(t){
   // V2.9: locked trade and current scanner are intentionally shown as separate concepts.
   $("marketViewTitle").textContent="CURRENT MARKET VIEW — informational only";
   $("indicatorSectionTitle").textContent="CURRENT MARKET INDICATORS — do not overwrite locked trade";
   $("triggerSectionTitle").textContent="CURRENT SCANNER LEVELS — next setup only";
   renderLockedTrade(t,null);
   monitorTrade(d);
   return;
 }else{
   $("marketViewTitle").textContent="Current market view";
   $("indicatorSectionTitle").textContent="Current market indicators";
   $("triggerSectionTitle").textContent="Current scanner trigger levels";
 }

 $("signal").textContent=d.signal;
 $("signal").className="signal "+(d.signal==="BUY"?"buy":d.signal==="SELL"?"sell":d.signal.includes("WATCH")?"watch":"neutral");
 $("bias").textContent=d.bias;

 if(d.option){
   $("contract").textContent=d.option.contract;
   $("contract").className="contract "+(d.option.type==="CE"?"buy":"sell");
   $("expiry").textContent=`Expiry ${d.option.expiry} • Strike ${d.option.strike} • ${d.option.type}`;
   $("liquidity").textContent=`Liquidity ${d.option.liquidity} • Volume ${d.option.volume} • OI ${d.option.oi} • BidQty ${d.option.bid_qty||0} • AskQty ${d.option.ask_qty||0} • Spread ${fmt(d.option.spread_pct)}%`;
   $("optionLtp").textContent="₹"+fmt(d.option.ltp);$("bid").textContent="₹"+fmt(d.option.bid);$("ask").textContent="₹"+fmt(d.option.ask);
   $("entry").textContent="₹"+fmt(d.option.entry);$("sl").textContent="₹"+fmt(d.option.sl);$("t1").textContent="₹"+fmt(d.option.target1);$("t2").textContent="₹"+fmt(d.option.target2)
 }else{
   ["contract","expiry","liquidity","optionLtp","bid","ask","entry","sl","t1","t2"].forEach(id=>$(id).textContent="—")
 }
 monitorTrade(d);
 updateTradeUI(readTrade());
}

async function refresh(){if(busy)return;busy=true;$("dot").className="dot warn";$("status").textContent="Updating…";try{const r=await fetch("/api/signal",{cache:"no-store"});const d=await r.json();if(!r.ok||d.error)throw new Error(d.error||("HTTP "+r.status));render(d)}catch(e){$("dot").className="dot";$("status").textContent="Data unavailable";$("error").textContent=e.message}finally{busy=false}}
$("refresh").onclick=refresh;$("resetTradeBtn").onclick=()=>{clearTrade();refresh()};$("notifyBtn").onclick=async()=>{notificationsEnabled=true;localStorage.setItem("niftyV29Notify","1");if("Notification"in window&&Notification.permission==="default"){try{await Notification.requestPermission()}catch(e){}}$("notifyBtn").textContent="NOTIFICATIONS / VIBRATION ENABLED";notify("NIFTY ALERTS ENABLED","Frozen-trigger confirmation, order price, stop loss, Target 1 and Target 2 alerts are enabled while this page stays open.","setup")};if(notificationsEnabled)$("notifyBtn").textContent="NOTIFICATIONS / VIBRATION ENABLED";updateTradeUI(readTrade());refresh();setInterval(refresh,15000);setInterval(refreshLockedTrade,15000);
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
    rec=oc["records"];ex=expiry(rec.get("expiryDates",[]));typ="CE" if bull else "PE"
    atm=round(spot/50)*50;c=[];seen=[]

    def num(d,*keys):
        for k in keys:
            v=d.get(k)
            if v not in (None,"","-"):
                try:return float(str(v).replace(",",""))
                except:pass
        return 0.0

    for row in rec["data"]:
        row_exp=row.get("expiryDate")
        if (row_exp and row_exp!=ex) or not row.get(typ):continue
        side=row[typ];st=float(row.get("strikePrice",0) or 0)

        # Search nearby strikes, but allow enough room to find a genuinely liquid quote.
        if abs(st-atm)>400:continue

        l=num(side,"lastPrice","ltp","last_price")
        # NSE option-chain-v3 currently exposes market depth as buyPrice1 / sellPrice1.
        # Keep older aliases too so the app survives minor NSE schema changes.
        b=num(side,"buyPrice1","bidPrice","bidprice","bid","bestBid")
        a=num(side,"sellPrice1","askPrice","askprice","ask","bestAsk")
        bq=num(side,"buyQuantity1","bidQty","bidQuantity","bestBidQty")
        aq=num(side,"sellQuantity1","askQty","askQuantity","bestAskQty")
        v=int(num(side,"totalTradedVolume","volume"))
        oi=int(num(side,"openInterest","oi"))

        seen.append((abs(st-atm),st,l,b,a,v,oi,bq,aq))

        # A real tradable quote must have positive two-sided market depth.
        if l<=0 or b<=0 or a<=0 or a<b:continue

        mid=(a+b)/2
        sp=(a-b)/mid*100 if mid>0 else 999

        # Keep the gate conservative but realistic for near-ATM NIFTY options.
        if sp>15 or v<100 or oi<=0:continue

        # Prefer ATM, tighter spread, stronger volume/OI, and actual quote size.
        depth_bonus=min(math.log10(max(bq+aq,1)+1),5)
        rank=(abs(st-atm)/50)*6 + sp*3 - min(math.log10(v+1),6)*4 - min(math.log10(oi+1),7)*3 - depth_bonus*2
        c.append((rank,st,l,b,a,v,oi,sp,bq,aq))

    if not c:
        # Helpful diagnostic without exposing huge raw NSE payloads.
        nearby=sorted(seen)[:5]
        detail="; ".join(
            f"{int(st)} {typ}: LTP {l:.2f}, bid {b:.2f}, ask {a:.2f}, vol {v}, OI {oi}"
            for _,st,l,b,a,v,oi,_,_ in nearby
        )
        raise RuntimeError("No nearby option passed liquidity checks. NSE quotes seen: "+detail)

    _,st,l,b,a,v,oi,sp,bq,aq=sorted(c)[0]
    entry=a
    spread=a-b
    risk=min(max(entry*.18,spread*2),entry*.25)
    liq="GOOD" if sp<=5 and v>=1000 and bq>0 and aq>0 else "FAIR"

    return {
        "contract":f"NIFTY {int(st)} {typ}","expiry":ex,"strike":st,"type":typ,
        "ltp":round(l,2),"bid":round(b,2),"ask":round(a,2),"entry":round(entry,2),
        "sl":round(max(entry-risk,.05),2),"target1":round(entry+1.5*risk,2),
        "target2":round(entry+2.5*risk,2),"volume":v,"oi":oi,
        "bid_qty":int(bq),"ask_qty":int(aq),
        "spread_pct":round(sp,2),"liquidity":liq,"tradable":True
    }


def confirm_trigger(direction,spot,atr,setup_active):
    """
    V2.5 frozen WATCH trigger:
    - Freeze the breakout level as soon as BUY WATCH / SELL WATCH (or stronger) appears.
    - Do not chase spot on each refresh.
    - Require 2 consecutive refreshes beyond that same frozen level.
    - Allow up to 2 temporary NO TRADE refreshes before invalidating the setup.
    - Opposite directional setup immediately replaces the old frozen trigger.
    """
    global _trigger_state

    def fresh_state():
        return {
            "direction":None,"level":None,"count":0,"confirmed":False,
            "misses":0,"started_at":None
        }

    if direction not in ("BUY","SELL"):
        _trigger_state=fresh_state()
        return None,0,False,False

    # If the directional setup temporarily weakens, keep the frozen level briefly
    # instead of moving it with price or deleting it immediately.
    if not setup_active:
        if _trigger_state["direction"]==direction and _trigger_state["level"] is not None:
            _trigger_state["misses"]+=1
            _trigger_state["count"]=0
            if _trigger_state["misses"]<=2:
                return float(_trigger_state["level"]),0,bool(_trigger_state["confirmed"]),True
        _trigger_state=fresh_state()
        return None,0,False,False

    # New setup or opposite direction -> freeze a new trigger once.
    if _trigger_state["direction"]!=direction or _trigger_state["level"] is None:
        buffer=max(float(atr)*0.15,5.0)
        level=spot+buffer if direction=="BUY" else spot-buffer
        _trigger_state={
            "direction":direction,
            "level":round(level,2),
            "count":0,
            "confirmed":False,
            "misses":0,
            "started_at":datetime.now(IST).strftime("%H:%M:%S")
        }
    else:
        _trigger_state["misses"]=0

    level=float(_trigger_state["level"])
    beyond=(spot>=level) if direction=="BUY" else (spot<=level)

    if _trigger_state["confirmed"]:
        return level,2,True,True

    if beyond:
        _trigger_state["count"]+=1
    else:
        _trigger_state["count"]=0

    if _trigger_state["count"]>=2:
        _trigger_state["confirmed"]=True

    return level,min(_trigger_state["count"],2),bool(_trigger_state["confirmed"]),True




def exact_option_quote(strike,opt_type,expiry_date):
    """
    Return a live quote for the exact locked contract.

    V2.7 fixes:
    - NSE v3 may omit expiryDate at the row level; check the CE/PE object too.
    - Match strike/type first and only reject an expiry when NSE actually supplies one.
    - Short last-good cache prevents one transient NSE response from breaking monitoring.
    """
    global _locked_quote_cache

    strike=float(strike);opt_type=str(opt_type).upper()
    cache_key=f"{int(strike)}-{opt_type}-{expiry_date or ''}"

    def num(d,*keys):
        for k in keys:
            v=d.get(k)
            if v not in (None,"","-"):
                try:return float(str(v).replace(",",""))
                except:pass
        return 0.0

    try:
        oc=fetch_oc();rec=oc["records"]
        fallback=None

        for row in rec.get("data",[]):
            try:
                row_strike=float(row.get("strikePrice",0) or 0)
            except:
                continue
            if row_strike!=strike:
                continue

            side=row.get(opt_type)
            if not side:
                continue

            # Some NSE v3 payloads do not put expiryDate on the row.
            row_exp = row.get("expiryDate") or side.get("expiryDate") or ""
            if expiry_date and row_exp and row_exp != expiry_date:
                # Keep same-strike/type as a fallback only; nearest-expiry fetch
                # should normally already be the locked expiry.
                if fallback is None:
                    fallback=(row,side,row_exp)
                continue

            l=num(side,"lastPrice","ltp","last_price")
            b=num(side,"buyPrice1","bidPrice","bidprice","bid","bestBid")
            a=num(side,"sellPrice1","askPrice","askprice","ask","bestAsk")
            bq=num(side,"buyQuantity1","bidQty","bidQuantity","bestBidQty")
            aq=num(side,"sellQuantity1","askQty","askQuantity","bestAskQty")
            v=int(num(side,"totalTradedVolume","volume"))
            oi=int(num(side,"openInterest","oi"))
            mid=(a+b)/2 if a>0 and b>0 else 0
            sp=((a-b)/mid*100) if mid>0 and a>=b else None

            q={
                "contract":f"NIFTY {int(strike)} {opt_type}",
                "expiry":row_exp or expiry_date,
                "strike":strike,"type":opt_type,
                "ltp":round(l,2),"bid":round(b,2),"ask":round(a,2),
                "bid_qty":int(bq),"ask_qty":int(aq),"volume":v,"oi":oi,
                "spread_pct":round(sp,2) if sp is not None else None,
                "source":"NSE live","stale":False,
                "quote_time":datetime.now(IST).strftime("%H:%M:%S")
            }
            if q["ltp"]>0:
                _locked_quote_cache[cache_key]={"quote":q,"ts":time.time()}
                return q

        # Last-resort same strike/type if NSE omitted/changed expiry metadata.
        if fallback:
            row,side,row_exp=fallback
            l=num(side,"lastPrice","ltp","last_price")
            b=num(side,"buyPrice1","bidPrice","bidprice","bid","bestBid")
            a=num(side,"sellPrice1","askPrice","askprice","ask","bestAsk")
            bq=num(side,"buyQuantity1","bidQty","bidQuantity","bestBidQty")
            aq=num(side,"sellQuantity1","askQty","askQuantity","bestAskQty")
            v=int(num(side,"totalTradedVolume","volume"))
            oi=int(num(side,"openInterest","oi"))
            mid=(a+b)/2 if a>0 and b>0 else 0
            sp=((a-b)/mid*100) if mid>0 and a>=b else None
            q={
                "contract":f"NIFTY {int(strike)} {opt_type}",
                "expiry":row_exp or expiry_date,
                "strike":strike,"type":opt_type,
                "ltp":round(l,2),"bid":round(b,2),"ask":round(a,2),
                "bid_qty":int(bq),"ask_qty":int(aq),"volume":v,"oi":oi,
                "spread_pct":round(sp,2) if sp is not None else None,
                "source":"NSE same-strike fallback","stale":False,
                "quote_time":datetime.now(IST).strftime("%H:%M:%S")
            }
            if q["ltp"]>0:
                _locked_quote_cache[cache_key]={"quote":q,"ts":time.time()}
                return q

        raise RuntimeError("Locked option contract not present in this NSE response.")

    except Exception as e:
        # Brief NSE hiccups should not instantly stop SL/target monitoring.
        cached=_locked_quote_cache.get(cache_key)
        if cached and (time.time()-cached["ts"])<=90:
            q=dict(cached["quote"])
            q["stale"]=True
            q["source"]="Last good NSE quote"
            q["warning"]=str(e)
            return q
        raise RuntimeError("Locked option live quote unavailable: "+str(e))



def build_signal():
    tv=fetch_tv();bs,bc=score(tv,True);ss,sc=score(tv,False)
    bull=bs>=ss;conf=bs if bull else ss;checks=bc if bull else sc;diff=abs(bs-ss)

    if conf>=70 and diff>=15:
        raw_signal="BUY" if bull else "SELL";bias="BULLISH" if bull else "BEARISH"
    elif conf>=55 and diff>=10:
        raw_signal="BUY WATCH" if bull else "SELL WATCH";bias="BULLISH WATCH" if bull else "BEARISH WATCH"
    else:
        raw_signal="NO TRADE";bias="MIXED / LOW CONFIDENCE"

    signal=raw_signal;opt=None;warn="";execution_ready=False
    direction="BUY" if bull else "SELL"

    # V2.5 starts freezing at WATCH stage, not only after a strong BUY/SELL.
    directional_setup=raw_signal in ("BUY","BUY WATCH","SELL","SELL WATCH")
    trigger_level,confirm_count,trigger_confirmed,trigger_frozen=confirm_trigger(
        direction,tv["spot"],tv["atr5"],directional_setup
    )

    buffer=max(tv["atr5"]*.15,5.0)
    if bull:
        buy_above=round(trigger_level if trigger_level is not None else tv["spot"]+buffer,2)
        sell_below=round(tv["spot"]-buffer,2)
    else:
        buy_above=round(tv["spot"]+buffer,2)
        sell_below=round(trigger_level if trigger_level is not None else tv["spot"]-buffer,2)

    # Keep showing a valid option while a directional setup is alive.
    if directional_setup:
        try:
            opt=choose_option(fetch_oc(),tv["spot"],bull)
        except Exception as e:
            warn="Signal available, but no tradable option quote: "+str(e)

    # A trade can become ready only from a strong BUY/SELL plus frozen-trigger 2/2.
    strong=raw_signal in ("BUY","SELL")
    if strong:
        execution_ready=bool(trigger_confirmed and opt and opt.get("tradable"))
        if execution_ready:
            signal=direction
            bias=("BULLISH" if bull else "BEARISH")+" • FROZEN TRIGGER CONFIRMED 2/2"
        else:
            signal=("BUY WATCH" if bull else "SELL WATCH")
            waiting=[]
            if not trigger_confirmed:
                waiting.append(f"frozen NIFTY trigger {confirm_count}/2")
            if not opt:
                waiting.append("valid option quote")
            bias=("BULLISH WATCH" if bull else "BEARISH WATCH")+" • WAITING FOR "+(" + ".join(waiting) if waiting else "CONFIRMATION")
    elif raw_signal in ("BUY WATCH","SELL WATCH"):
        # Watch-stage setup keeps the same frozen breakout level.
        signal=raw_signal
        bias=("BULLISH WATCH" if bull else "BEARISH WATCH")+f" • FROZEN TRIGGER {confirm_count}/2"
    else:
        # NO TRADE: a recently frozen trigger may survive briefly internally,
        # but it cannot activate a trade until a valid strong directional setup returns.
        confirm_count=0
        trigger_confirmed=False

    trigger_note = (
        f"Frozen {trigger_level:.2f}" if trigger_frozen and trigger_level is not None
        else "Not frozen"
    )

    return {
        "spot":round(tv["spot"],2),"signal":signal,"bias":bias,"confidence":conf,
        "reason":f"{conf}/100 • 5m {tv_rating(tv['rec5'])} • 15m {tv_rating(tv['rec15'])} • ADX {tv['adx5']:.1f} • Trigger {confirm_count}/2 • {trigger_note}",
        "rating5":tv_rating(tv["rec5"]),"rating15":tv_rating(tv["rec15"]),
        "rsi5":tv["rsi5"],"rsi15":tv["rsi15"],"ema10_5":tv["ema10_5"],
        "ema20_5":tv["ema20_5"],"ema10_15":tv["ema10_15"],"ema20_15":tv["ema20_15"],
        "macd_5":tv["macd5"],"macd_signal_5":tv["macds5"],"adx5":tv["adx5"],
        "atr5":tv["atr5"],"buy_above":buy_above,"sell_below":sell_below,
        "checks":checks,"option":opt,"execution_ready":execution_ready,
        "trigger_hit":trigger_confirmed,"trigger_confirmed":trigger_confirmed,
        "trigger_confirmations":confirm_count,"trigger_level":trigger_level,
        "trigger_frozen":trigger_frozen,
        "trigger_started_at":_trigger_state.get("started_at"),
        "market_open":market_open_now(),"data_source":"TradingView + NSE",
        "warning":warn,"updated":datetime.now(IST).strftime("%d-%b %I:%M:%S %p")
    }

@app.route("/",methods=["GET"])
@app.route("/<path:p>",methods=["GET"])
def home(p=""):
    if p=="api/signal":return api_signal()
    if p=="health":return jsonify({"ok":True})
    return PAGE,200,{"Content-Type":"text/html; charset=utf-8"}

@app.route("/api/locked-option",methods=["GET"])
def api_locked_option():
    try:
        strike=request.args.get("strike",type=float)
        opt_type=(request.args.get("type") or "").upper()
        expiry_date=request.args.get("expiry") or ""
        if strike is None or opt_type not in ("CE","PE"):
            return jsonify({"error":"Invalid locked option parameters."}),400
        return jsonify(exact_option_quote(strike,opt_type,expiry_date))
    except Exception as e:
        return jsonify({"error":str(e)}),503



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
