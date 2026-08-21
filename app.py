from flask import Flask, jsonify, request
import requests, time, math
from datetime import datetime
from zoneinfo import ZoneInfo

app = Flask(__name__)
IST = ZoneInfo("Asia/Kolkata")

VERSION = "3.7"
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
_trigger_state = {"direction": None, "level": None, "count": 0, "confirmed": False, "misses": 0, "started_at": None}
_locked_quote_cache = {}

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True, "version": VERSION})

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
        "close|5","open|5","Recommend.All|5","RSI|5","EMA10|5","EMA20|5","EMA50|5",
        "MACD.macd|5","MACD.signal|5","ADX|5","ATR|5","high|5","low|5","VWAP|5",
        "open|15","high|15","low|15","close|15","Recommend.All|15","RSI|15",
        "EMA10|15","EMA20|15","EMA50|15","MACD.macd|15","MACD.signal|15","ADX|15"
    ]
    payload = {"symbols":{"tickers":["NSE:NIFTY"],"query":{"types":[]}},"columns":cols,"range":[0,1]}
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
        "spot": spot, "open5": f("open|5", spot),
        "rec5": f("Recommend.All|5"), "rsi5": f("RSI|5",50),
        "ema10_5": f("EMA10|5",spot), "ema20_5": f("EMA20|5",spot), "ema50_5": f("EMA50|5",spot),
        "macd5": f("MACD.macd|5"), "macds5": f("MACD.signal|5"), "adx5": f("ADX|5"),
        "atr5": max(f("ATR|5",1), .01), "high5": f("high|5",spot), "low5": f("low|5",spot),
        "vwap5": f("VWAP|5",spot),
        "open15": f("open|15",spot), "high15": f("high|15",spot), "low15": f("low|15",spot),
        "close15": f("close|15",spot), "rec15": f("Recommend.All|15"), "rsi15": f("RSI|15",50),
        "ema10_15": f("EMA10|15",spot), "ema20_15": f("EMA20|15",spot), "ema50_15": f("EMA50|15",spot),
        "macd15": f("MACD.macd|15"), "macds15": f("MACD.signal|15"), "adx15": f("ADX|15")
    }

def score(tv, bull=True):
    s = 0
    checks = []
    def add(label, ok, pts):
        nonlocal s
        ok = bool(ok)
        checks.append({"label": label, "ok": ok})
        if ok: s += pts
    add("5m rating confirms", tv["rec5"] >= .1 if bull else tv["rec5"] <= -.1, 10)
    add("15m rating confirms", tv["rec15"] >= .1 if bull else tv["rec15"] <= -.1, 15)
    add("5m EMA 10/20 trend", tv["ema10_5"] > tv["ema20_5"] if bull else tv["ema10_5"] < tv["ema20_5"], 10)
    add("15m EMA 10/20 trend", tv["ema10_15"] > tv["ema20_15"] if bull else tv["ema10_15"] < tv["ema20_15"], 12)
    add("Price vs EMA50 5m", tv["spot"] > tv["ema50_5"] if bull else tv["spot"] < tv["ema50_5"], 8)
    add("Price vs EMA50 15m", tv["spot"] > tv["ema50_15"] if bull else tv["spot"] < tv["ema50_15"], 10)
    add("Price vs VWAP", tv["spot"] > tv["vwap5"] if bull else tv["spot"] < tv["vwap5"], 10)
    add("RSI 5m healthy", (52 <= tv["rsi5"] <= 68) if bull else (32 <= tv["rsi5"] <= 48), 5)
    add("RSI 15m healthy", (50 <= tv["rsi15"] <= 68) if bull else (32 <= tv["rsi15"] <= 50), 5)
    add("MACD 5m confirms", tv["macd5"] > tv["macds5"] if bull else tv["macd5"] < tv["macds5"], 5)
    add("MACD 15m confirms", tv["macd15"] > tv["macds15"] if bull else tv["macd15"] < tv["macds15"], 5)
    add("ADX 5m ≥ 20", tv["adx5"] >= 20, 3)
    add("ADX 15m ≥ 18", tv["adx15"] >= 18, 2)
    return s, checks

def quality_gate(tv, bull, score_value):
    same_direction = (tv["rec5"]>=.1 and tv["rec15"]>=.1) if bull else (tv["rec5"]<=-.1 and tv["rec15"]<=-.1)
    ema_trend = (tv["ema10_5"]>tv["ema20_5"] and tv["ema10_15"]>tv["ema20_15"]) if bull else (tv["ema10_5"]<tv["ema20_5"] and tv["ema10_15"]<tv["ema20_15"])
    long_trend = (tv["spot"]>tv["ema50_5"] and tv["spot"]>tv["ema50_15"]) if bull else (tv["spot"]<tv["ema50_5"] and tv["spot"]<tv["ema50_15"])
    vwap_ok = tv["spot"]>tv["vwap5"] if bull else tv["spot"]<tv["vwap5"]
    macd_ok = (tv["macd5"]>tv["macds5"] and tv["macd15"]>tv["macds15"]) if bull else (tv["macd5"]<tv["macds5"] and tv["macd15"]<tv["macds15"])
    rsi_ok = ((50<=tv["rsi5"]<=68) and (50<=tv["rsi15"]<=68)) if bull else ((32<=tv["rsi5"]<=50) and (32<=tv["rsi15"]<=50))
    if tv["adx5"] < 15:
        market_regime, regime_ok = "CHOP / LOW TREND", False
    elif tv["adx5"] < 20 or tv["adx15"] < 18:
        market_regime, regime_ok = "TRANSITION / DEVELOPING TREND", False
    else:
        market_regime, regime_ok = "TRENDING", True
    candle_ok = tv["spot"] > tv["open5"] if bull else tv["spot"] < tv["open5"]
    buf = max(tv["atr5"] * .10, 1.5)
    structure = max(tv["ema20_5"],tv["ema50_5"],tv["vwap5"]) if bull else min(tv["ema20_5"],tv["ema50_5"],tv["vwap5"])
    breakout_ok = tv["spot"] >= structure + buf if bull else tv["spot"] <= structure - buf
    hard = {
        ("BUY: 5m + 15m direction agree" if bull else "SELL: 5m + 15m direction agree"): same_direction,
        ("BUY: EMA 10/20 aligned" if bull else "SELL: EMA 10/20 aligned"): ema_trend,
        ("BUY: price beyond EMA50s" if bull else "SELL: price below EMA50s"): long_trend,
        ("BUY: price above VWAP" if bull else "SELL: price below VWAP"): vwap_ok,
        ("BUY: MACD confirms 5m + 15m" if bull else "SELL: MACD confirms 5m + 15m"): macd_ok,
        "Trend regime strong enough": regime_ok,
        "RSI healthy / not stretched": rsi_ok,
        ("5m bullish candle confirms" if bull else "5m bearish candle confirms"): candle_ok,
        "Breakout clears structure + buffer": breakout_ok
    }
    passed = sum(1 for v in hard.values() if v)
    core_prepare = same_direction and ema_trend and long_trend and vwap_ok and rsi_ok and score_value >= 65
    confirmed = all(hard.values()) and score_value >= 80
    prepare = core_prepare and passed >= 6 and not confirmed
    if confirmed:
        state, detail = "CONFIRMED", "All new-entry conditions aligned; still wait for frozen trigger and tradable option quote."
    elif prepare:
        state, detail = "PREPARE", "Setup is developing, but execution is blocked until candle/breakout/regime confirmation completes."
    elif market_regime == "CHOP / LOW TREND":
        state, detail = "AVOID", "Low-trend/choppy regime. New entries are intentionally blocked."
    else:
        state, detail = "WAIT", "Conditions are mixed. Wait for stronger multi-timeframe alignment."
    return confirmed, prepare, hard, passed, state, detail, market_regime, candle_ok, breakout_ok

def fetch_oc():
    s = requests.Session()
    s.headers.update(NSE_HEADERS)
    s.get(NSE_HOME, timeout=10)
    s.get(NSE_OC_PAGE, timeout=10)
    ci = s.get(NSE_OC_CONTRACT, params={"symbol":"NIFTY"}, timeout=12)
    ci.raise_for_status()
    info = ci.json()
    expiries = info.get("expiryDates",[]) or info.get("records",{}).get("expiryDates",[])
    if not expiries:
        raise RuntimeError("NSE returned no NIFTY expiry dates.")
    ex = expiry(expiries)
    r = s.get(NSE_OC_V3, params={"type":"Indices","symbol":"NIFTY","expiry":ex}, timeout=12)
    if r.status_code in (401,403):
        s = requests.Session(); s.headers.update(NSE_HEADERS)
        s.get(NSE_HOME,timeout=10); s.get(NSE_OC_PAGE,timeout=10)
        r = s.get(NSE_OC_V3, params={"type":"Indices","symbol":"NIFTY","expiry":ex}, timeout=12)
    r.raise_for_status()
    j = r.json()
    if not j.get("records",{}).get("data"):
        raise RuntimeError("NSE v3 option chain returned no contracts.")
    return j

def pexp(x):
    for f in ("%d-%b-%Y","%d-%b-%y"):
        try: return datetime.strptime(x,f).date()
        except Exception: pass
    return None

def expiry(xs):
    today = datetime.now(IST).date()
    p = [(pexp(x),x) for x in xs]
    p = [x for x in p if x[0]]
    f = [x for x in p if x[0] >= today]
    u = f or p
    if not u: raise RuntimeError("No usable expiry.")
    return sorted(u)[0][1]

def choose_option(oc, spot, bull, atr5=None, confidence=0):
    rec = oc["records"]; ex = expiry(rec.get("expiryDates",[])); typ = "CE" if bull else "PE"
    atm = round(spot/50)*50; c=[]; seen=[]
    def num(d,*keys):
        for k in keys:
            v=d.get(k)
            if v not in (None,"","-"):
                try:return float(str(v).replace(",",""))
                except Exception:pass
        return 0.0
    for row in rec["data"]:
        row_exp=row.get("expiryDate")
        if (row_exp and row_exp!=ex) or not row.get(typ): continue
        side=row[typ]; st=float(row.get("strikePrice",0) or 0)
        if abs(st-atm)>400: continue
        l=num(side,"lastPrice","ltp","last_price")
        b=num(side,"buyPrice1","bidPrice","bidprice","bid","bestBid")
        a=num(side,"sellPrice1","askPrice","askprice","ask","bestAsk")
        bq=num(side,"buyQuantity1","bidQty","bidQuantity","bestBidQty")
        aq=num(side,"sellQuantity1","askQty","askQuantity","bestAskQty")
        v=int(num(side,"totalTradedVolume","volume")); oi=int(num(side,"openInterest","oi"))
        seen.append((abs(st-atm),st,l,b,a,v,oi,bq,aq))
        if l<=0 or b<=0 or a<=0 or a<b: continue
        mid=(a+b)/2; sp=(a-b)/mid*100 if mid>0 else 999
        if sp>15 or v<100 or oi<=0: continue
        depth_bonus=min(math.log10(max(bq+aq,1)+1),5)
        rank=(abs(st-atm)/50)*6 + sp*3 - min(math.log10(v+1),6)*4 - min(math.log10(oi+1),7)*3 - depth_bonus*2
        c.append((rank,st,l,b,a,v,oi,sp,bq,aq))
    if not c:
        nearby=sorted(seen)[:5]
        detail="; ".join(f"{int(st)} {typ}: LTP {l:.2f}, bid {b:.2f}, ask {a:.2f}, vol {v}, OI {oi}" for _,st,l,b,a,v,oi,_,_ in nearby)
        raise RuntimeError("No nearby option passed liquidity checks. NSE quotes seen: "+detail)
    _,st,l,b,a,v,oi,sp,bq,aq = sorted(c)[0]
    entry=a; spread=a-b
    atr_ratio=(float(atr5 or 0)/max(float(spot),1))*100
    base_pct=.14 if confidence>=90 else .18 if confidence<80 else .16
    if atr_ratio>.08: base_pct+=.02
    risk=min(max(entry*base_pct,spread*3,entry*.10),entry*.22)
    rr1=1.50 if confidence>=90 else 1.35
    rr2=2.40 if confidence>=90 else 2.15
    liq="GOOD" if sp<=5 and v>=1000 and bq>0 and aq>0 else "FAIR"
    return {
        "contract":f"NIFTY {int(st)} {typ}","expiry":ex,"strike":st,"type":typ,
        "ltp":round(l,2),"bid":round(b,2),"ask":round(a,2),"entry":round(entry,2),
        "sl":round(max(entry-risk,.05),2),"target1":round(entry+rr1*risk,2),
        "target2":round(entry+rr2*risk,2),"risk_pct":round(risk/entry*100,1),
        "rr1":rr1,"rr2":rr2,"volume":v,"oi":oi,"bid_qty":int(bq),"ask_qty":int(aq),
        "spread_pct":round(sp,2),"liquidity":liq,"tradable":True
    }

def confirm_trigger(direction, spot, atr, setup_active):
    global _trigger_state
    def fresh():
        return {"direction":None,"level":None,"count":0,"confirmed":False,"misses":0,"started_at":None}
    if direction not in ("BUY","SELL"):
        _trigger_state=fresh(); return None,0,False,False
    if not setup_active:
        if _trigger_state["direction"]==direction and _trigger_state["level"] is not None:
            _trigger_state["misses"]+=1; _trigger_state["count"]=0
            if _trigger_state["misses"]<=2:
                return float(_trigger_state["level"]),0,bool(_trigger_state["confirmed"]),True
        _trigger_state=fresh(); return None,0,False,False
    if _trigger_state["direction"]!=direction or _trigger_state["level"] is None:
        buffer=max(float(atr)*.15,5.0)
        level=spot+buffer if direction=="BUY" else spot-buffer
        _trigger_state={"direction":direction,"level":round(level,2),"count":0,"confirmed":False,"misses":0,"started_at":datetime.now(IST).strftime("%H:%M:%S")}
    else:
        _trigger_state["misses"]=0
    level=float(_trigger_state["level"])
    beyond=spot>=level if direction=="BUY" else spot<=level
    if _trigger_state["confirmed"]:
        return level,2,True,True
    _trigger_state["count"] = _trigger_state["count"]+1 if beyond else 0
    if _trigger_state["count"]>=2: _trigger_state["confirmed"]=True
    return level,min(_trigger_state["count"],2),bool(_trigger_state["confirmed"]),True

def exact_option_quote(strike,opt_type,expiry_date):
    global _locked_quote_cache
    strike=float(strike); opt_type=str(opt_type).upper()
    cache_key=f"{int(strike)}-{opt_type}-{expiry_date or ''}"
    def num(d,*keys):
        for k in keys:
            v=d.get(k)
            if v not in (None,"","-"):
                try:return float(str(v).replace(",",""))
                except Exception:pass
        return 0.0
    try:
        oc=fetch_oc(); rec=oc["records"]; fallback=None
        for row in rec.get("data",[]):
            try: row_strike=float(row.get("strikePrice",0) or 0)
            except Exception: continue
            if row_strike!=strike: continue
            side=row.get(opt_type)
            if not side: continue
            row_exp=row.get("expiryDate") or side.get("expiryDate") or ""
            if expiry_date and row_exp and row_exp!=expiry_date:
                if fallback is None: fallback=(row,side,row_exp)
                continue
            l=num(side,"lastPrice","ltp","last_price"); b=num(side,"buyPrice1","bidPrice","bidprice","bid","bestBid")
            a=num(side,"sellPrice1","askPrice","askprice","ask","bestAsk")
            bq=num(side,"buyQuantity1","bidQty","bidQuantity","bestBidQty"); aq=num(side,"sellQuantity1","askQty","askQuantity","bestAskQty")
            v=int(num(side,"totalTradedVolume","volume")); oi=int(num(side,"openInterest","oi"))
            mid=(a+b)/2 if a>0 and b>0 else 0; sp=((a-b)/mid*100) if mid>0 and a>=b else None
            q={"contract":f"NIFTY {int(strike)} {opt_type}","expiry":row_exp or expiry_date,"strike":strike,"type":opt_type,
               "ltp":round(l,2),"bid":round(b,2),"ask":round(a,2),"bid_qty":int(bq),"ask_qty":int(aq),"volume":v,"oi":oi,
               "spread_pct":round(sp,2) if sp is not None else None,"source":"NSE live","stale":False,
               "quote_time":datetime.now(IST).strftime("%H:%M:%S")}
            if q["ltp"]>0:
                _locked_quote_cache[cache_key]={"quote":q,"ts":time.time()}; return q
        if fallback:
            row,side,row_exp=fallback
            l=num(side,"lastPrice","ltp","last_price"); b=num(side,"buyPrice1","bidPrice","bidprice","bid","bestBid")
            a=num(side,"sellPrice1","askPrice","askprice","ask","bestAsk")
            bq=num(side,"buyQuantity1","bidQty","bidQuantity","bestBidQty"); aq=num(side,"sellQuantity1","askQty","askQuantity","bestAskQty")
            v=int(num(side,"totalTradedVolume","volume")); oi=int(num(side,"openInterest","oi"))
            q={"contract":f"NIFTY {int(strike)} {opt_type}","expiry":row_exp or expiry_date,"strike":strike,"type":opt_type,
               "ltp":round(l,2),"bid":round(b,2),"ask":round(a,2),"bid_qty":int(bq),"ask_qty":int(aq),"volume":v,"oi":oi,
               "spread_pct":None,"source":"NSE same-strike fallback","stale":False,"quote_time":datetime.now(IST).strftime("%H:%M:%S")}
            if q["ltp"]>0:
                _locked_quote_cache[cache_key]={"quote":q,"ts":time.time()}; return q
        raise RuntimeError("Locked option contract not present in this NSE response.")
    except Exception as e:
        cached=_locked_quote_cache.get(cache_key)
        if cached and time.time()-cached["ts"]<=90:
            q=dict(cached["quote"]); q["stale"]=True; q["source"]="Last good NSE quote"; q["warning"]=str(e); return q
        raise RuntimeError("Locked option live quote unavailable: "+str(e))

def build_signal():
    tv=fetch_tv(); bs,bc=score(tv,True); ss,sc=score(tv,False)
    bull=bs>=ss; conf=bs if bull else ss; checks=bc if bull else sc; diff=abs(bs-ss)
    strong_gate,watch_gate,quality_checks,quality_passed,entry_state,entry_state_detail,market_regime,candle_confirmation,breakout_confirmation=quality_gate(tv,bull,conf)
    if strong_gate and conf>=80 and diff>=20:
        raw_signal="BUY" if bull else "SELL"; bias="HIGH-SELECTIVITY BULLISH" if bull else "HIGH-SELECTIVITY BEARISH"
    elif watch_gate and conf>=65 and diff>=15:
        raw_signal="BUY WATCH" if bull else "SELL WATCH"; bias="SELECTIVE BULLISH WATCH" if bull else "SELECTIVE BEARISH WATCH"
    else:
        raw_signal="NO TRADE"; bias="FILTERED OUT • CONDITIONS NOT STRONG ENOUGH"
    signal=raw_signal; opt=None; warn=""; execution_ready=False; direction="BUY" if bull else "SELL"
    directional_setup=raw_signal in ("BUY","BUY WATCH","SELL","SELL WATCH")
    trigger_level,confirm_count,trigger_confirmed,trigger_frozen=confirm_trigger(direction,tv["spot"],tv["atr5"],directional_setup)
    buffer=max(tv["atr5"]*.15,5.0)
    if bull:
        buy_above=round(trigger_level if trigger_level is not None else tv["spot"]+buffer,2); sell_below=round(tv["spot"]-buffer,2)
    else:
        buy_above=round(tv["spot"]+buffer,2); sell_below=round(trigger_level if trigger_level is not None else tv["spot"]-buffer,2)
    if directional_setup:
        try: opt=choose_option(fetch_oc(),tv["spot"],bull,tv["atr5"],conf)
        except Exception as e: warn="Signal available, but no tradable option quote: "+str(e)
    strong=raw_signal in ("BUY","SELL")
    if strong:
        execution_ready=bool(strong_gate and trigger_confirmed and opt and opt.get("tradable"))
        if execution_ready:
            signal=direction; bias=("BULLISH" if bull else "BEARISH")+" • FROZEN TRIGGER CONFIRMED 2/2"
        else:
            signal="BUY WATCH" if bull else "SELL WATCH"; waiting=[]
            if not trigger_confirmed: waiting.append(f"frozen NIFTY trigger {confirm_count}/2")
            if not opt: waiting.append("valid option quote")
            bias=("BULLISH WATCH" if bull else "BEARISH WATCH")+" • WAITING FOR "+(" + ".join(waiting) if waiting else "CONFIRMATION")
    elif raw_signal in ("BUY WATCH","SELL WATCH"):
        signal=raw_signal; bias=("BULLISH WATCH" if bull else "BEARISH WATCH")+f" • FROZEN TRIGGER {confirm_count}/2"
    else:
        confirm_count=0; trigger_confirmed=False
    trigger_note=f"Frozen {trigger_level:.2f}" if trigger_frozen and trigger_level is not None else "Not frozen"
    return {
        "spot":round(tv["spot"],2),"signal":signal,"bias":bias,"confidence":conf,
        "reason":f"{conf}/100 • 5m {tv_rating(tv['rec5'])} • 15m {tv_rating(tv['rec15'])} • ADX 5m {tv['adx5']:.1f} / 15m {tv['adx15']:.1f} • Quality {quality_passed}/{len(quality_checks)} • {entry_state} • {market_regime} • Trigger {confirm_count}/2 • {trigger_note}",
        "rating5":tv_rating(tv["rec5"]),"rating15":tv_rating(tv["rec15"]),"rsi5":tv["rsi5"],"rsi15":tv["rsi15"],
        "ema10_5":tv["ema10_5"],"ema20_5":tv["ema20_5"],"ema10_15":tv["ema10_15"],"ema20_15":tv["ema20_15"],
        "macd_5":tv["macd5"],"macd_signal_5":tv["macds5"],"adx5":tv["adx5"],
        "macd_15":tv["macd15"],"macd_signal_15":tv["macds15"],"adx15":tv["adx15"],
        "ema50_5":tv["ema50_5"],"ema50_15":tv["ema50_15"],"vwap5":tv["vwap5"],"atr5":tv["atr5"],
        "buy_above":buy_above,"sell_below":sell_below,"checks":checks,
        "quality_checks":[{"label":k,"ok":v} for k,v in quality_checks.items()],
        "quality_passed":quality_passed,"quality_total":len(quality_checks),
        "quality_grade":("A+" if strong_gate and conf>=90 and quality_passed==len(quality_checks) else "A" if strong_gate else "B" if watch_gate else "C" if conf>=55 else "BLOCKED"),
        "entry_state":entry_state,"entry_state_detail":entry_state_detail,"market_regime":market_regime,
        "candle_confirmation":candle_confirmation,"breakout_confirmation":breakout_confirmation,
        "option":opt,"execution_ready":execution_ready,"trigger_hit":trigger_confirmed,"trigger_confirmed":trigger_confirmed,
        "trigger_confirmations":confirm_count,"trigger_level":trigger_level,"trigger_frozen":trigger_frozen,
        "trigger_started_at":_trigger_state.get("started_at"),"market_open":market_open_now(),
        "data_source":"TradingView + NSE","warning":warn,"updated":datetime.now(IST).strftime("%d-%b %I:%M:%S %p")
    }

PAGE = r"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#07111f"><title>NIFTY Professional V3.7</title>
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
<h1>📈 NIFTY Professional Signals V3.7</h1>
<div class="sub">5m + 15m confirmation • confidence scoring • option liquidity • trade health • carry / exit review</div>
<div class="banner">Decision-support only. Public feeds may be delayed. Verify contract and premium in your broker before any real order.</div>

<div class="card"><div class="status"><span id="dot" class="dot"></span><span id="status">Starting…</span><span id="marketStatus" style="margin-left:auto">—</span></div>
<div class="price" id="spot">—</div><div id="signal" class="signal neutral">WAITING</div><div class="small" id="bias">Loading market data…</div><div class="small" id="updated">—</div><div id="error"></div></div>

<div class="card"><div class="small">Current market view</div><div id="marketViewSignal" style="font-size:18px;font-weight:850;margin-top:3px">—</div>
<div class="small">Signal confidence</div><div id="confidence" style="font-size:28px;font-weight:900">—</div><div class="progress"><div class="bar" id="confBar"></div></div><div class="small" id="reason">—</div></div>

<div class="card"><div class="small">V3.7 PROFESSIONAL ENTRY QUALITY ENGINE</div>
<div id="qualityGrade" style="font-size:26px;font-weight:900;margin-top:4px">—</div>
<div id="qualityState" style="font-size:18px;font-weight:850;margin-top:4px">WAIT</div>
<div class="small" id="qualitySummary">Waiting for market data…</div><div class="small" id="regimeDetail" style="margin-top:4px">Market regime: —</div>
<div style="height:8px"></div><div id="qualityChecks"></div></div>

<div class="card"><div class="small">Automatically selected option / locked contract</div><div id="contract" class="contract">—</div><div class="small" id="expiry">—</div><div class="small" id="liquidity">—</div><div style="height:10px"></div>
<div class="row3"><div class="kpi"><div class="t">OPTION LTP</div><div class="v" id="optionLtp">—</div></div><div class="kpi"><div class="t">BID</div><div class="v" id="bid">—</div></div><div class="kpi"><div class="t">ASK / ENTRY</div><div class="v" id="ask">—</div></div></div></div>

<div class="row"><div class="kpi"><div class="t">ENTRY</div><div class="v" id="entry">—</div></div><div class="kpi"><div class="t">STOP LOSS</div><div class="v" id="sl">—</div></div></div>
<div style="height:8px"></div><div class="row"><div class="kpi"><div class="t">TARGET 1</div><div class="v" id="t1">—</div></div><div class="kpi"><div class="t">TARGET 2 / EXIT</div><div class="v" id="t2">—</div></div></div>

<div class="card"><div class="small">V3.7 PROFESSIONAL TRADE HEALTH</div>
<div id="tradeHealth" style="font-size:25px;font-weight:900;margin-top:4px">NO ACTIVE TRADE</div>
<div id="tradeAction" style="font-size:17px;font-weight:850;margin-top:4px;color:#9bb0c9">Waiting for a locked trade.</div>
<div id="tradeHealthReasons" class="small" style="margin-top:7px">Health combines locked P/L, current trend, VWAP, EMA, MACD and ADX.</div></div>

<div class="card"><div class="small">V3.7 END-OF-DAY POSITION DECISION</div>
<div id="eodDecision" style="font-size:25px;font-weight:900;margin-top:4px">NO ACTIVE POSITION</div>
<div id="eodScore" class="small" style="margin-top:4px">Carry score — / 100</div>
<div id="eodAction" style="font-size:17px;font-weight:850;margin-top:6px;color:#9bb0c9">Waiting for a locked trade.</div>
<div id="eodReasons" class="small" style="margin-top:7px">Final carry/exit review becomes active near market close.</div>
<div class="small" style="margin-top:7px">Review window: 3:05 PM onward (India time).</div></div>

<div class="card"><div class="small">V3.7 SESSION TRADE JOURNAL</div>
<div class="row"><div class="kpi"><div class="t">EVENTS</div><div class="v" id="journalCount">0</div></div><div class="kpi"><div class="t">LAST EVENT</div><div class="v" id="journalLast" style="font-size:14px">—</div></div></div>
<button class="secondary" id="exportJournalBtn">COPY JOURNAL SUMMARY</button></div>

<div class="card"><div class="small">Trade monitor</div><div id="tradeState" class="state">NO ACTIVE TRADE</div><div class="small" id="tradeDetail">Alerts work while this page stays open.</div>
<button class="secondary" id="notifyBtn">ENABLE NOTIFICATIONS / VIBRATION</button><button class="danger" id="resetTradeBtn">RESET TRADE MONITOR</button></div>

<div class="card"><div class="small" style="margin-bottom:8px">Current market indicators</div><div class="row3">
<div><div class="small">Rating 5m</div><b id="rating5">—</b></div><div><div class="small">Rating 15m</div><b id="rating15">—</b></div>
<div><div class="small">RSI 5m</div><b id="rsi5">—</b></div><div><div class="small">RSI 15m</div><b id="rsi15">—</b></div>
<div><div class="small">EMA 10/20 5m</div><b id="ema5">—</b></div><div><div class="small">EMA 10/20 15m</div><b id="ema15">—</b></div>
<div><div class="small">MACD 5m</div><b id="macd5">—</b></div><div><div class="small">ADX 5m</div><b id="adx5">—</b></div>
<div><div class="small">ATR 5m</div><b id="atr5">—</b></div><div><div class="small">ADX 15m</div><b id="adx15">—</b></div>
<div><div class="small">VWAP 5m</div><b id="vwap5">—</b></div><div><div class="small">EMA 50 5m</div><b id="ema50_5">—</b></div>
<div><div class="small">EMA 50 15m</div><b id="ema50_15">—</b></div><div><div class="small">MACD 15m</div><b id="macd15">—</b></div>
</div><div style="height:10px"></div><div id="checks"></div></div>

<div class="card"><div class="small" style="margin-bottom:8px">Current scanner trigger levels</div><div class="row">
<div class="kpi"><div class="t">NIFTY BUY ABOVE</div><div class="v" id="buyAbove">—</div></div>
<div class="kpi"><div class="t">NIFTY SELL BELOW</div><div class="v" id="sellBelow">—</div></div></div></div>
<button id="refresh">REFRESH NOW</button></div>

<script>
"use strict";
const $=id=>document.getElementById(id);
const fmt=x=>(x==null||!Number.isFinite(Number(x)))?"—":Number(x).toLocaleString("en-IN",{minimumFractionDigits:2,maximumFractionDigits:2});
let notificationsEnabled=localStorage.getItem("niftyV37Notify")==="1";
function notify(title,body){
 if(!notificationsEnabled)return;
 if(navigator.vibrate)navigator.vibrate([180,80,180]);
 if("Notification"in window&&Notification.permission==="granted"){try{new Notification(title,{body})}catch(e){}}
}
function readTrade(){try{return JSON.parse(localStorage.getItem("niftyV37Trade")||"null")}catch(e){return null}}
function saveTrade(t){localStorage.setItem("niftyV37Trade",JSON.stringify(t))}
function clearTrade(){localStorage.removeItem("niftyV37Trade");updateTradeUI(null)}
function journal(){try{return JSON.parse(localStorage.getItem("niftyV37Journal")||"[]")}catch(e){return []}}
function addJournal(kind,t,detail=""){const j=journal();j.unshift({time:new Date().toLocaleString(),kind,contract:t&&t.contract?t.contract:"—",detail});localStorage.setItem("niftyV37Journal",JSON.stringify(j.slice(0,50)));renderJournal()}
function renderJournal(){const j=journal();$("journalCount").textContent=j.length;$("journalLast").textContent=j.length?`${j[0].kind} • ${j[0].time}`:"—"}
function updateTradeUI(t){
 if(!t){$("tradeState").textContent="NO ACTIVE TRADE";$("tradeDetail").textContent="Alerts work while this page stays open.";return}
 $("tradeState").textContent=t.state||"ENTRY LOCKED / MONITORING";
 const cp=Number(t.currentLtp||0),entry=Number(t.entry||0),pnl=(cp>0&&entry>0)?((cp-entry)/entry*100):null;
 $("tradeDetail").textContent=`${t.contract} • Locked Entry ₹${fmt(entry)}${cp>0?` • Current ₹${fmt(cp)}`:""}${pnl!==null?` • Ref P/L ${pnl>=0?"+":""}${pnl.toFixed(1)}%`:""} • SL ₹${fmt(t.sl)} • T1 ₹${fmt(t.t1)} • T2 ₹${fmt(t.t2)}`;
}
function indiaClock(){
 const parts=new Intl.DateTimeFormat("en-GB",{timeZone:"Asia/Kolkata",hour12:false,hour:"2-digit",minute:"2-digit"}).formatToParts(new Date());
 const o={};parts.forEach(x=>o[x.type]=x.value);return {hour:Number(o.hour||0),minute:Number(o.minute||0)}
}
function expiryDays(exp){
 if(!exp)return null;const m=String(exp).match(/(\d{1,2})-([A-Za-z]{3})-(\d{4})/);if(!m)return null;
 const months={Jan:0,Feb:1,Mar:2,Apr:3,May:4,Jun:5,Jul:6,Aug:7,Sep:8,Oct:9,Nov:10,Dec:11};
 const mon=months[m[2][0].toUpperCase()+m[2].slice(1,3).toLowerCase()];if(mon==null)return null;
 const target=Date.UTC(Number(m[3]),mon,Number(m[1]));const now=new Date();const today=Date.UTC(now.getUTCFullYear(),now.getUTCMonth(),now.getUTCDate());return Math.round((target-today)/86400000)
}
function evaluateTradeHealth(t,d){
 if(!t){$("tradeHealth").textContent="NO ACTIVE TRADE";$("tradeAction").textContent="Waiting for a locked trade.";$("tradeHealthReasons").textContent="Health combines locked P/L, current trend, VWAP, EMA, MACD and ADX.";return}
 const bull=t.type==="CE";let score=0,reasons=[];const cp=Number(t.currentLtp||0),entry=Number(t.entry||0),pnl=(cp>0&&entry>0)?((cp-entry)/entry*100):0;
 const dir5=bull?["BUY","STRONG BUY"].includes(d.rating5):["SELL","STRONG SELL"].includes(d.rating5);
 const dir15=bull?["BUY","STRONG BUY"].includes(d.rating15):["SELL","STRONG SELL"].includes(d.rating15);
 const ema5=bull?d.ema10_5>d.ema20_5:d.ema10_5<d.ema20_5;const ema15=bull?d.ema10_15>d.ema20_15:d.ema10_15<d.ema20_15;
 const vwap=bull?d.spot>d.vwap5:d.spot<d.vwap5;const macd=bull?d.macd_5>d.macd_signal_5:d.macd_5<d.macd_signal_5;
 if(dir5)score+=18;else reasons.push("5m direction against trade");if(dir15)score+=22;else reasons.push("15m direction against trade");
 if(ema5)score+=12;else reasons.push("5m EMA trend weakened");if(ema15)score+=14;else reasons.push("15m EMA trend weakened");
 if(vwap)score+=12;else reasons.push("price crossed adverse side of VWAP");if(macd)score+=10;else reasons.push("5m MACD no longer confirms");
 if(Number(d.adx5)>=18)score+=6;else reasons.push("ADX 5m weak");if(Number(d.adx15)>=18)score+=6;else reasons.push("ADX 15m weak");
 if(pnl<=-12)score-=20;else if(pnl<=-7)score-=10;
 let label="HEALTHY",action="HOLD PLAN / MONITOR",color="#22c55e";
 if(score<35||pnl<=-15){label="EXIT REVIEW";action="SETUP INVALIDATION RISK — review broker position and stop immediately";color="#ff7690"}
 else if(score<55){label="WEAKENING";action="DEFENSIVE MODE — avoid adding; consider tighter risk";color="#ef4444"}
 else if(score<75){label="CAUTION";action="MONITOR CLOSELY — momentum is mixed";color="#f59e0b"}
 $("tradeHealth").textContent=`${label} • ${Math.max(0,Math.min(100,score))}/100`;$("tradeHealth").style.color=color;$("tradeAction").textContent=action;$("tradeHealthReasons").textContent=reasons.length?reasons.slice(0,4).join(" • "):"Current trend structure remains aligned with the locked trade.";
}
function evaluateEodDecision(t,d){
 if(!t){$("eodDecision").textContent="NO ACTIVE POSITION";$("eodScore").textContent="Carry score — / 100";$("eodAction").textContent="Waiting for a locked trade.";return}
 const bull=t.type==="CE";let score=0,reasons=[];
 const dir15=bull?["BUY","STRONG BUY"].includes(d.rating15):["SELL","STRONG SELL"].includes(d.rating15);
 const ema15=bull?d.ema10_15>d.ema20_15:d.ema10_15<d.ema20_15;const ema50=bull?d.spot>d.ema50_15:d.spot<d.ema50_15;
 const vwap=bull?d.spot>d.vwap5:d.spot<d.vwap5;const macd15=bull?d.macd_15>d.macd_signal_15:d.macd_15<d.macd_signal_15;
 const rsi15=Number(d.rsi15),rsiHealthy=bull?(rsi15>=50&&rsi15<=68):(rsi15>=32&&rsi15<=50);const adx15=Number(d.adx15||0);
 const cp=Number(t.currentLtp||0),entry=Number(t.entry||0),pnl=(cp>0&&entry>0)?((cp-entry)/entry*100):0;const days=expiryDays(t.expiry);
 const add=(ok,pts,neg)=>{if(ok)score+=pts;else reasons.push(neg)};
 add(dir15,22,"15m direction against position");add(ema15,16,"15m EMA 10/20 trend adverse");add(ema50,14,"price on adverse side of 15m EMA50");
 add(vwap,12,"price on adverse side of VWAP");add(macd15,12,"15m MACD momentum adverse");add(adx15>=20,10,"15m trend strength below 20");
 add(rsiHealthy,8,"15m RSI weak or stretched");add(pnl>-8,6,"position already materially below reference entry");
 if(days!==null&&days<=1){score-=25;reasons.push("expiry is too close for comfortable overnight carry")}else if(days!==null&&days<=2){score-=10;reasons.push("near-expiry overnight theta/gap risk")}
 score=Math.max(0,Math.min(100,score));const c=indiaClock(),mins=c.hour*60+c.minute,review=mins>=15*60+5;
 let label="REVIEW LATER",action="Intraday monitoring continues; final carry/exit decision activates near market close.",color="#9bb0c9";
 if(review){
   if(score>=75&&dir15&&ema15&&vwap&&macd15&&adx15>=20&&!(days!==null&&days<=1)){label="CARRY FORWARD CANDIDATE";action="Overnight carry conditions are comparatively strong. Re-check broker quote, gap risk and position size before close.";color="#22c55e"}
   else if(score>=55&&dir15&&ema15){label="HOLD ONLY WITH CAUTION";action="Mixed overnight quality. Consider reducing exposure or exiting unless your broker risk plan explicitly allows the carry.";color="#f59e0b"}
   else{label="EXIT BEFORE CLOSE";action="Overnight carry quality is weak. Review the broker position and consider closing before market close.";color="#ff7690"}
 }
 $("eodDecision").textContent=label;$("eodDecision").style.color=color;$("eodScore").textContent=`Carry score ${score} / 100`;$("eodAction").textContent=action;$("eodReasons").textContent=reasons.slice(0,5).join(" • ")||"Current carry evidence remains supportive.";
}
async function refreshLockedTrade(){
 const t=readTrade();if(!t)return;
 try{
   const r=await fetch(`/api/locked-option?strike=${encodeURIComponent(t.strike)}&type=${encodeURIComponent(t.type)}&expiry=${encodeURIComponent(t.expiry||"")}`,{cache:"no-store"});
   const q=await r.json();if(!r.ok||q.error)throw new Error(q.error||"quote unavailable");
   t.currentLtp=Number(q.ltp||0);
   const p=t.currentLtp,effectiveSl=Number(t.trailingSl||0)>0?Number(t.trailingSl):Number(t.sl);
   if((t.state==="ENTRY LOCKED / MONITORING"||t.state==="TARGET 1 HIT")&&p<=effectiveSl){t.state="STOP LOSS HIT / CLOSED";addJournal("STOP / TRAIL HIT",t,`LTP ₹${fmt(p)}`);notify("🛑 STOP / TRAIL HIT",`${t.contract} • LTP ₹${fmt(p)}`)}
   else if((t.state==="ENTRY LOCKED / MONITORING"||t.state==="TARGET 1 HIT")&&p>=Number(t.t2)){t.state="TARGET 2 HIT / CLOSED";addJournal("TARGET 2",t,`LTP ₹${fmt(p)}`);notify("🏆 TARGET 2 HIT",`${t.contract} • LTP ₹${fmt(p)}`)}
   else if(t.state==="ENTRY LOCKED / MONITORING"&&p>=Number(t.t1)){t.state="TARGET 1 HIT";t.trailingSl=Math.max(Number(t.entry),Number(t.sl));addJournal("TARGET 1",t,`Trail ₹${fmt(t.trailingSl)}`);notify("✅ TARGET 1 HIT",`${t.contract} • protective trail moved to ₹${fmt(t.trailingSl)}`)}
   saveTrade(t);updateTradeUI(t);
 }catch(e){}
}
function monitorTrade(d){
 let t=readTrade();if(t){updateTradeUI(t);refreshLockedTrade();evaluateTradeHealth(t,d);evaluateEodDecision(t,d);return}
 const active=Boolean(d.option&&d.execution_ready&&d.trigger_confirmed&&d.option.tradable);if(!active)return;
 t={contract:d.option.contract,expiry:d.option.expiry,strike:Number(d.option.strike),type:d.option.type,entry:Number(d.option.entry),sl:Number(d.option.sl),t1:Number(d.option.target1),t2:Number(d.option.target2),currentLtp:Number(d.option.ltp||0),state:"ENTRY LOCKED / MONITORING",lockedAt:d.updated,trailingSl:0};
 saveTrade(t);addJournal("ENTRY LOCKED",t,`Entry ₹${fmt(t.entry)} • SL ₹${fmt(t.sl)} • T1 ₹${fmt(t.t1)} • T2 ₹${fmt(t.t2)}`);notify("✅ ENTRY REFERENCE LOCKED",`${t.contract} • Entry ₹${fmt(t.entry)}`);updateTradeUI(t)
}
function render(d){
 $("spot").textContent=fmt(d.spot);$("updated").textContent=`Updated ${d.updated} • ${d.data_source}`;$("marketStatus").textContent=d.market_open?"MARKET OPEN":"MARKET CLOSED";
 $("confidence").textContent=d.confidence+" / 100";$("confBar").style.width=d.confidence+"%";$("marketViewSignal").textContent=`${d.signal} • 5m ${d.rating5} • 15m ${d.rating15}`;$("reason").textContent=d.reason;
 $("buyAbove").textContent=fmt(d.buy_above);$("sellBelow").textContent=fmt(d.sell_below);$("rating5").textContent=d.rating5;$("rating15").textContent=d.rating15;$("rsi5").textContent=fmt(d.rsi5);$("rsi15").textContent=fmt(d.rsi15);
 $("ema5").textContent=`${fmt(d.ema10_5)} / ${fmt(d.ema20_5)}`;$("ema15").textContent=`${fmt(d.ema10_15)} / ${fmt(d.ema20_15)}`;$("macd5").textContent=`${fmt(d.macd_5)} / ${fmt(d.macd_signal_5)}`;$("adx5").textContent=fmt(d.adx5);$("atr5").textContent=fmt(d.atr5);$("adx15").textContent=fmt(d.adx15);$("vwap5").textContent=fmt(d.vwap5);$("ema50_5").textContent=fmt(d.ema50_5);$("ema50_15").textContent=fmt(d.ema50_15);$("macd15").textContent=`${fmt(d.macd_15)} / ${fmt(d.macd_signal_15)}`;
 $("checks").innerHTML=d.checks.map(x=>`<span class="pill ${x.ok?"ok":"bad"}">${x.ok?"✓":"✕"} ${x.label}</span>`).join("");
 const q=d.quality_grade||"—";$("qualityGrade").textContent=q==="A+"?"GRADE A+ • PREMIUM SETUP":q==="A"?"GRADE A • CONFIRMED SETUP":q==="B"?"GRADE B • PREPARE ONLY":q==="C"?"GRADE C • WAIT":"NO NEW ENTRY";
 $("qualityState").textContent=d.entry_state||"WAIT";$("qualitySummary").textContent=`New-entry filters passed ${d.quality_passed||0}/${d.quality_total||0}. ${d.entry_state_detail||""}`;$("regimeDetail").textContent=`Market regime: ${d.market_regime||"—"}`;$("qualityChecks").innerHTML=(d.quality_checks||[]).map(x=>`<span class="pill ${x.ok?"ok":"bad"}">${x.ok?"✓":"✕"} ${x.label}</span>`).join("");
 $("dot").className="dot on";$("status").textContent="Connected • V3.7";$("error").textContent=d.warning||"";
 const t=readTrade();
 if(t){$("signal").textContent="ACTIVE TRADE";$("signal").className="signal buy";$("contract").textContent=t.contract;$("expiry").textContent=`Locked ${t.expiry} • Strike ${t.strike} • ${t.type}`;$("entry").textContent="₹"+fmt(t.entry);$("sl").textContent="₹"+fmt(t.sl);$("t1").textContent="₹"+fmt(t.t1);$("t2").textContent="₹"+fmt(t.t2);monitorTrade(d);return}
 $("signal").textContent=d.signal;$("signal").className="signal "+(d.signal==="BUY"?"buy":d.signal==="SELL"?"sell":d.signal.includes("WATCH")?"watch":"neutral");$("bias").textContent=d.bias;
 if(d.option){$("contract").textContent=d.option.contract;$("expiry").textContent=`Expiry ${d.option.expiry} • Strike ${d.option.strike} • ${d.option.type}`;$("liquidity").textContent=`Liquidity ${d.option.liquidity} • Volume ${d.option.volume} • OI ${d.option.oi} • Spread ${fmt(d.option.spread_pct)}%`;$("optionLtp").textContent="₹"+fmt(d.option.ltp);$("bid").textContent="₹"+fmt(d.option.bid);$("ask").textContent="₹"+fmt(d.option.ask);$("entry").textContent="₹"+fmt(d.option.entry);$("sl").textContent="₹"+fmt(d.option.sl);$("t1").textContent="₹"+fmt(d.option.target1);$("t2").textContent="₹"+fmt(d.option.target2)}
 monitorTrade(d);updateTradeUI(readTrade());evaluateTradeHealth(readTrade(),d);evaluateEodDecision(readTrade(),d);
}
async function refresh(){
 $("dot").className="dot warn";$("status").textContent="Updating…";
 try{const r=await fetch("/api/signal",{cache:"no-store"});const d=await r.json();if(!r.ok||d.error)throw new Error(d.error||"request failed");render(d)}
 catch(e){$("error").textContent="Live refresh failed: "+e.message;$("status").textContent="Reconnecting • V3.7"}
}
$("refresh").onclick=refresh;$("resetTradeBtn").onclick=()=>{clearTrade();refresh()};$("notifyBtn").onclick=async()=>{notificationsEnabled=true;localStorage.setItem("niftyV37Notify","1");if("Notification"in window&&Notification.permission==="default"){try{await Notification.requestPermission()}catch(e){}}$("notifyBtn").textContent="NOTIFICATIONS / VIBRATION ENABLED"};
$("exportJournalBtn").onclick=async()=>{const txt=journal().map(x=>`${x.time} | ${x.kind} | ${x.contract} | ${x.detail}`).join("\n")||"No journal events.";try{await navigator.clipboard.writeText(txt);$("journalLast").textContent="Journal copied"}catch(e){$("journalLast").textContent="Copy blocked"}};
renderJournal();updateTradeUI(readTrade());refresh();setInterval(refresh,15000);setInterval(refreshLockedTrade,15000);
</script></body></html>"""

@app.route("/", methods=["GET"])
@app.route("/<path:p>", methods=["GET"])
def home(p=""):
    if p == "api/signal": return api_signal()
    if p == "health": return jsonify({"ok":True,"version":VERSION})
    return PAGE, 200, {"Content-Type":"text/html; charset=utf-8"}

@app.route("/api/locked-option", methods=["GET"])
def api_locked_option():
    try:
        strike=request.args.get("strike",type=float); opt_type=(request.args.get("type") or "").upper(); expiry_date=request.args.get("expiry") or ""
        if strike is None or opt_type not in ("CE","PE"):
            return jsonify({"error":"Invalid locked option parameters."}),400
        return jsonify(exact_option_quote(strike,opt_type,expiry_date))
    except Exception as e:
        return jsonify({"error":str(e)}),503

@app.route("/api/signal", methods=["GET"])
def api_signal():
    now=time.time()
    if _cache["signal"] is not None and now-_cache["ts"]<12:
        return jsonify(_cache["signal"])
    try:
        r=build_signal(); _cache["signal"]=r; _cache["ts"]=now; return jsonify(r)
    except Exception as e:
        if _cache["signal"] is not None:
            x=dict(_cache["signal"]); x["warning"]="Using cached data: "+str(e); return jsonify(x)
        return jsonify({"error":str(e)}),503

if __name__ == "__main__":
    import os
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT","10000")))
