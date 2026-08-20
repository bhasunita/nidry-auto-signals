from flask import Flask, jsonify
import requests
import math
import time
from datetime import datetime
from zoneinfo import ZoneInfo

app = Flask(__name__)

IST = ZoneInfo("Asia/Kolkata")

NSE_HOME = "https://www.nseindia.com/"
NSE_CHART_API = (
    "https://www.nseindia.com/api/chart-databyindex"
    "?index=NIFTY%2050&indices=true"
)
NSE_OC_API = (
    "https://www.nseindia.com/api/option-chain-indices"
    "?symbol=NIFTY"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 13) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Mobile Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/option-chain",
}

_cache = {
    "signal": None,
    "ts": 0
}


PAGE = r"""
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport"
content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#07111f">

<title>NIFTY Automatic Option Signals</title>

<style>
:root{
--bg:#07111f;
--card:#0f1c2e;
--card2:#12233a;
--text:#eef5ff;
--muted:#9bb0c9;
--green:#22c55e;
--red:#ef4444;
--amber:#f59e0b;
--line:#223855;
}

*{box-sizing:border-box}

body{
margin:0;
background:linear-gradient(180deg,#06101d,#0a1627);
color:var(--text);
font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;
min-height:100vh;
}

.wrap{
max-width:780px;
margin:auto;
padding:14px 12px 46px;
}

h1{
font-size:22px;
margin:4px 0;
}

.sub{
color:var(--muted);
font-size:13px;
margin-bottom:12px;
}

.banner{
background:#3b2b08;
border:1px solid #795c12;
border-radius:18px;
padding:14px;
margin:10px 0 18px;
color:#ffe9a8;
}

.card{
background:rgba(15,28,46,.98);
border:1px solid var(--line);
border-radius:18px;
padding:14px;
margin:10px 0;
}

.status{
display:flex;
gap:8px;
align-items:center;
font-size:13px;
color:var(--muted);
}

.dot{
width:9px;
height:9px;
border-radius:50%;
background:#64748b;
}

.dot.on{
background:var(--green);
box-shadow:0 0 9px var(--green);
}

.dot.warn{
background:var(--amber);
}

.price{
font-size:39px;
font-weight:850;
margin-top:8px;
}

.signal{
font-size:28px;
font-weight:900;
margin:5px 0;
}

.buy{color:var(--green)}
.sell{color:var(--red)}
.watch{color:var(--amber)}
.neutral{color:#cbd5e1}

.row{
display:grid;
grid-template-columns:1fr 1fr;
gap:10px;
}

.row3{
display:grid;
grid-template-columns:repeat(3,1fr);
gap:8px;
}

.kpi{
background:var(--card2);
border:1px solid var(--line);
border-radius:14px;
padding:11px;
}

.kpi .t{
color:var(--muted);
font-size:11px;
text-transform:uppercase;
letter-spacing:.5px;
}

.kpi .v{
font-size:20px;
font-weight:800;
margin-top:3px;
}

.small{
color:var(--muted);
font-size:12px;
line-height:1.45;
}

#error{
white-space:pre-wrap;
color:#ffb4bc;
font-size:12px;
margin-top:8px;
}

button{
width:100%;
border:0;
border-radius:12px;
padding:13px;
font-size:15px;
font-weight:800;
background:#0ea5e9;
color:#00101a;
margin-top:12px;
}

@media(max-width:520px){
.row3{grid-template-columns:1fr 1fr}
}
</style>
</head>

<body>
<div class="wrap">

<h1>📈 NIFTY Automatic Option Signals</h1>

<div class="sub">
Automatic contract + premium + SL + targets • no broker API key
</div>

<div class="banner">
Market feeds can be delayed or temporarily blocked.
This is a technical signal tool, not automatic order execution.
</div>

<div class="card">

<div class="status">
<span id="dot" class="dot"></span>
<span id="status">Starting...</span>
<span style="margin-left:auto" id="updated">—</span>
</div>

<div id="spot" class="price">—</div>
<div id="signal" class="signal neutral">WAITING</div>

<div id="bias" class="small">Loading NIFTY data...</div>
<div id="error"></div>

</div>

<div class="card">

<div class="small">Automatically selected option</div>

<div id="contract"
style="font-size:22px;font-weight:800;margin:5px 0">—</div>

<div id="expiry" class="small">—</div>

<div style="height:10px"></div>

<div class="row3">

<div class="kpi">
<div class="t">OPTION LTP</div>
<div class="v" id="optionLtp">—</div>
</div>

<div class="kpi">
<div class="t">BID</div>
<div class="v" id="bid">—</div>
</div>

<div class="kpi">
<div class="t">ASK / ENTRY</div>
<div class="v" id="entry">—</div>
</div>

</div>
</div>

<div class="row">

<div class="kpi">
<div class="t">BUY / ENTRY</div>
<div class="v" id="entry2">—</div>
</div>

<div class="kpi">
<div class="t">STOP LOSS</div>
<div class="v" id="sl">—</div>
</div>

<div class="kpi">
<div class="t">TARGET 1</div>
<div class="v" id="t1">—</div>
</div>

<div class="kpi">
<div class="t">TARGET 2 / EXIT</div>
<div class="v" id="t2">—</div>
</div>

</div>

<div class="card">

<div class="row3">

<div>
<div class="small">NIFTY BUY ABOVE</div>
<b id="buyAbove">—</b>
</div>

<div>
<div class="small">NIFTY SELL BELOW</div>
<b id="sellBelow">—</b>
</div>

<div>
<div class="small">RSI 14</div>
<b id="rsi">—</b>
</div>

<div>
<div class="small">EMA 9</div>
<b id="ema9">—</b>
</div>

<div>
<div class="small">EMA 21</div>
<b id="ema21">—</b>
</div>

<div>
<div class="small">ATR 14</div>
<b id="atr">—</b>
</div>

</div>

<button id="refresh">REFRESH NOW</button>

</div>

<div class="card small">
Contract rule: nearest available NIFTY expiry from NSE,
ATM strike nearest to spot, CE for bullish and PE for bearish.
Entry uses ask when available, otherwise LTP.
Premium plan: 20% SL, +30% Target 1, +50% Target 2.
</div>

</div>

<script>
"use strict";

const $ = id => document.getElementById(id);

function fmt(v, d=2){
    if(v === null || v === undefined || v === "") return "—";
    const n = Number(v);
    return Number.isFinite(n) ? n.toFixed(d) : v;
}

function render(d){

    $("error").textContent = "";

    $("spot").textContent = fmt(d.spot,2);
    $("signal").textContent = d.signal || "WAITING";
    $("bias").textContent = d.bias || "";

    $("updated").textContent = d.updated || "";

    $("signal").className =
        "signal " +
        (
            d.bias === "BULLISH" ? "buy" :
            d.bias === "BEARISH" ? "sell" :
            "neutral"
        );

    $("dot").className = "dot on";
    $("status").textContent = "Live data";

    $("buyAbove").textContent = fmt(d.buy_above,2);
    $("sellBelow").textContent = fmt(d.sell_below,2);

    $("rsi").textContent = fmt(d.rsi,2);
    $("ema9").textContent = fmt(d.ema9,2);
    $("ema21").textContent = fmt(d.ema21,2);
    $("atr").textContent = fmt(d.atr,2);

    if(d.option){

        $("contract").textContent =
            d.option.contract || "—";

        $("expiry").textContent =
            d.option.expiry || "—";

        $("optionLtp").textContent =
            fmt(d.option.ltp,2);

        $("bid").textContent =
            fmt(d.option.bid,2);

        $("entry").textContent =
            fmt(d.option.entry,2);

        $("entry2").textContent =
            fmt(d.option.entry,2);

        $("sl").textContent =
            fmt(d.option.sl,2);

        $("t1").textContent =
            fmt(d.option.target1,2);

        $("t2").textContent =
            fmt(d.option.target2,2);

    } else {

        $("contract").textContent = "—";
        $("expiry").textContent = "—";
        $("optionLtp").textContent = "—";
        $("bid").textContent = "—";
        $("entry").textContent = "—";
        $("entry2").textContent = "—";
        $("sl").textContent = "—";
        $("t1").textContent = "—";
        $("t2").textContent = "—";
    }
}

let busy = false;

async function refresh(){

    if(busy) return;

    busy = true;

    $("status").textContent = "Loading NSE data...";
    $("dot").className = "dot warn";
    $("error").textContent = "";

    try{

        const r = await fetch(
            "/api/signal?t=" + Date.now(),
            {cache:"no-store"}
        );

        const d = await r.json();

        if(!r.ok){
            throw new Error(d.error || ("HTTP " + r.status));
        }

        render(d);

    }catch(e){

        $("status").textContent = "Data unavailable";
        $("dot").className = "dot";
        $("error").textContent = e.message;

    }finally{

        busy = false;
    }
}

$("refresh").onclick = refresh;

refresh();

setInterval(refresh, 60000);
</script>

</body>
</html>
"""


def nse_session():
    s = requests.Session()
    s.headers.update(HEADERS)

    s.get(
        NSE_HOME,
        timeout=12
    )

    return s


def fetch_nifty_ticks():
    s = nse_session()

    r = s.get(
        NSE_CHART_API,
        timeout=15
    )

    r.raise_for_status()

    j = r.json()

    data = (
        j.get("grapthData")
        or j.get("graphData")
        or []
    )

    if not data:
        raise RuntimeError(
            "NSE NIFTY chart returned no data."
        )

    ticks = []

    for item in data:

        if not isinstance(item, (list, tuple)):
            continue

        if len(item) < 2:
            continue

        ts = item[0]
        px = item[1]

        try:
            ts = float(ts)
            px = float(px)
        except Exception:
            continue

        if ts > 100000000000:
            ts = ts / 1000.0

        if math.isfinite(px):
            ticks.append((ts, px))

    if len(ticks) < 20:
        raise RuntimeError(
            "Not enough NIFTY chart data from NSE."
        )

    return ticks


def ticks_to_5m_candles(ticks):

    buckets = {}

    for ts, price in ticks:

        bucket = int(ts // 300) * 300

        if bucket not in buckets:
            buckets[bucket] = {
                "t": bucket,
                "o": price,
                "h": price,
                "l": price,
                "c": price
            }
        else:
            x = buckets[bucket]
            x["h"] = max(x["h"], price)
            x["l"] = min(x["l"], price)
            x["c"] = price

    candles = [
        buckets[k]
        for k in sorted(buckets.keys())
    ]

    if len(candles) < 25:
        raise RuntimeError(
            "Not enough 5-minute NIFTY candles yet."
        )

    return candles


def ema(values, span):

    a = 2 / (span + 1)

    out = []
    prev = values[0]

    for i, v in enumerate(values):

        if i == 0:
            prev = v
        else:
            prev = v * a + prev * (1 - a)

        out.append(prev)

    return out


def rsi(values, period=14):

    out = [50.0] * len(values)

    gain_avg = 0.0
    loss_avg = 0.0

    for i in range(1, len(values)):

        ch = values[i] - values[i - 1]

        gain = max(ch, 0)
        loss = max(-ch, 0)

        if i <= period:

            gain_avg += gain
            loss_avg += loss

            if i == period:
                gain_avg /= period
                loss_avg /= period

        else:

            gain_avg = (
                gain_avg * (period - 1) + gain
            ) / period

            loss_avg = (
                loss_avg * (period - 1) + loss
            ) / period

        if i >= period:

            if loss_avg == 0:
                out[i] = 100.0
            else:
                rs = gain_avg / loss_avg
                out[i] = 100 - 100 / (1 + rs)

    return out


def atr(candles, period=14):

    tr = []

    for i, x in enumerate(candles):

        if i == 0:
            t = x["h"] - x["l"]
        else:
            prev_close = candles[i - 1]["c"]

            t = max(
                x["h"] - x["l"],
                abs(x["h"] - prev_close),
                abs(x["l"] - prev_close)
            )

        tr.append(t)

    out = []
    prev = tr[0]

    for i, t in enumerate(tr):

        if i == 0:
            prev = t
        else:
            prev = (
                prev * (period - 1) + t
            ) / period

        out.append(prev)

    return out


def supertrend(candles, period=10, mult=3.0):

    av = atr(candles, period)

    direction = [1] * len(candles)

    final_upper = None
    final_lower = None

    for i, x in enumerate(candles):

        mid = (x["h"] + x["l"]) / 2

        basic_upper = mid + mult * av[i]
        basic_lower = mid - mult * av[i]

        if i == 0:
            final_upper = basic_upper
            final_lower = basic_lower
            continue

        prev_close = candles[i - 1]["c"]

        if (
            basic_upper < final_upper
            or prev_close > final_upper
        ):
            final_upper = basic_upper

        if (
            basic_lower > final_lower
            or prev_close < final_lower
        ):
            final_lower = basic_lower

        if direction[i - 1] == 1:

            if x["c"] < final_lower:
                direction[i] = -1
            else:
                direction[i] = 1

        else:

            if x["c"] > final_upper:
                direction[i] = 1
            else:
                direction[i] = -1

    return direction


def parse_expiry(text):

    for fmt in (
        "%d-%b-%Y",
        "%d-%b-%y"
    ):
        try:
            return datetime.strptime(
                text,
                fmt
            ).date()
        except ValueError:
            pass

    return None


def choose_expiry(expiries):

    today = datetime.now(IST).date()

    parsed = []

    for e in expiries:

        d = parse_expiry(e)

        if d:
            parsed.append((d, e))

    future = [
        x for x in parsed
        if x[0] >= today
    ]

    use = future or parsed

    if not use:
        raise RuntimeError(
            "No usable NIFTY expiry found."
        )

    return sorted(use)[0][1]


def fetch_option_chain():

    s = nse_session()

    r = s.get(
        NSE_OC_API,
        timeout=15
    )

    if r.status_code in (401, 403):

        s = nse_session()

        r = s.get(
            NSE_OC_API,
            timeout=15
        )

    r.raise_for_status()

    j = r.json()

    records = j.get("records", {})

    if not records.get("data"):
        raise RuntimeError(
            "NSE option chain returned no data."
        )

    return j


def choose_option(oc, spot, direction):

    records = oc["records"]

    expiries = records.get(
        "expiryDates",
        []
    )

    expiry = choose_expiry(expiries)

    option_type = (
        "CE"
        if direction == "BULLISH"
        else "PE"
    )

    candidates = []

    for row in records["data"]:

        if row.get("expiryDate") != expiry:
            continue

        side = row.get(option_type)

        if not side:
            continue

        strike = float(
            row.get("strikePrice", 0)
        )

        candidates.append(
            (
                abs(strike - spot),
                strike,
                side
            )
        )

    if not candidates:
        raise RuntimeError(
            "No matching NIFTY option found."
        )

    _, strike, side = min(
        candidates,
        key=lambda x: x[0]
    )

    ltp = float(
        side.get("lastPrice", 0) or 0
    )

    bid = float(
        side.get("bidprice", 0)
        or side.get("bidPrice", 0)
        or 0
    )

    ask = float(
        side.get("askPrice", 0)
        or side.get("askprice", 0)
        or 0
    )

    entry = (
        ask
        if ask > 0
        else ltp
    )

    if entry <= 0:
        raise RuntimeError(
            "Selected option has no usable premium."
        )

    return {
        "contract": (
            f"NIFTY {int(strike)} {option_type}"
        ),
        "expiry": expiry,
        "strike": strike,
        "type": option_type,
        "ltp": round(ltp, 2),
        "bid": round(bid, 2),
        "ask": round(ask, 2),
        "entry": round(entry, 2),
        "sl": round(entry * 0.80, 2),
        "target1": round(entry * 1.30, 2),
        "target2": round(entry * 1.50, 2)
    }


def build_signal():

    ticks = fetch_nifty_ticks()

    candles = ticks_to_5m_candles(
        ticks
    )

    closes = [
        x["c"]
        for x in candles
    ]

    e9 = ema(
        closes,
        9
    )

    e21 = ema(
        closes,
        21
    )

    rs = rsi(
        closes,
        14
    )

    av = atr(
        candles,
        14
    )

    st = supertrend(
        candles,
        10,
        3.0
    )

    i = len(candles) - 1

    spot = closes[i]

    recent = candles[-6:]

    buy_above = max(
        x["h"]
        for x in recent
    )

    sell_below = min(
        x["l"]
        for x in recent
    )

    bullish_checks = [
        e9[i] > e21[i],
        rs[i] >= 52,
        st[i] == 1
    ]

    bearish_checks = [
        e9[i] < e21[i],
        rs[i] <= 48,
        st[i] == -1
    ]

    bull_score = sum(
        int(x)
        for x in bullish_checks
    )

    bear_score = sum(
        int(x)
        for x in bearish_checks
    )

    signal = "NO TRADE"
    bias = "NEUTRAL"

    if (
        bull_score >= 2
        and bull_score > bear_score
    ):
        signal = "BUY CALL"
        bias = "BULLISH"

    elif (
        bear_score >= 2
        and bear_score > bull_score
    ):
        signal = "BUY PUT"
        bias = "BEARISH"

    option = None

    if bias in (
        "BULLISH",
        "BEARISH"
    ):
        oc = fetch_option_chain()

        option = choose_option(
            oc,
            spot,
            bias
        )

    return {
        "spot": round(spot, 2),
        "signal": signal,
        "bias": bias,

        "ema9": round(e9[i], 2),
        "ema21": round(e21[i], 2),
        "rsi": round(rs[i], 2),
        "atr": round(av[i], 2),

        "buy_above": round(
            buy_above,
            2
        ),

        "sell_below": round(
            sell_below,
            2
        ),

        "option": option,

        "updated": datetime.now(
            IST
        ).strftime(
            "%d-%b %I:%M:%S %p"
        )
    }


@app.route(
    "/",
    methods=["GET"]
)
def home():
    return (
        PAGE,
        200,
        {
            "Content-Type":
            "text/html; charset=utf-8"
        }
    )


@app.route(
    "/health",
    methods=["GET"]
)
def health():
    return jsonify({
        "status": "ok"
    })


@app.route(
    "/api/signal",
    methods=["GET"]
)
def api_signal():

    now = time.time()

    if (
        _cache["signal"] is not None
        and now - _cache["ts"] < 45
    ):
        return jsonify(
            _cache["signal"]
        )

    try:

        result = build_signal()

        _cache["signal"] = result
        _cache["ts"] = now

        return jsonify(result)

    except Exception as e:

        if _cache["signal"] is not None:

            stale = dict(
                _cache["signal"]
            )

            stale["updated"] = (
                "Cached data"
            )

            stale["warning"] = str(e)

            return jsonify(stale)

        return jsonify({
            "error": str(e)
        }), 503


if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=10000
    )
