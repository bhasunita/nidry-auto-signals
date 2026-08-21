
from flask import Flask, jsonify, request
import requests, time, math
from datetime import datetime
from zoneinfo import ZoneInfo

app = Flask(__name__)

@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "ok": True,
        "version": "3.7.1",
        "standalone": True,
        "cross_instrument_cache_protection": True
    })

IST = ZoneInfo("Asia/Kolkata")
INSTRUMENTS = {
    "NIFTY": {"market":"NSE","mode":"OPTION","label":"NIFTY OPTIONS","ticker":"NSE:NIFTY","short":"NIFTY","session_start":555,"session_end":930},
    "CRUDEOILM": {"market":"MCX","mode":"FUTURE","label":"CRUDEOIL MINI","ticker":"MCX:CRUDEOILM1!","short":"CRUDEOIL MINI","session_start":540,"session_end":1410,"atr_sl":1.20,"atr_t1":1.80,"atr_t2":2.70},
    "CRUDEOIL": {"market":"MCX","mode":"FUTURE","label":"CRUDEOIL","ticker":"MCX:CRUDEOIL1!","short":"CRUDEOIL","session_start":540,"session_end":1410,"atr_sl":1.20,"atr_t1":1.80,"atr_t2":2.70},
    "GOLDM": {"market":"MCX","mode":"FUTURE","label":"GOLD MINI","ticker":"MCX:GOLDM1!","short":"GOLD MINI","session_start":540,"session_end":1410,"atr_sl":1.10,"atr_t1":1.65,"atr_t2":2.50},
    "GOLD": {"market":"MCX","mode":"FUTURE","label":"GOLD","ticker":"MCX:GOLD1!","short":"GOLD","session_start":540,"session_end":1410,"atr_sl":1.10,"atr_t1":1.65,"atr_t2":2.50},
    "SILVERM": {"market":"MCX","mode":"FUTURE","label":"SILVER MINI","ticker":"MCX:SILVERM1!","short":"SILVER MINI","session_start":540,"session_end":1410,"atr_sl":1.25,"atr_t1":1.90,"atr_t2":2.85},
    "SILVER": {"market":"MCX","mode":"FUTURE","label":"SILVER","ticker":"MCX:SILVER1!","short":"SILVER","session_start":540,"session_end":1410,"atr_sl":1.25,"atr_t1":1.90,"atr_t2":2.85},
    "NATURALGAS": {"market":"MCX","mode":"FUTURE","label":"NATURAL GAS","ticker":"MCX:NATURALGAS1!","short":"NATURAL GAS","session_start":540,"session_end":1410,"atr_sl":1.30,"atr_t1":1.95,"atr_t2":2.90},
}

def instrument_config(key):
    key=(key or "NIFTY").upper().strip()
    if key not in INSTRUMENTS:
        raise ValueError(f"Unknown instrument '{key}'. NIFTY fallback disabled.")
    return key, INSTRUMENTS[key]

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
_cache={}
_trigger_states={}
_locked_quote_cache={}

PAGE='<!doctype html><html><head><meta charset="utf-8">\n<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">\n<meta name="theme-color" content="#07111f"><title>Professional Signals V3.7.1</title>\n<style>\n:root{--card:#0f1c2e;--card2:#12233a;--text:#eef5ff;--muted:#9bb0c9;--green:#22c55e;--red:#ef4444;--amber:#f59e0b;--line:#223855}\n*{box-sizing:border-box}body{margin:0;background:linear-gradient(180deg,#06101d,#0a1627);color:var(--text);font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;min-height:100vh}\n.wrap{max-width:820px;margin:auto;padding:14px 12px 46px}h1{font-size:22px;margin:4px 0}.sub,.small{color:var(--muted);font-size:12px;line-height:1.45}.sub{font-size:13px;margin-bottom:12px}\n.card{background:rgba(15,28,46,.98);border:1px solid var(--line);border-radius:18px;padding:14px;margin:10px 0}.status{display:flex;gap:8px;align-items:center;font-size:13px;color:var(--muted);flex-wrap:wrap}\n.dot{width:9px;height:9px;border-radius:50%;background:#64748b}.dot.on{background:var(--green);box-shadow:0 0 9px var(--green)}.dot.warn{background:var(--amber)}\n.price{font-size:39px;font-weight:850;margin:4px 0}.signal{font-size:28px;font-weight:900;margin-top:5px}.buy{color:var(--green)}.sell{color:var(--red)}.watch{color:var(--amber)}.neutral{color:#cbd5e1}\n.row{display:grid;grid-template-columns:1fr 1fr;gap:10px}.row3{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}@media(max-width:540px){.row3{grid-template-columns:1fr 1fr}}\n.kpi{background:var(--card2);border:1px solid var(--line);border-radius:14px;padding:11px}.kpi .t{color:var(--muted);font-size:11px;text-transform:uppercase}.kpi .v{font-size:20px;font-weight:800;margin-top:3px}\n.contract{font-size:26px;font-weight:900;margin:4px 0}.banner{background:#372b12;border:1px solid #7a5b17;border-radius:13px;padding:10px;color:#ffe4a3;font-size:12px}\n.pill{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:5px 9px;font-size:12px;margin:3px}.ok{border-color:#23673d;color:#8ef0ab}.bad{border-color:#73323a;color:#ff9fa9}\nbutton{width:100%;border:0;border-radius:12px;padding:13px;font-weight:800;font-size:15px;background:#0ea5e9;color:#00101a;margin-top:8px}.secondary{background:#1d304a;color:#eaf3ff}.danger{background:#40202a;color:#ffd9df}\n.progress{height:12px;background:#071421;border:1px solid #29415f;border-radius:999px;overflow:hidden;margin-top:7px}.bar{height:100%;width:0%;background:linear-gradient(90deg,#0ea5e9,#22c55e)}\n.state{font-size:18px;font-weight:850;margin-top:5px}.selectrow{display:grid;grid-template-columns:1fr 1fr;gap:10px}.selectrow select{width:100%;padding:11px;border-radius:12px;border:1px solid var(--line);background:var(--card2);color:var(--text);font-weight:750}.modehint{margin-top:7px;color:var(--muted);font-size:12px}#error{white-space:pre-wrap;color:#ffb4bc;font-size:12px;margin-top:8px}\n</style></head><body><div class="wrap">\n<h1>📈 Professional Signals V3.7.1</h1><div class="sub">NIFTY options + MCX futures • 5m + 15m confirmation • quality filters • trade alerts</div>\n<div class="banner">Decision-support only. Public feeds may be delayed. Verify the exact active contract, expiry, price and order in your broker before any real trade.</div>\n\n<div class="card"><div class="small">V3.7.1 MARKET / INSTRUMENT</div><div class="selectrow" style="margin-top:8px"><select id="marketSelect"><option value="NSE">NIFTY OPTIONS</option><option value="MCX">MCX COMMODITIES</option></select><select id="instrumentSelect"></select></div><div class="modehint" id="instrumentHint">NIFTY option mode uses NSE option-chain quotes.</div></div>\n\n<div class="card"><div class="status"><span id="dot" class="dot"></span><span id="status">Starting…</span><span id="marketStatus" style="margin-left:auto">—</span></div>\n<div class="price" id="spot">—</div><div id="signal" class="signal neutral">WAITING</div><div class="small" id="bias">Loading market data…</div><div class="small" id="updated">—</div><div id="error"></div></div>\n\n<div id="connectionBanner" class="card" style="display:none;border-color:#8a6a22">\n<div style="font-weight:900" id="connectionTitle">DATA CONNECTION INTERRUPTED</div>\n<div class="small" id="connectionDetail">Showing the last known data while reconnecting.</div>\n</div>\n\n<div class="card"><div class="small" id="marketViewTitle">Current market view</div><div id="marketViewSignal" style="font-size:18px;font-weight:850;margin-top:3px">—</div><div class="small">Signal confidence</div><div id="confidence" style="font-size:28px;font-weight:900">—</div><div class="progress"><div class="bar" id="confBar"></div></div><div class="small" id="reason">—</div></div>\n\n<div class="card" id="qualityCard">\n<div class="small">V3.7.1 PROFESSIONAL ENTRY QUALITY ENGINE</div>\n<div id="qualityGrade" style="font-size:26px;font-weight:900;margin-top:4px">—</div>\n<div id="qualityState" style="font-size:18px;font-weight:850;margin-top:4px">WAIT</div>\n<div class="small" id="qualitySummary">Waiting for market data…</div>\n<div class="small" id="regimeDetail" style="margin-top:4px">Market regime: —</div>\n<div style="height:8px"></div>\n<div id="qualityChecks"></div>\n</div>\n\n<div class="card"><div class="small" id="optionCardTitle">Automatically selected option</div><div id="contract" class="contract">—</div><div class="small" id="expiry">—</div><div class="small" id="liquidity">—</div><div style="height:10px"></div>\n<div class="row3"><div class="kpi"><div class="t">OPTION LTP</div><div class="v" id="optionLtp">—</div></div><div class="kpi"><div class="t">BID</div><div class="v" id="bid">—</div></div><div class="kpi"><div class="t">ASK / ENTRY</div><div class="v" id="ask">—</div></div></div></div>\n\n<div class="row"><div class="kpi"><div class="t">ENTRY</div><div class="v" id="entry">—</div></div><div class="kpi"><div class="t">STOP LOSS</div><div class="v" id="sl">—</div></div></div>\n<div style="height:8px"></div><div class="row"><div class="kpi"><div class="t">TARGET 1</div><div class="v" id="t1">—</div></div><div class="kpi"><div class="t">TARGET 2 / EXIT</div><div class="v" id="t2">—</div></div></div>\n\n<div id="reversalWarningCard" class="card" style="display:none;border-color:#9d3248;background:#25111a">\n<div class="small">LOCKED TRADE SAFETY CHECK</div>\n<div id="reversalWarningTitle" style="font-size:24px;font-weight:900;margin-top:6px;color:#ff9caf">MARKET REVERSAL / EXIT WARNING</div>\n<div id="reversalWarningDetail" class="small" style="margin-top:6px">Current market direction strongly conflicts with the locked trade.</div>\n<div class="small" style="margin-top:8px">Warning only — this does not automatically close or modify your locked trade.</div>\n</div>\n\n<div class="card" id="tradeHealthCard"><div class="small">V3.7.1 PROFESSIONAL TRADE HEALTH</div>\n<div id="tradeHealth" style="font-size:25px;font-weight:900;margin-top:4px">NO ACTIVE TRADE</div>\n<div id="tradeAction" style="font-size:17px;font-weight:850;margin-top:4px;color:#9bb0c9">Waiting for a locked trade.</div>\n<div id="tradeHealthReasons" class="small" style="margin-top:7px">Health combines locked P/L, current trend, VWAP, EMA, MACD and ADX. It never places or closes an order automatically.</div>\n</div>\n\n<div class="card" id="eodDecisionCard">\n<div class="small">V3.7.1 END-OF-DAY POSITION DECISION</div>\n<div id="eodDecision" style="font-size:25px;font-weight:900;margin-top:4px">NO ACTIVE POSITION</div>\n<div id="eodScore" class="small" style="margin-top:4px">Carry score — / 100</div>\n<div id="eodAction" style="font-size:17px;font-weight:850;margin-top:6px;color:#9bb0c9">Waiting for a locked trade.</div>\n<div id="eodReasons" class="small" style="margin-top:7px">Final carry/exit review becomes active near market close. Decision-support only; no broker order is placed.</div>\n<div id="eodWindow" class="small" style="margin-top:7px">NIFTY review window: 3:05 PM onward. MCX carry/exit timing must be verified against the selected contract/session.</div>\n</div>\n\n<div class="card"><div class="small">V3.7.1 SESSION TRADE JOURNAL</div>\n<div class="row"><div class="kpi"><div class="t">EVENTS</div><div class="v" id="journalCount">0</div></div><div class="kpi"><div class="t">LAST EVENT</div><div class="v" id="journalLast" style="font-size:14px">—</div></div></div>\n<button class="secondary" id="exportJournalBtn">COPY JOURNAL SUMMARY</button></div>\n\n<div class="card"><div class="small">Locked trade dashboard</div>\n<div class="row"><div class="kpi"><div class="t">LIVE P/L</div><div class="v" id="livePnl">—</div></div><div class="kpi"><div class="t">CURRENT LTP</div><div class="v" id="liveTradeLtp">—</div></div></div>\n<div style="height:8px"></div>\n<div class="row"><div class="kpi"><div class="t">TO STOP LOSS</div><div class="v" id="distSl">—</div></div><div class="kpi"><div class="t">TO TARGET 1</div><div class="v" id="distT1">—</div></div></div>\n<div style="height:8px"></div>\n<div class="row"><div class="kpi"><div class="t">TO TARGET 2</div><div class="v" id="distT2">—</div></div><div class="kpi"><div class="t">RISK / REWARD</div><div class="v" id="rrNow">—</div></div></div>\n<div class="small" id="tradeProgressText" style="margin-top:10px">No locked trade.</div></div>\n\n<div class="card"><div class="small">Trade monitor</div><div id="tradeState" class="state">NO ACTIVE TRADE</div><div class="small" id="tradeDetail">Alerts work while this page stays open.</div>\n<button class="secondary" id="notifyBtn">ENABLE NOTIFICATIONS / VIBRATION</button><button class="danger" id="resetTradeBtn">RESET TRADE MONITOR</button></div>\n\n<div class="card"><div class="small" id="indicatorSectionTitle" style="margin-bottom:8px">Current market indicators</div><div class="row3">\n<div><div class="small">Rating 5m</div><b id="rating5">—</b></div><div><div class="small">Rating 15m</div><b id="rating15">—</b></div>\n<div><div class="small">RSI 5m</div><b id="rsi5">—</b></div><div><div class="small">RSI 15m</div><b id="rsi15">—</b></div>\n<div><div class="small">EMA 10/20 5m</div><b id="ema5">—</b></div><div><div class="small">EMA 10/20 15m</div><b id="ema15">—</b></div>\n<div><div class="small">MACD 5m</div><b id="macd5">—</b></div><div><div class="small">ADX 5m</div><b id="adx5">—</b></div>\n<div><div class="small">ATR 5m</div><b id="atr5">—</b></div>\n<div><div class="small">ADX 15m</div><b id="adx15">—</b></div>\n<div><div class="small">VWAP 5m</div><b id="vwap5">—</b></div>\n<div><div class="small">EMA 50 5m</div><b id="ema50_5">—</b></div>\n<div><div class="small">EMA 50 15m</div><b id="ema50_15">—</b></div>\n<div><div class="small">MACD 15m</div><b id="macd15">—</b></div>\n</div><div style="height:10px"></div><div id="checks"></div></div>\n\n<div class="card"><div class="small" id="triggerSectionTitle" style="margin-bottom:8px">Current scanner trigger levels</div><div class="row"><div class="kpi"><div class="t">NIFTY BUY ABOVE</div><div class="v" id="buyAbove">—</div></div><div class="kpi"><div class="t">NIFTY SELL BELOW</div><div class="v" id="sellBelow">—</div></div></div></div>\n<button id="refresh">REFRESH NOW</button></div>\n\n<script>\n"use strict";const $=id=>document.getElementById(id);const fmt=x=>(x==null||!Number.isFinite(Number(x)))?"—":Number(x).toLocaleString("en-IN",{minimumFractionDigits:2,maximumFractionDigits:2});let busy=false;\nlet reconnectFailures=0;\nlet reconnectTimer=null;\nlet lastSignalData=null;\nconst LAST_SIGNAL_PREFIX="v371LastSignal:";\nfunction signalCacheKey(){\n  return LAST_SIGNAL_PREFIX+String(selectedInstrument||"NIFTY").toUpperCase();\n}\n\nfunction saveLastSignal(d){\n try{\n   if(!d)return;\n   const dk=String(d.instrument_key||"").toUpperCase();\n   const sk=String(selectedInstrument||"NIFTY").toUpperCase();\n   if(!dk || dk!==sk)return;\n   lastSignalData=d;\n   localStorage.setItem(signalCacheKey(),JSON.stringify({savedAt:Date.now(),instrument:sk,data:d}));\n }catch(e){}\n}\nfunction loadLastSignal(){\n try{\n   const sk=String(selectedInstrument||"NIFTY").toUpperCase();\n   const x=JSON.parse(localStorage.getItem(signalCacheKey())||"null");\n   if(x&&x.data){\n     const dk=String(x.data.instrument_key||x.instrument||"").toUpperCase();\n     if(dk===sk){lastSignalData=x.data;return x}\n   }\n }catch(e){}\n lastSignalData=null;\n return null\n}\nfunction showConnectionIssue(msg,nextSeconds=null){\n const b=$("connectionBanner");\n if(b)b.style.display="block";\n $("connectionTitle").textContent="DATA CONNECTION INTERRUPTED";\n $("connectionDetail").textContent=\n   `Current instrument only. ${msg||"Temporary server/network problem."}`+\n   (nextSeconds!==null?` Retrying in ${nextSeconds}s.`:"");\n $("dot").className="dot warn";\n $("status").textContent="Reconnecting • V3.7.1";\n}\nfunction clearConnectionIssue(){\n const b=$("connectionBanner");\n if(b)b.style.display="none";\n reconnectFailures=0;\n if(reconnectTimer){clearTimeout(reconnectTimer);reconnectTimer=null}\n}\nfunction sleep(ms){return new Promise(r=>setTimeout(r,ms))}\nasync function fetchJsonWithRetry(url,attempts=3,timeoutMs=10000){\n let lastErr=null;\n for(let i=0;i<attempts;i++){\n   const ctl=new AbortController();\n   const timer=setTimeout(()=>ctl.abort(),timeoutMs);\n   try{\n     const r=await fetch(url,{cache:"no-store",signal:ctl.signal});\n     const ct=(r.headers.get("content-type")||"").toLowerCase();\n     let body=null;\n     if(ct.includes("application/json")) body=await r.json();\n     else{\n       const raw=await r.text();\n       throw new Error(`HTTP ${r.status}${raw?" • non-JSON response":""}`);\n     }\n     if(!r.ok||body.error)throw new Error(body.error||("HTTP "+r.status));\n     clearTimeout(timer);\n     return body;\n   }catch(e){\n     clearTimeout(timer);\n     lastErr=e;\n     if(i<attempts-1)await sleep(700*(i+1));\n   }\n }\n throw lastErr||new Error("Request failed");\n}\n\n\nconst V37_INSTRUMENTS={\n NSE:[{id:"NIFTY",label:"NIFTY OPTIONS"}],\n MCX:[{id:"CRUDEOILM",label:"CRUDEOIL MINI"},{id:"CRUDEOIL",label:"CRUDEOIL"},{id:"GOLDM",label:"GOLD MINI"},{id:"GOLD",label:"GOLD"},{id:"SILVERM",label:"SILVER MINI"},{id:"SILVER",label:"SILVER"},{id:"NATURALGAS",label:"NATURAL GAS"}]\n};\nlet selectedInstrument=localStorage.getItem("v37Instrument")||"NIFTY";\nfunction setInstrumentOptions(){\n const m=$("marketSelect").value; const arr=V37_INSTRUMENTS[m]||V37_INSTRUMENTS.NSE;\n $("instrumentSelect").innerHTML=arr.map(x=>`<option value="${x.id}">${x.label}</option>`).join("");\n if(arr.some(x=>x.id===selectedInstrument)) $("instrumentSelect").value=selectedInstrument; else {selectedInstrument=arr[0].id;$("instrumentSelect").value=selectedInstrument}\n $("instrumentHint").textContent=m==="NSE"?"NIFTY option mode uses NSE option-chain quotes.":"MCX mode uses TradingView continuous-futures reference data. Verify the exact active expiry/contract in your broker.";\n}\nif(selectedInstrument!=="NIFTY")$("marketSelect").value="MCX";\nsetInstrumentOptions();\n$("marketSelect").onchange=()=>{selectedInstrument=$("marketSelect").value==="NSE"?"NIFTY":"CRUDEOILM";setInstrumentOptions();localStorage.setItem("v37Instrument",selectedInstrument);clearTrade();clearScannerDisplay("Switching instrument…");refresh()};\n$("instrumentSelect").onchange=()=>{selectedInstrument=$("instrumentSelect").value;localStorage.setItem("v37Instrument",selectedInstrument);clearTrade();clearScannerDisplay("Switching instrument…");refresh()};\n\nif(!localStorage.getItem("niftyV35Notify")){\n const oldNotify=localStorage.getItem("niftyV33Notify")||localStorage.getItem("niftyV32Notify")||localStorage.getItem("niftyV31Notify")||localStorage.getItem("niftyV30Notify")||localStorage.getItem("niftyV29Notify")||localStorage.getItem("niftyV28Notify")||localStorage.getItem("niftyV27Notify")||localStorage.getItem("niftyV26Notify");\n if(oldNotify==="1")localStorage.setItem("niftyV35Notify","1");\n}\nlet notificationsEnabled=localStorage.getItem("niftyV35Notify")==="1";\nfunction notify(title,body,kind="info"){\nif(!notificationsEnabled)return;\nconst patterns={setup:[120,70,120],entry:[240,80,240],target1:[120,60,120,60,220],target2:[180,60,180,60,320],stop:[500,120,500]};\nif(navigator.vibrate)navigator.vibrate(patterns[kind]||[180,80,180]);\ntry{\n const c=new(window.AudioContext||window.webkitAudioContext)(),o=c.createOscillator(),g=c.createGain();\n const hz=kind==="stop"?320:kind==="target2"?1100:kind==="target1"?940:kind==="entry"?760:620;\n o.frequency.value=hz;g.gain.value=.07;o.connect(g);g.connect(c.destination);o.start();o.stop(c.currentTime+.30)\n}catch(e){}\nif("Notification"in window&&Notification.permission==="granted"){\n try{new Notification(title,{body,tag:"nifty-"+kind,renotify:true})}catch(e){}\n}}\n\nfunction readTrade(){\ntry{\n let raw=localStorage.getItem("niftyV35Trade");\n if(!raw){\n   const old=localStorage.getItem("niftyV33Trade")||localStorage.getItem("niftyV32Trade")||localStorage.getItem("niftyV31Trade")||localStorage.getItem("niftyV30Trade")||localStorage.getItem("niftyV29Trade")||localStorage.getItem("niftyV28Trade")||localStorage.getItem("niftyV27Trade")||localStorage.getItem("niftyV26Trade");\n   if(old){localStorage.setItem("niftyV35Trade",old);raw=old}\n }\n let t=JSON.parse(raw||"null");\n if(t){\n   // V2.8: once the NIFTY trigger has confirmed and the option ask was locked,\n   // treat that locked ask as the monitoring reference immediately.\n   if(t.state==="ORDER READY / WAITING FOR ENTRY" || t.state==="ENTRY / ORDER PRICE HIT"){\n     t.state="ENTRY LOCKED / MONITORING";\n     t.entryLocked=true;\n     localStorage.setItem("niftyV35Trade",JSON.stringify(t));\n   }\n }\n return t\n}catch(e){return null}\n}\nfunction saveTrade(t){localStorage.setItem("niftyV35Trade",JSON.stringify(t))}\nfunction clearTrade(){localStorage.removeItem("niftyV35Trade");localStorage.removeItem("niftyV33Trade");localStorage.removeItem("niftyV32Trade");localStorage.removeItem("niftyV31Trade");localStorage.removeItem("niftyV30Trade");localStorage.removeItem("niftyV29Trade");localStorage.removeItem("niftyV28Trade");localStorage.removeItem("niftyV27Trade");localStorage.removeItem("niftyV26Trade");localStorage.removeItem("niftyV35Reversal");hideReversalWarning();updateTradeUI(null)}\n\nfunction isClosedTrade(t){\n return !!t && (t.state==="STOP LOSS HIT / CLOSED" || t.state==="TARGET 2 HIT / CLOSED");\n}\n\n\nfunction ratingSide(x){\n x=String(x||"").toUpperCase();\n if(x.includes("BUY"))return "BUY";\n if(x.includes("SELL"))return "SELL";\n return "NEUTRAL";\n}\nfunction hideReversalWarning(){\n const c=$("reversalWarningCard");\n if(c)c.style.display="none";\n}\nfunction evaluateReversalWarning(t,d){\n if(!t||isClosedTrade(t)||!d){hideReversalWarning();return false}\n\n const lockedSide=t.side?String(t.side).toUpperCase():(String(t.type||"").toUpperCase()==="PE"?"SELL":"BUY");\n const opposite=lockedSide==="BUY"?"SELL":"BUY";\n const s5=ratingSide(d.rating5), s15=ratingSide(d.rating15);\n const confidence=Number(d.confidence||0);\n const strongConflict=(s5===opposite && s15===opposite && confidence>=70);\n\n if(!strongConflict){\n   hideReversalWarning();\n   localStorage.removeItem("niftyV35Reversal");\n   return false;\n }\n\n const c=$("reversalWarningCard");\n if(c)c.style.display="block";\n $("reversalWarningTitle").textContent="MARKET REVERSAL / EXIT WARNING";\n $("reversalWarningDetail").textContent=\n   `${t.contract} is a locked ${lockedSide} trade, but current 5m and 15m ratings are both ${opposite} with confidence ${confidence}/100. Review the open trade, stop loss and exit plan.`;\n\n const key=`${t.contract}|${opposite}|${confidence>=85?"HIGH":"CONFIRMED"}`;\n const prior=localStorage.getItem("niftyV35Reversal");\n if(prior!==key){\n   localStorage.setItem("niftyV35Reversal",key);\n   notify("⚠️ MARKET REVERSAL / EXIT WARNING",\n     `${t.contract} locked ${lockedSide}; current 5m + 15m are ${opposite} (${confidence}/100). Review trade / SL. No automatic exit.`,\n     "reversal");\n }\n return true;\n}\n\nfunction updateTradeUI(t){\n const title=$("optionCardTitle");\n if(!t){\n   hideReversalWarning();\n   if(title)title.textContent="Automatically selected contract";\n   $("tradeState").textContent="NO ACTIVE TRADE";\n   $("tradeDetail").textContent="Alerts work while this page stays open.";\n   $("livePnl").textContent="—"; $("liveTradeLtp").textContent="—";\n   $("distSl").textContent="—"; $("distT1").textContent="—"; $("distT2").textContent="—";\n   $("rrNow").textContent="—"; $("tradeProgressText").textContent="No locked trade.";\n   return\n }\n if(title)title.textContent="LOCKED TRADE CONTRACT";\n $("tradeState").textContent=t.state;\n const cp=Number(t.currentLtp||0), entry=Number(t.entry||0), sl=Number(t.sl||0), t1=Number(t.t1||0), t2=Number(t.t2||0);\n const shortSide=(String(t.side||"").toUpperCase()==="SELL"||String(t.type||"").toUpperCase()==="PE"); const pnl=(cp>0&&entry>0)?((shortSide?(entry-cp):(cp-entry))/entry)*100:null;\n const cpText=cp>0?` • Current ₹${fmt(cp)}`:"";\n const pnlText=pnl!==null?` • Ref P/L ${pnl>=0?"+":""}${pnl.toFixed(1)}%`:"";\n $("tradeDetail").textContent=`${t.contract} • Locked Entry ₹${fmt(entry)}${cpText}${pnlText} • SL ₹${fmt(sl)} • T1 ₹${fmt(t1)} • T2 ₹${fmt(t2)}`;\n $("liveTradeLtp").textContent=cp>0?`₹${fmt(cp)}`:"—";\n $("livePnl").textContent=pnl!==null?`${pnl>=0?"+":""}${pnl.toFixed(1)}%`:"—";\n const dsl=(cp>0&&sl>0)?((shortSide?(sl-cp):(cp-sl))/cp*100):null;\n const dt1=(cp>0&&t1>0)?((shortSide?(cp-t1):(t1-cp))/cp*100):null;\n const dt2=(cp>0&&t2>0)?((shortSide?(cp-t2):(t2-cp))/cp*100):null;\n $("distSl").textContent=dsl!==null?`${dsl.toFixed(1)}%`:"—";\n $("distT1").textContent=dt1!==null?`${dt1.toFixed(1)}%`:"—";\n $("distT2").textContent=dt2!==null?`${dt2.toFixed(1)}%`:"—";\n const risk=(entry>0&&sl>0)?Math.abs(entry-sl):0, reward=(entry>0&&t1>0)?Math.abs(t1-entry):0;\n $("rrNow").textContent=(risk>0&&reward>0)?`1:${(reward/risk).toFixed(2)}`:"—";\n let status="Locked trade monitoring active.";\n if(t.state.includes("STOP LOSS"))status="Trade closed at stop loss.";\n else if(t.state.includes("TARGET 2"))status="Trade completed at Target 2.";\n else if(t.state.includes("TARGET 1"))status="Target 1 achieved; monitoring Target 2 / SL.";\n else if(pnl!==null&&pnl<0)status=`Below locked entry by ${Math.abs(pnl).toFixed(1)}%.`;\n else if(pnl!==null)status=`Above locked entry by ${pnl.toFixed(1)}%.`;\n $("tradeProgressText").textContent=status;\n}\n\nfunction renderLockedTrade(t,q=null){\n // While a trade exists, the option/risk cards show ONLY the locked trade.\n $("signal").textContent=isClosedTrade(t)?"TRADE CLOSED":"ACTIVE TRADE";\n $("signal").className="signal "+(isClosedTrade(t)?"neutral":"buy");\n $("bias").textContent=isClosedTrade(t)?"LOCKED TRADE COMPLETE • RESET TO SCAN AGAIN":"LOCKED TRADE • LIVE P/L + SL/TARGET MONITORING • V3.7 QUALITY + REVERSAL + EOD CHECKS ACTIVE";\n $("contract").textContent=t.contract;\n $("contract").className="contract "+((t.side||t.type)==="SELL"||t.type==="PE"?"sell":"buy");\n $("expiry").textContent=t.marketMode==="FUTURE"?`Reference ${t.instrumentLabel||t.contract} • ${t.side||t.type||""}`:`Locked ${t.expiry||""} • Strike ${t.strike||""} • ${t.type||""}`;\n $("liquidity").textContent=q\n   ?`LOCKED CONTRACT • ${q.stale?"STALE/FALLBACK":"LIVE"} • Volume ${q.volume||0} • OI ${q.oi||0} • BidQty ${q.bid_qty||0} • AskQty ${q.ask_qty||0}${q.spread_pct!=null?` • Spread ${fmt(q.spread_pct)}%`:""}${q.quote_time?` • ${q.quote_time}`:""}`\n   :"LOCKED CONTRACT • waiting for live quote";\n const ltp=q&&Number(q.ltp)>0?Number(q.ltp):Number(t.currentLtp||0);\n $("optionLtp").textContent=ltp>0?"₹"+fmt(ltp):"—";\n $("bid").textContent=q&&Number(q.bid)>0?"₹"+fmt(q.bid):"—";\n $("ask").textContent=q&&Number(q.ask)>0?"₹"+fmt(q.ask):"—";\n $("entry").textContent="₹"+fmt(t.entry);\n $("sl").textContent="₹"+fmt(t.sl);\n $("t1").textContent="₹"+fmt(t.t1);\n $("t2").textContent="₹"+fmt(t.t2);\n}\n\nfunction journal(){try{return JSON.parse(localStorage.getItem("niftyV36Journal")||"[]")}catch(e){return []}}\nfunction addJournal(kind,t,detail=""){\n const j=journal(); j.unshift({time:new Date().toLocaleString(),kind,contract:t&&t.contract?t.contract:"—",detail});\n localStorage.setItem("niftyV36Journal",JSON.stringify(j.slice(0,50))); renderJournal();\n}\nfunction renderJournal(){const j=journal();$("journalCount").textContent=j.length;$("journalLast").textContent=j.length?`${j[0].kind} • ${j[0].time}`:"—"}\nfunction evaluateTradeHealth(t,d){\n if(!t){$("tradeHealth").textContent="NO ACTIVE TRADE";$("tradeHealth").style.color="#cbd5e1";$("tradeAction").textContent="Waiting for a locked trade.";return}\n const bull=t.side?String(t.side).toUpperCase()==="BUY":t.type==="CE"; let score=0, reasons=[];\n const cp=Number(t.currentLtp||0), entry=Number(t.entry||0), pnl=(cp>0&&entry>0)?((cp-entry)/entry*100):0;\n const dir5=bull?["BUY","STRONG BUY"].includes(d.rating5):["SELL","STRONG SELL"].includes(d.rating5);\n const dir15=bull?["BUY","STRONG BUY"].includes(d.rating15):["SELL","STRONG SELL"].includes(d.rating15);\n const ema5=bull?d.ema10_5>d.ema20_5:d.ema10_5<d.ema20_5;\n const ema15=bull?d.ema10_15>d.ema20_15:d.ema10_15<d.ema20_15;\n const vwap=bull?d.spot>d.vwap5:d.spot<d.vwap5;\n const macd=bull?d.macd_5>d.macd_signal_5:d.macd_5<d.macd_signal_5;\n if(dir5){score+=18}else reasons.push("5m direction against trade");\n if(dir15){score+=22}else reasons.push("15m direction against trade");\n if(ema5){score+=12}else reasons.push("5m EMA trend weakened");\n if(ema15){score+=14}else reasons.push("15m EMA trend weakened");\n if(vwap){score+=12}else reasons.push("price crossed adverse side of VWAP");\n if(macd){score+=10}else reasons.push("5m MACD no longer confirms");\n if(Number(d.adx5)>=18){score+=6}else reasons.push("ADX 5m weak");\n if(Number(d.adx15)>=18){score+=6}else reasons.push("ADX 15m weak");\n if(pnl<=-12)score-=20; else if(pnl<=-7)score-=10;\n let label="HEALTHY", action="HOLD PLAN / MONITOR", color="#22c55e";\n if(score<35 || pnl<=-15){label="EXIT REVIEW";action="SETUP INVALIDATION RISK — review broker position and stop immediately";color="#ff7690"}\n else if(score<55){label="WEAKENING";action="DEFENSIVE MODE — avoid adding; consider tighter risk";color="#ef4444"}\n else if(score<75){label="CAUTION";action="MONITOR CLOSELY — momentum is mixed";color="#f59e0b"}\n $("tradeHealth").textContent=`${label} • ${Math.max(0,Math.min(100,score))}/100`;$("tradeHealth").style.color=color;\n $("tradeAction").textContent=action;$("tradeHealthReasons").textContent=reasons.length?reasons.slice(0,4).join(" • "):"Current trend structure remains aligned with the locked trade.";\n if((label==="EXIT REVIEW"||label==="WEAKENING")&&t.lastHealthAlert!==label){t.lastHealthAlert=label;saveTrade(t);notify("⚠️ TRADE "+label,`${t.contract} • ${action}`,"stop");addJournal(label,t,action)}\n}\n\nfunction indiaClock(){\n try{\n   const parts=new Intl.DateTimeFormat("en-GB",{timeZone:"Asia/Kolkata",hour12:false,hour:"2-digit",minute:"2-digit",weekday:"short",year:"numeric",month:"2-digit",day:"2-digit"}).formatToParts(new Date());\n   const o={};parts.forEach(x=>o[x.type]=x.value);return {hour:Number(o.hour||0),minute:Number(o.minute||0),weekday:o.weekday||"",date:`${o.year}-${o.month}-${o.day}`};\n }catch(e){const n=new Date();return {hour:n.getHours(),minute:n.getMinutes(),weekday:"",date:""}}\n}\nfunction expiryDays(exp){\n if(!exp)return null;\n const m=String(exp).match(/(\\d{1,2})-([A-Za-z]{3})-(\\d{4})/); if(!m)return null;\n const months={Jan:0,Feb:1,Mar:2,Apr:3,May:4,Jun:5,Jul:6,Aug:7,Sep:8,Oct:9,Nov:10,Dec:11};\n const mon=months[m[2][0].toUpperCase()+m[2].slice(1,3).toLowerCase()]; if(mon==null)return null;\n const target=Date.UTC(Number(m[3]),mon,Number(m[1]));\n const now=new Date(); const today=Date.UTC(now.getUTCFullYear(),now.getUTCMonth(),now.getUTCDate());\n return Math.round((target-today)/86400000);\n}\nfunction evaluateEodDecision(t,d){\n const dec=$("eodDecision"),scoreEl=$("eodScore"),act=$("eodAction"),why=$("eodReasons"),win=$("eodWindow");\n if(!dec)return;\n if(!t || isClosedTrade(t)){\n   dec.textContent=t?"POSITION CLOSED":"NO ACTIVE POSITION";dec.style.color="#cbd5e1";scoreEl.textContent="Carry score — / 100";act.textContent=t?"No overnight decision required.":"Waiting for a locked trade.";why.textContent="Final carry/exit review becomes active only for an open locked trade.";return;\n }\n const bull=t.side?String(t.side).toUpperCase()==="BUY":t.type==="CE"; let score=0, reasons=[], positives=[];\n const dir15=bull?["BUY","STRONG BUY"].includes(d.rating15):["SELL","STRONG SELL"].includes(d.rating15);\n const ema15=bull?d.ema10_15>d.ema20_15:d.ema10_15<d.ema20_15;\n const ema50=bull?d.spot>d.ema50_15:d.spot<d.ema50_15;\n const vwap=bull?d.spot>d.vwap5:d.spot<d.vwap5;\n const macd15=bull?d.macd_15>d.macd_signal_15:d.macd_15<d.macd_signal_15;\n const rsi15=Number(d.rsi15), rsiHealthy=bull?(rsi15>=50&&rsi15<=68):(rsi15>=32&&rsi15<=50);\n const adx15=Number(d.adx15||0), cp=Number(t.currentLtp||0), entry=Number(t.entry||0), pnl=(cp>0&&entry>0)?((cp-entry)/entry*100):0;\n const days=expiryDays(t.expiry);\n const add=(ok,pts,pos,neg)=>{if(ok){score+=pts;positives.push(pos)}else reasons.push(neg)};\n add(dir15,22,"15m direction aligned","15m direction against position");\n add(ema15,16,"15m EMA trend aligned","15m EMA 10/20 trend adverse");\n add(ema50,14,"price aligned with 15m EMA50","price on adverse side of 15m EMA50");\n add(vwap,12,"VWAP aligned","price on adverse side of VWAP");\n add(macd15,12,"15m MACD aligned","15m MACD momentum adverse");\n add(adx15>=20,10,"15m ADX strong","15m trend strength below 20");\n add(rsiHealthy,8,"15m RSI healthy","15m RSI weak or stretched");\n add(pnl>-8,6,"P/L not deeply adverse","position already materially below reference entry");\n if(days!==null && days<=1){score-=25;reasons.push("expiry is too close for comfortable overnight carry")}\n else if(days!==null && days<=2){score-=10;reasons.push("near-expiry overnight theta/gap risk")}\n score=Math.max(0,Math.min(100,score));\n const c=indiaClock(), mins=c.hour*60+c.minute, review=mins>=15*60+5, finalWindow=mins>=15*60+20;\n let label="REVIEW LATER", action="Intraday monitoring continues; final carry/exit decision activates near market close.", color="#9bb0c9";\n if(review){\n   if(score>=75 && dir15 && ema15 && vwap && macd15 && adx15>=20 && !(days!==null&&days<=1)){\n     label="CARRY FORWARD CANDIDATE"; action="Overnight carry conditions are comparatively strong. Re-check broker quote, gap risk and position size before close."; color="#22c55e";\n   }else if(score>=55 && dir15 && ema15){\n     label="HOLD ONLY WITH CAUTION"; action="Mixed overnight quality. Consider reducing exposure or exiting unless your broker risk plan explicitly allows the carry."; color="#f59e0b";\n   }else{\n     label="EXIT BEFORE CLOSE"; action="Overnight carry quality is weak. Review the broker position and consider closing before market close."; color="#ff7690";\n   }\n }\n dec.textContent=label;dec.style.color=color;scoreEl.textContent=`Carry score ${score} / 100`;\n act.textContent=action;why.textContent=(reasons.length?reasons.slice(0,5):positives.slice(0,5)).join(" • ") || "No additional EOD evidence available.";\n win.textContent=review?(finalWindow?"FINAL EOD REVIEW WINDOW ACTIVE • India time":"EOD REVIEW ACTIVE • final check again after 3:20 PM"):`Review window starts at 3:05 PM India time • current score is provisional.`;\n if(review && t.lastEodDecision!==label){t.lastEodDecision=label;saveTrade(t);notify("🌙 EOD: "+label,`${t.contract} • Carry score ${score}/100 • ${action}`,label.includes("EXIT")?"stop":"entry");addJournal("EOD "+label,t,`Carry score ${score}/100`)}\n}\n\nfunction applyTradePrice(t,p){\n if(!Number.isFinite(p)||p<=0)return t;\n t.currentLtp=p;\n if(t.state==="ORDER READY / WAITING FOR ENTRY" || t.state==="ENTRY / ORDER PRICE HIT"){t.state="ENTRY LOCKED / MONITORING";t.entryLocked=true}\n const shortSide=(String(t.side||"").toUpperCase()==="SELL"||String(t.type||"").toUpperCase()==="PE");\n const effectiveSl=(t.state==="TARGET 1 HIT"&&Number(t.trailingSl||0)>0)?Number(t.trailingSl):Number(t.sl);\n const stopHit=shortSide?p>=effectiveSl:p<=effectiveSl;\n const t2Hit=shortSide?p<=Number(t.t2):p>=Number(t.t2);\n const t1Hit=shortSide?p<=Number(t.t1):p>=Number(t.t1);\n if((t.state==="ENTRY LOCKED / MONITORING"||t.state==="TARGET 1 HIT") && stopHit){\n   t.state="STOP LOSS HIT / CLOSED";notify("🛑 STOP / TRAIL HIT",`${t.contract} • LTP ₹${fmt(p)} • Effective SL ₹${fmt(effectiveSl)}`,"stop");addJournal("STOP / TRAIL HIT",t,`Exit reference ₹${fmt(p)}`)\n }else if((t.state==="ENTRY LOCKED / MONITORING"||t.state==="TARGET 1 HIT") && t2Hit){\n   t.state="TARGET 2 HIT / CLOSED";notify("🏆 TARGET 2 HIT",`${t.contract} • LTP ₹${fmt(p)} • Locked final target ₹${fmt(t.t2)}`,"target2");addJournal("TARGET 2",t,`LTP ₹${fmt(p)}`)\n }else if(t.state==="ENTRY LOCKED / MONITORING" && t1Hit){\n   t.state="TARGET 1 HIT";t.trailingSl=Number(t.entry);notify("✅ TARGET 1 HIT",`${t.contract} • LTP ₹${fmt(p)} • T1 ₹${fmt(t.t1)} • protective trail moved to entry ₹${fmt(t.trailingSl)}`,"target1");addJournal("TARGET 1",t,`Trail ₹${fmt(t.trailingSl)}`)\n }\n saveTrade(t);return t;\n}\n\nasync function refreshLockedTrade(){\n let t=readTrade();\n if(!t)return;\n try{\n   const u=t.marketMode==="FUTURE"?`/api/commodity-quote?instrument=${encodeURIComponent(t.instrument||selectedInstrument)}`:`/api/locked-option?strike=${encodeURIComponent(t.strike)}&type=${encodeURIComponent(t.type)}&expiry=${encodeURIComponent(t.expiry||"")}`;\n   const q=await fetchJsonWithRetry(u,3,9000);\n   t=applyTradePrice(t,Number(q.ltp));\n   updateTradeUI(t);\n   renderLockedTrade(t,q);\n   if(q.stale){\n     $("error").textContent="Locked trade using a temporary last-good quote.";\n     showConnectionIssue("Locked-contract live quote is temporarily stale.");\n   }\n }catch(e){\n   updateTradeUI(t);\n   renderLockedTrade(t,null);\n   $("error").textContent="Locked trade quote temporarily unavailable: "+e.message;\n   showConnectionIssue("Locked trade is preserved; live option quote will retry automatically.");\n }\n}\n\nfunction monitorTrade(d){\n let t=readTrade();\n\n // Existing trade always wins over new scanner output.\n if(t){\n   updateTradeUI(t);\n   renderLockedTrade(t,null);\n   refreshLockedTrade();\n   return;\n }\n\n const tradeObj=d.option||d.contract_quote; const active=Boolean(tradeObj&&d.execution_ready&&d.trigger_confirmed&&tradeObj.tradable);\n if(!active)return;\n\n // First confirmed setup: lock contract, exact ask entry and all risk levels.\n t={\n   contract:tradeObj.contract,\n   expiry:tradeObj.expiry||"",\n   strike:Number(tradeObj.strike||0),\n   type:tradeObj.type||tradeObj.side,\n   side:tradeObj.side||(tradeObj.type==="PE"?"SELL":"BUY"),\n   marketMode:d.market_mode||"OPTION",\n   instrument:d.instrument_key||"NIFTY",\n   instrumentLabel:d.instrument_label||"NIFTY",\n   entry:Number(tradeObj.entry),\n   sl:Number(tradeObj.sl),\n   t1:Number(tradeObj.target1),\n   t2:Number(tradeObj.target2),\n   currentLtp:Number(tradeObj.ltp||0),\n   state:"ENTRY LOCKED / MONITORING",\n   locked:true,\n   entryLocked:true,\n   lockedAt:d.updated,\n   originalSl:Number(tradeObj.sl),\n   trailingSl:0\n };\n saveTrade(t);\n hideReversalWarning();\n localStorage.removeItem("niftyV35Reversal");\n updateTradeUI(t);\n renderLockedTrade(t,tradeObj);\n notify("✅ ENTRY REFERENCE LOCKED",`${t.contract} • Entry ref ₹${fmt(t.entry)} • SL ₹${fmt(t.sl)} • T1 ₹${fmt(t.t1)} • T2 ₹${fmt(t.t2)} • monitoring started`,"entry");\n addJournal("ENTRY LOCKED",t,`Entry ₹${fmt(t.entry)} • SL ₹${fmt(t.sl)} • T1 ₹${fmt(t.t1)} • T2 ₹${fmt(t.t2)}`);\n\n // Start monitoring SL/targets immediately from the confirmation refresh.\n t=applyTradePrice(t,Number(tradeObj.ltp));\n updateTradeUI(t);\n renderLockedTrade(t,tradeObj);\n}\n\nfunction render(d){\n const requested=String(selectedInstrument||"NIFTY").toUpperCase();\n const received=String((d&&d.instrument_key)||"").toUpperCase();\n if(!d || !received || received!==requested){\n   throw new Error(`Instrument mismatch: requested ${requested}, received ${received||"UNKNOWN"}. Data rejected.`);\n }\n $("spot").textContent=fmt(d.spot);\n $("updated").textContent=`Updated ${d.updated} • ${d.data_source}`; document.querySelector("h1").textContent=`📈 ${d.instrument_label||"Professional"} Signals V3.7.1`;\n $("marketStatus").textContent=d.market_open?"MARKET OPEN":"MARKET CLOSED";\n $("confidence").textContent=d.confidence+" / 100";$("confBar").style.width=d.confidence+"%";\n $("marketViewSignal").textContent=`${d.signal} • 5m ${d.rating5} • 15m ${d.rating15}`;\n $("marketViewSignal").className="small";\n $("reason").textContent=d.reason;\n $("buyAbove").textContent=fmt(d.buy_above);$("sellBelow").textContent=fmt(d.sell_below); document.querySelector("#triggerSectionTitle + .row .kpi .t").textContent=`${d.instrument_short||"MARKET"} BUY ABOVE`; document.querySelectorAll("#triggerSectionTitle + .row .kpi .t")[1].textContent=`${d.instrument_short||"MARKET"} SELL BELOW`;\n $("rating5").textContent=d.rating5;$("rating15").textContent=d.rating15;\n $("rsi5").textContent=fmt(d.rsi5);$("rsi15").textContent=fmt(d.rsi15);\n $("ema5").textContent=`${fmt(d.ema10_5)} / ${fmt(d.ema20_5)}`;\n $("ema15").textContent=`${fmt(d.ema10_15)} / ${fmt(d.ema20_15)}`;\n $("macd5").textContent=`${fmt(d.macd_5)} / ${fmt(d.macd_signal_5)}`;\n $("adx5").textContent=fmt(d.adx5);$("atr5").textContent=fmt(d.atr5);\n $("adx15").textContent=fmt(d.adx15);$("vwap5").textContent=fmt(d.vwap5);\n $("ema50_5").textContent=fmt(d.ema50_5);$("ema50_15").textContent=fmt(d.ema50_15);\n $("macd15").textContent=`${fmt(d.macd_15)} / ${fmt(d.macd_signal_15)}`;\n $("checks").innerHTML=d.checks.map(x=>`<span class="pill ${x.ok?"ok":"bad"}">${x.ok?"✓":"✕"} ${x.label}</span>`).join("");\n const qGrade=d.quality_grade||"—";\n $("qualityGrade").textContent=qGrade==="A+"?"GRADE A+ • PREMIUM SETUP":qGrade==="A"?"GRADE A • CONFIRMED SETUP":qGrade==="B"?"GRADE B • PREPARE ONLY":qGrade==="C"?"GRADE C • WAIT":"NO NEW ENTRY";\n $("qualityGrade").style.color=(qGrade==="A+"||qGrade==="A")?"#00d66b":qGrade==="B"?"#ffb020":qGrade==="C"?"#cbd5e1":"#ff7690";\n $("qualityState").textContent=d.entry_state||"WAIT";\n $("qualityState").style.color=d.entry_state==="CONFIRMED"?"#22c55e":d.entry_state==="PREPARE"?"#f59e0b":d.entry_state==="AVOID"?"#ef4444":"#cbd5e1";\n $("qualitySummary").textContent=`New-entry filters passed ${d.quality_passed||0}/${d.quality_total||0}. ${d.entry_state_detail||""}`;\n $("regimeDetail").textContent=`Market regime: ${d.market_regime||"—"}${d.candle_confirmation?" • candle aligned":""}${d.breakout_confirmation?" • breakout confirmed":""}`;\n $("qualityChecks").innerHTML=(d.quality_checks||[]).map(x=>`<span class="pill ${x.ok?"ok":"bad"}">${x.ok?"✓":"✕"} ${x.label}</span>`).join("");\n $("dot").className="dot on";$("status").textContent="Connected • V3.7.1";$("error").textContent=d.warning||"";\n saveLastSignal(d);\n clearConnectionIssue();\n\n const t=readTrade();\n if(t){\n   // V3.6: locked trade remains separate; scanner drives quality, reversal and EOD carry/exit checks.\n   $("marketViewTitle").textContent="CURRENT MARKET VIEW — informational only";\n   $("indicatorSectionTitle").textContent="CURRENT MARKET INDICATORS — do not overwrite locked trade";\n   $("triggerSectionTitle").textContent="CURRENT SCANNER LEVELS — next setup only";\n   renderLockedTrade(t,null);\n   evaluateReversalWarning(t,d);\n   evaluateTradeHealth(t,d);\n   evaluateEodDecision(t,d);\n   monitorTrade(d);\n   return;\n }else{\n   $("marketViewTitle").textContent="Current market view";\n   $("indicatorSectionTitle").textContent="Current market indicators";\n   $("triggerSectionTitle").textContent="Current scanner trigger levels";\n }\n\n $("signal").textContent=d.signal;\n $("signal").className="signal "+(d.signal==="BUY"?"buy":d.signal==="SELL"?"sell":d.signal.includes("WATCH")?"watch":"neutral");\n $("bias").textContent=d.bias;\n\n const cq=d.option||d.contract_quote;\n if(cq){\n   $("optionCardTitle").textContent=d.market_mode==="FUTURE"?"Selected MCX futures reference":"Automatically selected option";\n   $("contract").textContent=cq.contract;\n   $("contract").className="contract "+((cq.side==="SELL"||cq.type==="PE")?"sell":"buy");\n   $("expiry").textContent=d.market_mode==="FUTURE"?`${d.instrument_label} • continuous-futures reference • ${cq.side}`:`Expiry ${cq.expiry} • Strike ${cq.strike} • ${cq.type}`;\n   $("liquidity").textContent=d.market_mode==="FUTURE"?`TradingView reference quote • Verify exact active MCX contract/expiry in broker • ATR ${fmt(d.atr5)}`:`Liquidity ${cq.liquidity} • Volume ${cq.volume} • OI ${cq.oi} • BidQty ${cq.bid_qty||0} • AskQty ${cq.ask_qty||0} • Spread ${fmt(cq.spread_pct)}% • Risk ${fmt(cq.risk_pct||0)}% • R:R ${fmt(cq.rr1||0)} / ${fmt(cq.rr2||0)}`;\n   $("optionLtp").textContent="₹"+fmt(cq.ltp);$("bid").textContent=cq.bid?"₹"+fmt(cq.bid):"—";$("ask").textContent=cq.ask?"₹"+fmt(cq.ask):"₹"+fmt(cq.entry);\n   $("entry").textContent="₹"+fmt(cq.entry);$("sl").textContent="₹"+fmt(cq.sl);$("t1").textContent="₹"+fmt(cq.target1);$("t2").textContent="₹"+fmt(cq.target2)\n }else{\n   ["contract","expiry","liquidity","optionLtp","bid","ask","entry","sl","t1","t2"].forEach(id=>$(id).textContent="—")\n }\n monitorTrade(d);\n updateTradeUI(readTrade());\n evaluateTradeHealth(readTrade(),d);\n evaluateEodDecision(readTrade(),d);\n}\n\n\nfunction clearScannerDisplay(message="Waiting for selected instrument data…"){\n const selectedLabel=($("instrumentSelect")&&$("instrumentSelect").selectedOptions.length)\n   ? $("instrumentSelect").selectedOptions[0].textContent\n   : String(selectedInstrument||"MARKET");\n\n $("spot").textContent="—";\n $("signal").textContent="DATA UNAVAILABLE";\n $("signal").className="signal neutral";\n $("bias").textContent=selectedLabel+" • "+message;\n $("updated").textContent="No verified data loaded for this instrument";\n $("marketStatus").textContent="—";\n\n $("confidence").textContent="—";\n $("confBar").style.width="0%";\n $("marketViewSignal").textContent="NO VERIFIED SIGNAL";\n $("reason").textContent="Signals are disabled until valid data for "+selectedLabel+" is received.";\n\n ["rating5","rating15","rsi5","rsi15","ema5","ema15","macd5","adx5","atr5",\n  "adx15","vwap5","ema50_5","ema50_15","macd15"].forEach(id=>{\n   if($(id))$(id).textContent="—";\n });\n if($("checks"))$("checks").innerHTML="";\n if($("qualityChecks"))$("qualityChecks").innerHTML="";\n if($("qualityGrade"))$("qualityGrade").textContent="DATA REQUIRED";\n if($("qualityState"))$("qualityState").textContent="WAIT";\n if($("qualitySummary"))$("qualitySummary").textContent="No entry evaluation until valid selected-instrument data arrives.";\n if($("regimeDetail"))$("regimeDetail").textContent="Market regime: —";\n\n if($("buyAbove"))$("buyAbove").textContent="—";\n if($("sellBelow"))$("sellBelow").textContent="—";\n\n ["contract","expiry","liquidity","optionLtp","bid","ask","entry","sl","t1","t2"].forEach(id=>{\n   if($(id))$(id).textContent="—";\n });\n\n $("dot").className="dot warn";\n $("status").textContent="DATA UNAVAILABLE • V3.7.1";\n}\n\nasync function refresh(){\n if(busy)return;\n busy=true;\n $("dot").className="dot warn";\n $("status").textContent="Updating…";\n try{\n   const d=await fetchJsonWithRetry(`/api/signal?instrument=${encodeURIComponent(selectedInstrument)}`,3,10000);\n   render(d);\n }catch(e){\n   reconnectFailures++;\n   const cached=loadLastSignal();\n   if(cached&&cached.data){\n     // Same-instrument cache only. Cross-instrument cache is forbidden in V3.7.1.\n     render(cached.data);\n     const ageMin=Math.max(0,Math.round((Date.now()-Number(cached.savedAt||Date.now()))/60000));\n     $("updated").textContent=`LAST KNOWN ${selectedInstrument} DATA • saved about ${ageMin} min ago`;\n     $("status").textContent="STALE SAME-INSTRUMENT DATA • V3.7.1";\n   }else{\n     clearScannerDisplay("Live source unavailable. No same-instrument fallback exists.");\n   }\n   const delay=Math.min(60,5*Math.pow(2,Math.min(reconnectFailures-1,3)));\n   $("error").textContent="Live refresh failed: "+e.message;\n   showConnectionIssue("Live source unavailable. No cross-instrument fallback is allowed.",delay);\n   if(reconnectTimer)clearTimeout(reconnectTimer);\n   reconnectTimer=setTimeout(()=>{reconnectTimer=null;refresh()},delay*1000);\n }finally{\n   busy=false;\n }\n}\nwindow.addEventListener("offline",()=>showConnectionIssue("Phone is offline. Waiting for internet connection."));\nwindow.addEventListener("online",()=>{showConnectionIssue("Internet restored. Reconnecting now…");refresh();refreshLockedTrade()});\n$("refresh").onclick=refresh;$("resetTradeBtn").onclick=()=>{clearTrade();clearScannerDisplay("Switching instrument…");refresh()};$("notifyBtn").onclick=async()=>{notificationsEnabled=true;localStorage.setItem("niftyV35Notify","1");if("Notification"in window&&Notification.permission==="default"){try{await Notification.requestPermission()}catch(e){}}$("notifyBtn").textContent="NOTIFICATIONS / VIBRATION ENABLED";notify("TRADE ALERTS ENABLED","Selected-instrument trigger, entry reference, stop loss, Target 1 and Target 2 alerts are enabled while this page stays open.","setup")};if(notificationsEnabled)$("notifyBtn").textContent="NOTIFICATIONS / VIBRATION ENABLED";\nrenderJournal();\n$("exportJournalBtn").onclick=async()=>{const txt=journal().map(x=>`${x.time} | ${x.kind} | ${x.contract} | ${x.detail}`).join("\\n")||"No journal events.";try{await navigator.clipboard.writeText(txt);$("journalLast").textContent="Journal copied"}catch(e){$("journalLast").textContent="Copy blocked by browser"}};\nupdateTradeUI(readTrade());\nconst cachedStartup=loadLastSignal();\nif(cachedStartup&&cachedStartup.data){\n try{\n   render(cachedStartup.data);\n   $("updated").textContent="LAST KNOWN DATA • checking live connection…";\n   showConnectionIssue("Checking live server connection now.");\n }catch(e){}\n}\nrefresh();\nsetInterval(refresh,15000);\nsetInterval(refreshLockedTrade,15000);\n</script></body></html>'

def market_open_now(cfg=None):
    cfg=cfg or INSTRUMENTS["NIFTY"]
    now=datetime.now(IST)
    if now.weekday()>=5:return False
    m=now.hour*60+now.minute
    return int(cfg.get("session_start",555))<=m<=int(cfg.get("session_end",930))

def tv_rating(v):
    if v>=.5:return "STRONG BUY"
    if v>=.1:return "BUY"
    if v<=-.5:return "STRONG SELL"
    if v<=-.1:return "SELL"
    return "NEUTRAL"

def fetch_tv(ticker="NSE:NIFTY", label="NIFTY"):
    # V3.5 adds trend, momentum and market-regime confirmation.
    cols=[
        "close|5","open|5","Recommend.All|5","RSI|5","EMA10|5","EMA20|5","EMA50|5",
        "MACD.macd|5","MACD.signal|5","ADX|5","ATR|5","high|5","low|5","VWAP|5",
        "open|15","high|15","low|15","close|15","Recommend.All|15","RSI|15","EMA10|15","EMA20|15","EMA50|15",
        "MACD.macd|15","MACD.signal|15","ADX|15"
    ]
    payload={"symbols":{"tickers":[ticker],"query":{"types":[]}},"columns":cols,"range":[0,1]}
    r=requests.post(TV_URL,json=payload,headers=TV_HEADERS,timeout=12)
    r.raise_for_status()
    j=r.json()
    if not j.get("data"):raise RuntimeError(f"TradingView returned no {label} data for {ticker}.")
    vals=j["data"][0]["d"]
    if len(vals)!=len(cols):raise RuntimeError("TradingView returned incomplete indicator data.")
    x=dict(zip(cols,vals))
    def f(k,d=0):
        try:return float(x[k]) if x[k] is not None else float(d)
        except:return float(d)
    spot=f("close|5")
    return {
        "spot":spot,"open5":f("open|5",spot),
        "rec5":f("Recommend.All|5"),"rsi5":f("RSI|5",50),
        "ema10_5":f("EMA10|5",spot),"ema20_5":f("EMA20|5",spot),"ema50_5":f("EMA50|5",spot),
        "macd5":f("MACD.macd|5"),"macds5":f("MACD.signal|5"),"adx5":f("ADX|5"),
        "atr5":max(f("ATR|5",1),.01),"high5":f("high|5",spot),"low5":f("low|5",spot),
        "vwap5":f("VWAP|5",spot),
        "open15":f("open|15",spot),"high15":f("high|15",spot),"low15":f("low|15",spot),"close15":f("close|15",spot),
        "rec15":f("Recommend.All|15"),"rsi15":f("RSI|15",50),
        "ema10_15":f("EMA10|15",spot),"ema20_15":f("EMA20|15",spot),"ema50_15":f("EMA50|15",spot),
        "macd15":f("MACD.macd|15"),"macds15":f("MACD.signal|15"),"adx15":f("ADX|15")
    }

def score(tv,bull=True):
    """
    V3.5 deliberately favors fewer, higher-quality setups.
    Maximum score = 100. A high score alone is not enough; hard filters below
    can still block a trade during weak-trend or conflicting conditions.
    """
    s=0;checks=[]
    def add(label,ok,pts):
        nonlocal s
        ok=bool(ok)
        checks.append({"label":label,"ok":ok})
        if ok:s+=pts

    add("5m rating confirms",tv["rec5"]>=.1 if bull else tv["rec5"]<=-.1,10)
    add("15m rating confirms",tv["rec15"]>=.1 if bull else tv["rec15"]<=-.1,15)
    add("5m EMA 10/20 trend",tv["ema10_5"]>tv["ema20_5"] if bull else tv["ema10_5"]<tv["ema20_5"],10)
    add("15m EMA 10/20 trend",tv["ema10_15"]>tv["ema20_15"] if bull else tv["ema10_15"]<tv["ema20_15"],12)
    add("Price vs EMA50 5m",tv["spot"]>tv["ema50_5"] if bull else tv["spot"]<tv["ema50_5"],8)
    add("Price vs EMA50 15m",tv["spot"]>tv["ema50_15"] if bull else tv["spot"]<tv["ema50_15"],10)
    add("Price vs VWAP",tv["spot"]>tv["vwap5"] if bull else tv["spot"]<tv["vwap5"],10)
    add("RSI 5m healthy",(52<=tv["rsi5"]<=68) if bull else (32<=tv["rsi5"]<=48),5)
    add("RSI 15m healthy",(50<=tv["rsi15"]<=68) if bull else (32<=tv["rsi15"]<=50),5)
    add("MACD 5m confirms",tv["macd5"]>tv["macds5"] if bull else tv["macd5"]<tv["macds5"],5)
    add("MACD 15m confirms",tv["macd15"]>tv["macds15"] if bull else tv["macd15"]<tv["macds15"],5)
    add("ADX 5m ≥ 20",tv["adx5"]>=20,3)
    add("ADX 15m ≥ 18",tv["adx15"]>=18,2)
    return s,checks

def quality_gate(tv,bull,score_value):
    """V3.5 separates setup quality from the existing locked-trade monitor.

    A new entry needs directional agreement, structure alignment, trend strength,
    and a simple live-candle/breakout confirmation. The regime detector prevents
    new entries when the 5m trend is too weak even if slower indicators still look good.
    """
    same_direction=(tv["rec5"]>=.1 and tv["rec15"]>=.1) if bull else (tv["rec5"]<=-.1 and tv["rec15"]<=-.1)
    ema_trend=(tv["ema10_5"]>tv["ema20_5"] and tv["ema10_15"]>tv["ema20_15"]) if bull else (tv["ema10_5"]<tv["ema20_5"] and tv["ema10_15"]<tv["ema20_15"])
    long_trend=(tv["spot"]>tv["ema50_5"] and tv["spot"]>tv["ema50_15"]) if bull else (tv["spot"]<tv["ema50_5"] and tv["spot"]<tv["ema50_15"])
    vwap_ok=tv["spot"]>tv["vwap5"] if bull else tv["spot"]<tv["vwap5"]
    macd_ok=(tv["macd5"]>tv["macds5"] and tv["macd15"]>tv["macds15"]) if bull else (tv["macd5"]<tv["macds5"] and tv["macd15"]<tv["macds15"])
    rsi_ok=((50<=tv["rsi5"]<=68) and (50<=tv["rsi15"]<=68)) if bull else ((32<=tv["rsi5"]<=50) and (32<=tv["rsi15"]<=50))

    # Regime filter: avoid chop when the fast timeframe has little directional strength.
    if tv["adx5"] < 15:
        market_regime="CHOP / LOW TREND"
        regime_ok=False
    elif tv["adx5"] < 20 or tv["adx15"] < 18:
        market_regime="TRANSITION / DEVELOPING TREND"
        regime_ok=False
    else:
        market_regime="TRENDING"
        regime_ok=True

    # Live candle confirmation: current 5m body should agree with the intended side.
    candle_ok=(tv["spot"]>tv["open5"]) if bull else (tv["spot"]<tv["open5"])

    # Practical breakout confirmation: require price beyond the nearby structure cluster,
    # not just one indicator flipping. ATR buffer reduces noise around EMA/VWAP.
    buf=max(tv["atr5"]*0.10, 1.5)
    if bull:
        structure=max(tv["ema20_5"],tv["ema50_5"],tv["vwap5"])
        breakout_ok=tv["spot"] >= structure + buf
    else:
        structure=min(tv["ema20_5"],tv["ema50_5"],tv["vwap5"])
        breakout_ok=tv["spot"] <= structure - buf

    hard={
        ("BUY: 5m + 15m direction agree" if bull else "SELL: 5m + 15m direction agree"):same_direction,
        ("BUY: EMA 10/20 aligned" if bull else "SELL: EMA 10/20 aligned"):ema_trend,
        ("BUY: price beyond EMA50s" if bull else "SELL: price below EMA50s"):long_trend,
        ("BUY: price above VWAP" if bull else "SELL: price below VWAP"):vwap_ok,
        ("BUY: MACD confirms 5m + 15m" if bull else "SELL: MACD confirms 5m + 15m"):macd_ok,
        "Trend regime strong enough":regime_ok,
        "RSI healthy / not stretched":rsi_ok,
        ("5m bullish candle confirms" if bull else "5m bearish candle confirms"):candle_ok,
        "Breakout clears structure + buffer":breakout_ok,
    }
    passed=sum(1 for v in hard.values() if v)

    # PREPARE is informational only; only CONFIRMED may proceed to trigger/option execution.
    core_prepare=same_direction and ema_trend and long_trend and vwap_ok and rsi_ok and score_value>=65
    confirmed=all(hard.values()) and score_value>=80
    prepare=core_prepare and passed>=6 and not confirmed

    if confirmed:
        entry_state="CONFIRMED"
        detail="All new-entry conditions aligned; still wait for frozen trigger and tradable option quote."
    elif prepare:
        entry_state="PREPARE"
        detail="Setup is developing, but execution is blocked until candle/breakout/regime confirmation completes."
    elif market_regime=="CHOP / LOW TREND":
        entry_state="AVOID"
        detail="Low-trend/choppy regime. New entries are intentionally blocked."
    else:
        entry_state="WAIT"
        detail="Conditions are mixed. Wait for stronger multi-timeframe alignment."

    return confirmed,prepare,hard,passed,entry_state,detail,market_regime,candle_ok,breakout_ok

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

def choose_option(oc,spot,bull,atr5=None,confidence=0):
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
    # V3.5 volatility-aware risk model. It is a decision-support reference, not a broker order.
    atr_ratio=(float(atr5 or 0)/max(float(spot),1))*100
    base_pct=0.16
    if confidence>=90: base_pct=0.14
    elif confidence<80: base_pct=0.18
    if atr_ratio>0.08: base_pct+=0.02
    risk=min(max(entry*base_pct,spread*3,entry*.10),entry*.22)
    rr1=1.35 if confidence<90 else 1.50
    rr2=2.15 if confidence<90 else 2.40
    liq="GOOD" if sp<=5 and v>=1000 and bq>0 and aq>0 else "FAIR"

    return {
        "contract":f"NIFTY {int(st)} {typ}","expiry":ex,"strike":st,"type":typ,
        "ltp":round(l,2),"bid":round(b,2),"ask":round(a,2),"entry":round(entry,2),
        "sl":round(max(entry-risk,.05),2),"target1":round(entry+rr1*risk,2),
        "target2":round(entry+rr2*risk,2),"risk_pct":round(risk/entry*100,1),
        "rr1":round(rr1,2),"rr2":round(rr2,2),"volume":v,"oi":oi,
        "bid_qty":int(bq),"ask_qty":int(aq),
        "spread_pct":round(sp,2),"liquidity":liq,"tradable":True
    }


def confirm_trigger(direction,spot,atr,setup_active,state_key="NIFTY"):
    global _trigger_states
    _trigger_state=_trigger_states.setdefault(state_key,{"direction":None,"level":None,"count":0,"confirmed":False,"misses":0,"started_at":None})
    """
    V2.5 frozen WATCH trigger:
    - Freeze the breakout level as soon as BUY WATCH / SELL WATCH (or stronger) appears.
    - Do not chase spot on each refresh.
    - Require 2 consecutive refreshes beyond that same frozen level.
    - Allow up to 2 temporary NO TRADE refreshes before invalidating the setup.
    - Opposite directional setup immediately replaces the old frozen trigger.
    """
    def fresh_state():
        return {
            "direction":None,"level":None,"count":0,"confirmed":False,
            "misses":0,"started_at":None
        }

    if direction not in ("BUY","SELL"):
        _trigger_states[state_key]=fresh_state(); _trigger_state=_trigger_states[state_key]
        return None,0,False,False

    # If the directional setup temporarily weakens, keep the frozen level briefly
    # instead of moving it with price or deleting it immediately.
    if not setup_active:
        if _trigger_state["direction"]==direction and _trigger_state["level"] is not None:
            _trigger_state["misses"]+=1
            _trigger_state["count"]=0
            if _trigger_state["misses"]<=2:
                return float(_trigger_state["level"]),0,bool(_trigger_state["confirmed"]),True
        _trigger_states[state_key]=fresh_state(); _trigger_state=_trigger_states[state_key]
        return None,0,False,False

    # New setup or opposite direction -> freeze a new trigger once.
    if _trigger_state["direction"]!=direction or _trigger_state["level"] is None:
        buffer=max(float(atr)*0.15,5.0)
        level=spot+buffer if direction=="BUY" else spot-buffer
        _trigger_states[state_key]={
            "direction":direction,
            "level":round(level,2),
            "count":0,
            "confirmed":False,
            "misses":0,
            "started_at":datetime.now(IST).strftime("%H:%M:%S")
        }
        _trigger_state=_trigger_states[state_key]
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




def commodity_contract(cfg, tv, bull, trigger_level, trigger_confirmed):
    # Continuous-futures market data is used only as a signal/reference feed.
    # The user must verify the exact active MCX expiry and executable quote in the broker.
    if not trigger_confirmed or trigger_level is None:
        return None
    side="BUY" if bull else "SELL"
    entry=round(float(trigger_level),2)
    atr=max(float(tv.get("atr5",0) or 0),0.01)
    sl_mult=float(cfg.get("atr_sl",1.2)); t1_mult=float(cfg.get("atr_t1",1.8)); t2_mult=float(cfg.get("atr_t2",2.7))
    if bull:
        sl=entry-sl_mult*atr; t1=entry+t1_mult*atr; t2=entry+t2_mult*atr
    else:
        sl=entry+sl_mult*atr; t1=entry-t1_mult*atr; t2=entry-t2_mult*atr
    return {
        "contract":cfg["label"]+" FUTURES REF","side":side,"type":side,"expiry":"VERIFY IN BROKER",
        "strike":0,"ltp":round(tv["spot"],2),"bid":0,"ask":0,"entry":round(entry,2),
        "sl":round(sl,2),"target1":round(t1,2),"target2":round(t2,2),
        "tradable":True,"liquidity":"BROKER VERIFY","volume":0,"oi":0,"bid_qty":0,"ask_qty":0,"spread_pct":0,
        "source":"TradingView continuous futures reference"
    }

def commodity_quote(instrument):
    key,cfg=instrument_config(instrument)
    if cfg["mode"]!="FUTURE": raise RuntimeError("Not an MCX futures instrument.")
    tv=fetch_tv(cfg["ticker"],cfg["label"])
    return {"contract":cfg["label"]+" FUTURES REF","instrument":key,"ltp":round(tv["spot"],2),"bid":0,"ask":0,"stale":False,"quote_time":datetime.now(IST).strftime("%H:%M:%S"),"source":"TradingView continuous futures reference"}

def build_signal(instrument="NIFTY"):
    instrument,cfg=instrument_config(instrument)
    tv=fetch_tv(cfg["ticker"],cfg["label"]);bs,bc=score(tv,True);ss,sc=score(tv,False)
    bull=bs>=ss;conf=bs if bull else ss;checks=bc if bull else sc;diff=abs(bs-ss)

    strong_gate,watch_gate,quality_checks,quality_passed,entry_state,entry_state_detail,market_regime,candle_confirmation,breakout_confirmation=quality_gate(tv,bull,conf)

    # V3.5: score alone can never create an entry.
    # A CONFIRMED setup also requires regime + candle + breakout confirmation.
    if strong_gate and conf>=80 and diff>=20:
        raw_signal="BUY" if bull else "SELL"
        bias="HIGH-SELECTIVITY BULLISH" if bull else "HIGH-SELECTIVITY BEARISH"
    elif watch_gate and conf>=65 and diff>=15:
        raw_signal="BUY WATCH" if bull else "SELL WATCH"
        bias="SELECTIVE BULLISH WATCH" if bull else "SELECTIVE BEARISH WATCH"
    else:
        raw_signal="NO TRADE"
        bias="FILTERED OUT • CONDITIONS NOT STRONG ENOUGH"

    signal=raw_signal;opt=None;warn="";execution_ready=False
    direction="BUY" if bull else "SELL"

    # V2.5 starts freezing at WATCH stage, not only after a strong BUY/SELL.
    directional_setup=raw_signal in ("BUY","BUY WATCH","SELL","SELL WATCH")
    trigger_level,confirm_count,trigger_confirmed,trigger_frozen=confirm_trigger(
        direction,tv["spot"],tv["atr5"],directional_setup,instrument
    )

    buffer=max(tv["atr5"]*.15,5.0)
    if bull:
        buy_above=round(trigger_level if trigger_level is not None else tv["spot"]+buffer,2)
        sell_below=round(tv["spot"]-buffer,2)
    else:
        buy_above=round(tv["spot"]+buffer,2)
        sell_below=round(trigger_level if trigger_level is not None else tv["spot"]-buffer,2)

    # Keep showing a valid option while a directional setup is alive.
    contract_quote=None
    if directional_setup and cfg["mode"]=="OPTION":
        try:
            opt=choose_option(fetch_oc(),tv["spot"],bull,tv["atr5"],conf)
        except Exception as e:
            warn="Signal available, but no tradable option quote: "+str(e)
    elif directional_setup and cfg["mode"]=="FUTURE":
        contract_quote=commodity_contract(cfg,tv,bull,trigger_level,trigger_confirmed)

    # A trade can become ready only from a strong BUY/SELL plus frozen-trigger 2/2.
    strong=raw_signal in ("BUY","SELL")
    if strong:
        execution_ready=bool(strong_gate and trigger_confirmed and ((opt and opt.get("tradable")) if cfg["mode"]=="OPTION" else (contract_quote and contract_quote.get("tradable"))))
        if execution_ready:
            signal=direction
            bias=("BULLISH" if bull else "BEARISH")+" • FROZEN TRIGGER CONFIRMED 2/2"
        else:
            signal=("BUY WATCH" if bull else "SELL WATCH")
            waiting=[]
            if not trigger_confirmed:
                waiting.append(f"frozen {cfg['short']} trigger {confirm_count}/2")
            if cfg["mode"]=="OPTION" and not opt:
                waiting.append("valid option quote")
            if cfg["mode"]=="FUTURE" and not contract_quote:
                waiting.append("futures reference")
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
        "reason":f"{conf}/100 • 5m {tv_rating(tv['rec5'])} • 15m {tv_rating(tv['rec15'])} • ADX 5m {tv['adx5']:.1f} / 15m {tv['adx15']:.1f} • Quality {quality_passed}/{len(quality_checks)} • {entry_state} • {market_regime} • Trigger {confirm_count}/2 • {trigger_note}",
        "rating5":tv_rating(tv["rec5"]),"rating15":tv_rating(tv["rec15"]),
        "rsi5":tv["rsi5"],"rsi15":tv["rsi15"],"ema10_5":tv["ema10_5"],
        "ema20_5":tv["ema20_5"],"ema10_15":tv["ema10_15"],"ema20_15":tv["ema20_15"],
        "macd_5":tv["macd5"],"macd_signal_5":tv["macds5"],"adx5":tv["adx5"],
        "macd_15":tv["macd15"],"macd_signal_15":tv["macds15"],"adx15":tv["adx15"],
        "ema50_5":tv["ema50_5"],"ema50_15":tv["ema50_15"],"vwap5":tv["vwap5"],
        "atr5":tv["atr5"],"buy_above":buy_above,"sell_below":sell_below,
        "checks":checks,"quality_checks":[{"label":k,"ok":v} for k,v in quality_checks.items()],
        "quality_passed":quality_passed,"quality_total":len(quality_checks),
        "quality_grade":("A+" if strong_gate and conf>=90 and quality_passed==len(quality_checks) else "A" if strong_gate else "B" if watch_gate else "C" if conf>=55 else "BLOCKED"),
        "entry_state":entry_state,"entry_state_detail":entry_state_detail,"market_regime":market_regime,
        "candle_confirmation":candle_confirmation,"breakout_confirmation":breakout_confirmation,
        "option":opt,"contract_quote":contract_quote,"execution_ready":execution_ready,
        "instrument_key":instrument,"instrument_label":cfg["label"],"instrument_short":cfg["short"],"market_mode":cfg["mode"],"market_name":cfg["market"],
        "trigger_hit":trigger_confirmed,"trigger_confirmed":trigger_confirmed,
        "trigger_confirmations":confirm_count,"trigger_level":trigger_level,
        "trigger_frozen":trigger_frozen,
        "trigger_started_at":_trigger_states.get(instrument,{}).get("started_at"),
        "market_open":market_open_now(cfg),"data_source":("TradingView + NSE" if cfg["mode"]=="OPTION" else "TradingView MCX continuous futures reference"),
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



@app.route("/api/commodity-quote",methods=["GET"])
def api_commodity_quote():
    instrument=(request.args.get("instrument") or "CRUDEOILM").upper().strip()
    if instrument not in INSTRUMENTS:
        return jsonify({"error":f"Unknown instrument '{instrument}'."}),400
    if INSTRUMENTS[instrument].get("mode")!="FUTURE":
        return jsonify({"error":f"{instrument} is not configured as an MCX futures instrument."}),400
    try:
        out=commodity_quote(instrument)
        if str(out.get("instrument") or "").upper()!=instrument:
            raise RuntimeError("Commodity quote instrument mismatch.")
        out["app_version"]="3.7.1"
        return jsonify(out)
    except Exception as e:
        return jsonify({
            "error":f"{instrument} live MCX reference unavailable: {e}",
            "instrument":instrument,
            "version":"3.7.1"
        }),503

@app.route("/api/signal",methods=["GET"])
def api_signal():
    instrument=(request.args.get("instrument") or "NIFTY").upper().strip()
    if instrument not in INSTRUMENTS:
        return jsonify({
            "error":f"Unknown instrument '{instrument}'. Request rejected; NIFTY fallback disabled.",
            "version":"3.7.1"
        }),400

    now=time.time()
    c=_cache.get(instrument)

    if c and c.get("signal") is not None and now-c.get("ts",0)<12:
        x=dict(c["signal"])
        x["app_version"]="3.7.1"
        return jsonify(x)

    try:
        r=build_signal(instrument)
        received=str(r.get("instrument_key") or "").upper()
        if received!=instrument:
            raise RuntimeError(
                f"Data integrity check failed: requested {instrument}, engine returned {received or 'UNKNOWN'}."
            )
        r["app_version"]="3.7.1"
        r["stale"]=False
        _cache[instrument]={"signal":r,"ts":now}
        return jsonify(r)

    except Exception as e:
        c=_cache.get(instrument)
        if c and c.get("signal") is not None:
            age=max(0.0,now-float(c.get("ts",0) or 0))
            cached_instrument=str(c["signal"].get("instrument_key") or "").upper()
            if cached_instrument==instrument and age<=120:
                x=dict(c["signal"])
                x["app_version"]="3.7.1"
                x["stale"]=True
                x["cache_age_seconds"]=round(age,1)
                x["warning"]=(
                    f"{instrument} live refresh failed. "
                    f"Showing SAME-INSTRUMENT cached data ({age:.0f}s old): {e}"
                )
                return jsonify(x)

        return jsonify({
            "error":(
                f"{instrument} DATA UNAVAILABLE. No valid same-instrument fallback. "
                f"No NIFTY/other-instrument data will be substituted. Source error: {e}"
            ),
            "instrument_key":instrument,
            "instrument_label":INSTRUMENTS[instrument]["label"],
            "version":"3.7.1"
        }),503

if __name__=="__main__":
    import os
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT","10000")))
