from flask import Flask, jsonify
import requests, time
from datetime import datetime
from zoneinfo import ZoneInfo

app = Flask(__name__)
IST = ZoneInfo('Asia/Kolkata')
TV_URL = 'https://scanner.tradingview.com/india/scan'
NSE_HOME = 'https://www.nseindia.com/'
NSE_OC_PAGE = 'https://www.nseindia.com/option-chain'
NSE_OC_API = 'https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY'
HEADERS = {
    'User-Agent':'Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/126 Safari/537.36',
    'Accept':'application/json,text/plain,*/*',
    'Accept-Language':'en-US,en;q=0.9',
    'Origin':'https://www.tradingview.com',
    'Referer':'https://www.tradingview.com/'
}
NSE_HEADERS = dict(HEADERS)
NSE_HEADERS['Origin'] = 'https://www.nseindia.com'
NSE_HEADERS['Referer'] = NSE_OC_PAGE
_cache = {'signal':None,'ts':0}

PAGE = '''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>NIFTY Auto Options</title><style>
body{margin:0;background:#07111f;color:#eef5ff;font-family:Arial,sans-serif}.w{max-width:760px;margin:auto;padding:14px}.c{background:#0f1c2e;border:1px solid #223855;border-radius:18px;padding:14px;margin:10px 0}.p{font-size:38px;font-weight:800}.s{font-size:28px;font-weight:900}.g{color:#22c55e}.r{color:#ef4444}.a{color:#f59e0b}.m{color:#9bb0c9;font-size:13px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}.k{background:#12233a;border-radius:14px;padding:12px}.t{font-size:11px;color:#9bb0c9}.v{font-size:20px;font-weight:800;margin-top:4px}button{width:100%;padding:13px;border:0;border-radius:12px;background:#0ea5e9;font-weight:800}.err{color:#ffb4bc;font-size:12px;white-space:pre-wrap}</style></head><body><div class="w">
<h2>📈 NIFTY Automatic Option Signals</h2><div class="m">TradingView 5-minute signal + NSE option chain • no Yahoo</div>
<div class="c"><div id="status" class="m">Starting…</div><div id="spot" class="p">—</div><div id="signal" class="s">WAITING</div><div id="bias" class="m">Loading…</div><div id="err" class="err"></div></div>
<div class="c"><div class="m">Automatically selected option</div><div id="contract" class="s">—</div><div id="expiry" class="m">—</div><div class="grid"><div class="k"><div class="t">OPTION LTP</div><div id="ltp" class="v">—</div></div><div class="k"><div class="t">ASK / ENTRY</div><div id="ask" class="v">—</div></div><div class="k"><div class="t">STOP LOSS</div><div id="sl" class="v">—</div></div><div class="k"><div class="t">TARGET 1</div><div id="t1" class="v">—</div></div><div class="k"><div class="t">TARGET 2</div><div id="t2" class="v">—</div></div><div class="k"><div class="t">BID</div><div id="bid" class="v">—</div></div></div></div>
<div class="c"><div class="grid"><div><div class="m">TV Rating</div><b id="rating">—</b></div><div><div class="m">RSI 14 (5m)</div><b id="rsi">—</b></div><div><div class="m">EMA 10 (5m)</div><b id="e10">—</b></div><div><div class="m">EMA 20 (5m)</div><b id="e20">—</b></div></div></div>
<button onclick="loadData()">REFRESH NOW</button><div class="c m">Signal rule: TradingView 5-minute recommendation + RSI + EMA trend. BUY selects ATM CE; SELL selects ATM PE. Entry uses NSE ask when available, otherwise LTP. Default premium plan: 20% SL, +30% Target 1, +50% Target 2. Verify contract and premium in your broker before any trade.</div>
</div><script>
const $=x=>document.getElementById(x), fmt=x=>(x==null||isNaN(Number(x)))?'—':Number(x).toLocaleString('en-IN',{minimumFractionDigits:2,maximumFractionDigits:2});
async function loadData(){ $('status').textContent='Updating…'; try{let r=await fetch('/api/signal',{cache:'no-store'}),d=await r.json(); if(!r.ok||d.error)throw new Error(d.error||'Data error'); $('spot').textContent=fmt(d.spot); $('signal').textContent=d.signal; $('signal').className='s '+(d.signal==='BUY'?'g':d.signal==='SELL'?'r':'a'); $('bias').textContent=d.bias; $('rating').textContent=d.rating; $('rsi').textContent=fmt(d.rsi); $('e10').textContent=fmt(d.ema10); $('e20').textContent=fmt(d.ema20); if(d.option){$('contract').textContent=d.option.contract; $('expiry').textContent='Expiry '+d.option.expiry+' • Strike '+d.option.strike+' • '+d.option.type; $('ltp').textContent='₹'+fmt(d.option.ltp); $('ask').textContent='₹'+fmt(d.option.entry); $('bid').textContent='₹'+fmt(d.option.bid); $('sl').textContent='₹'+fmt(d.option.sl); $('t1').textContent='₹'+fmt(d.option.target1); $('t2').textContent='₹'+fmt(d.option.target2)} else {['contract','expiry','ltp','ask','bid','sl','t1','t2'].forEach(x=>$(x).textContent='—')} $('err').textContent=d.warning||''; $('status').textContent='Connected • '+d.updated;}catch(e){$('status').textContent='Data unavailable';$('err').textContent=e.message}}
loadData(); setInterval(loadData,30000);
</script></body></html>'''

def tv_rating(v):
    v=float(v or 0)
    if v>=0.5:return 'STRONG BUY'
    if v>=0.1:return 'BUY'
    if v<=-0.5:return 'STRONG SELL'
    if v<=-0.1:return 'SELL'
    return 'NEUTRAL'

def fetch_tv():
    cols=['close|5','Recommend.All|5','RSI|5','EMA10|5','EMA20|5']
    payload={'symbols':{'tickers':['NSE:NIFTY'],'query':{'types':[]}},'columns':cols,'range':[0,1]}
    r=requests.post(TV_URL,json=payload,headers=HEADERS,timeout=12); r.raise_for_status(); j=r.json()
    if not j.get('data'): raise RuntimeError('TradingView returned no NIFTY data.')
    d=dict(zip(cols,j['data'][0]['d']))
    return {'spot':float(d['close|5']),'recommend':float(d['Recommend.All|5'] or 0),'rsi':float(d['RSI|5'] or 50),'ema10':float(d['EMA10|5'] or d['close|5']),'ema20':float(d['EMA20|5'] or d['close|5'])}

def fetch_oc():
    s=requests.Session(); s.get(NSE_HOME,headers=NSE_HEADERS,timeout=10); s.get(NSE_OC_PAGE,headers=NSE_HEADERS,timeout=10); r=s.get(NSE_OC_API,headers=NSE_HEADERS,timeout=12)
    if r.status_code in (401,403):
        s=requests.Session(); s.get(NSE_OC_PAGE,headers=NSE_HEADERS,timeout=10); r=s.get(NSE_OC_API,headers=NSE_HEADERS,timeout=12)
    r.raise_for_status(); j=r.json()
    if not j.get('records',{}).get('data'): raise RuntimeError('NSE option chain returned no contracts.')
    return j

def parse_expiry(x):
    for f in ('%d-%b-%Y','%d-%b-%y'):
        try:return datetime.strptime(x,f).date()
        except ValueError:pass

def choose_expiry(xs):
    today=datetime.now(IST).date(); p=[(parse_expiry(x),x) for x in xs]; p=[x for x in p if x[0]]; f=[x for x in p if x[0]>=today] or p
    if not f: raise RuntimeError('No usable NIFTY expiry found.')
    return sorted(f,key=lambda x:x[0])[0][1]

def choose_option(oc,spot,direction):
    rec=oc['records']; expiry=choose_expiry(rec.get('expiryDates',[])); target=round(spot/50)*50; typ='CE' if direction=='bull' else 'PE'; c=[]
    for row in rec['data']:
        if row.get('expiryDate')!=expiry or not row.get(typ): continue
        side=row[typ]; strike=float(row.get('strikePrice',side.get('strikePrice',0)) or 0); c.append((abs(strike-target),strike,side))
    if not c: raise RuntimeError('No matching NIFTY option contract found.')
    _,strike,side=sorted(c,key=lambda x:x[0])[0]; ltp=float(side.get('lastPrice',0) or 0); bid=float(side.get('bidprice',side.get('bidPrice',0)) or 0); ask=float(side.get('askPrice',side.get('askprice',0)) or 0); entry=ask if ask>0 else ltp
    if entry<=0: raise RuntimeError('Selected option has no usable premium.')
    return {'contract':f'NIFTY {int(strike)} {typ}','expiry':expiry,'strike':strike,'type':typ,'ltp':ltp,'bid':bid,'entry':entry,'sl':entry*.80,'target1':entry*1.30,'target2':entry*1.50}

def build_signal():
    t=fetch_tv(); rating=tv_rating(t['recommend']); bullish=rating in ('BUY','STRONG BUY') and t['rsi']>=50 and t['ema10']>=t['ema20']; bearish=rating in ('SELL','STRONG SELL') and t['rsi']<=50 and t['ema10']<=t['ema20']; direction=None; signal='NO TRADE'; bias='NEUTRAL'
    if bullish: direction='bull'; signal='BUY'; bias='BULLISH'
    elif bearish: direction='bear'; signal='SELL'; bias='BEARISH'
    option=None; warning=''
    if direction:
        try: option=choose_option(fetch_oc(),t['spot'],direction)
        except Exception as e: warning='Signal available, but option-chain data failed: '+str(e)
    return {'spot':t['spot'],'signal':signal,'bias':bias,'rating':rating,'rsi':t['rsi'],'ema10':t['ema10'],'ema20':t['ema20'],'option':option,'warning':warning,'updated':datetime.now(IST).strftime('%H:%M:%S')}

@app.route('/',methods=['GET'])
@app.route('/<path:any_path>',methods=['GET'])
def home(any_path=''):
    if any_path=='api/signal': return api_signal()
    if any_path=='health': return jsonify({'ok':True})
    return PAGE,200,{'Content-Type':'text/html; charset=utf-8'}

@app.route('/api/signal',methods=['GET'])
def api_signal():
    now=time.time()
    if _cache['signal'] is not None and now-_cache['ts']<25: return jsonify(_cache['signal'])
    try:
        x=build_signal(); _cache['signal']=x; _cache['ts']=now; return jsonify(x)
    except Exception as e:
        if _cache['signal'] is not None:
            x=dict(_cache['signal']); x['warning']='Using cached data: '+str(e); return jsonify(x)
        return jsonify({'error':str(e)}),503

if __name__=='__main__':
    import os
    app.run(host='0.0.0.0',port=int(os.environ.get('PORT','10000')))
