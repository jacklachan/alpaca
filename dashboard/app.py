"""Read-only public view over the journal. This is the Application URL.

Deliberately read-only and credential-free: it opens the journal file and
nothing else. It holds no Alpaca keys, can place no orders, and has no write
path of any kind. If this process is compromised the worst outcome is that
somebody reads a trading log.

Alpaca's guidelines say a UI is not required ("A hosted link is needed only if
the submission includes a demo app that judges must open"). We build one anyway
because two of the five judging criteria -- Presentation & Execution, and
Technology Implementation -- are about whether a judge can *see* the agent
working. An equity curve is what every team will show. The decision journal,
with the refusals visible and the chain verifiable in the browser, is what
almost none of them will.

    uvicorn dashboard.app:app --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from glassbox import config as C
from glassbox.journal import Journal
from glassbox.macro import (CALENDAR, MEASUREMENT_ET, MEASUREMENT_SOURCE,
                            post_measurement_events)

app = FastAPI(title="Glassbox", docs_url=None, redoc_url=None)

JOURNAL_PATH = os.getenv("GLASSBOX_JOURNAL_PATH", C.JOURNAL_PATH)

# Events that carry the story. Anything else is plumbing and stays out of the
# default timeline so the interesting entries are not buried.
HEADLINE = {
    "PLAN_APPROVED", "PLAN_REFUSED", "PLAN_DISCARDED_INVALID",
    "ORDER_ACCEPTED", "ORDER_CANCELLED", "POSITION_CLOSED",
    "EXIT_TRIGGERED", "KILL_SWITCH_TRIPPED", "KILL_SWITCH_REARMED",
    "THESIS_COMPLETE", "THESIS_UNAVAILABLE", "DAILY_REVIEW",
    "STARTUP", "TORN_ENTRY_DISCARDED", "ANCHOR_PUBLISHED", "JOB_FAILED",
    "LEG_SUBMIT_FAILED", "EXIT_FAILED",
}

TONE = {
    "PLAN_REFUSED": "refuse", "PLAN_DISCARDED_INVALID": "refuse",
    "KILL_SWITCH_TRIPPED": "alarm", "JOB_FAILED": "alarm",
    "LEG_SUBMIT_FAILED": "alarm", "EXIT_FAILED": "alarm",
    "TORN_ENTRY_DISCARDED": "alarm",
    "ORDER_ACCEPTED": "act", "POSITION_CLOSED": "act", "EXIT_TRIGGERED": "act",
    "PLAN_APPROVED": "approve",
    "STARTUP": "sys", "ANCHOR_PUBLISHED": "sys", "STARTUP_": "sys",
}


def _load() -> list[dict]:
    p = Path(JOURNAL_PATH)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue          # a torn tail is expected; never 500 on it
    return out


def _summary(records: list[dict]) -> dict:
    counts = Counter(r["event"] for r in records)
    approved = counts["PLAN_APPROVED"]
    refused = counts["PLAN_REFUSED"] + counts["PLAN_DISCARDED_INVALID"]
    proposed = approved + refused

    equity, start_equity = None, None
    for r in records:
        if r["event"] == "STARTUP":
            try:
                start_equity = start_equity or float(r["payload"].get("equity", 0))
            except (TypeError, ValueError):
                pass
        if r["event"] == "HEARTBEAT":
            v = r["payload"].get("equity")
            if v is not None:
                try:
                    equity = float(v)
                except (TypeError, ValueError):
                    pass

    ok, why = (Journal(JOURNAL_PATH).verify() if records else (True, "no entries yet"))
    return {
        "entries": len(records),
        "plans_proposed": proposed,
        "plans_approved": approved,
        "plans_refused": refused,
        "refusal_rate": round(refused / proposed, 3) if proposed else None,
        "orders_accepted": counts["ORDER_ACCEPTED"],
        "restarts": counts["STARTUP"],
        "crash_recoveries": counts["TORN_ENTRY_DISCARDED"],
        "kill_switch_trips": counts["KILL_SWITCH_TRIPPED"],
        "anchors": counts["ANCHOR_PUBLISHED"],
        "equity": equity,
        "start_equity": start_equity,
        "chain_ok": ok,
        "chain_detail": why,
        "head": records[-1]["hash"] if records else None,
    }


def _equity_series(records: list[dict]) -> list[dict]:
    pts = []
    for r in records:
        if r["event"] != "HEARTBEAT":
            continue
        v = r["payload"].get("equity")
        if v is None:
            continue
        try:
            pts.append({"ts": r["ts"], "equity": float(v)})
        except (TypeError, ValueError):
            continue
    return pts


# ----------------------------------------------------------------- JSON API

@app.get("/api/summary")
def api_summary() -> JSONResponse:
    return JSONResponse(_summary(_load()))


@app.get("/api/journal")
def api_journal(limit: int = 300, all_events: bool = False) -> JSONResponse:
    recs = _load()
    if not all_events:
        recs = [r for r in recs if r["event"] in HEADLINE]
    return JSONResponse(recs[-limit:])


@app.get("/api/equity")
def api_equity() -> JSONResponse:
    return JSONResponse(_equity_series(_load()))


@app.get("/api/verify")
def api_verify() -> JSONResponse:
    ok, why = Journal(JOURNAL_PATH).verify()
    return JSONResponse({"ok": ok, "detail": why})


@app.get("/healthz")
def healthz() -> PlainTextResponse:
    return PlainTextResponse("ok")


# ----------------------------------------------------------------- the page

@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse(PAGE)


PAGE = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Glassbox — Autonomous Options Agent</title>
<style>
:root{
  --paper:#F7F7F5; --surface:#fff; --surface2:#EFEFEC;
  --ink:#15171A; --ink2:#3A3E44; --muted:#6E737C; --rule:#DEDEDA;
  --accent:#1F5E5B; --accent-soft:#E2EDEB;
  --signal:#A8720F; --signal-soft:#F6EBD6;
  --pos:#2E7D52; --neg:#B03A3A;
}
@media (prefers-color-scheme:dark){:root{
  --paper:#101315; --surface:#171B1E; --surface2:#1F2427;
  --ink:#E9E7E3; --ink2:#C3C7CB; --muted:#8C939B; --rule:#2A3033;
  --accent:#63B3AC; --accent-soft:#17302E;
  --signal:#DFA945; --signal-soft:#2E2415;
  --pos:#5BB98B; --neg:#E0716B;
}}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);
  font:15px/1.55 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
.mono,td.n{font-family:ui-monospace,Menlo,Consolas,monospace;font-variant-numeric:tabular-nums}
header{border-bottom:2px solid var(--ink);padding:22px 24px 18px}
.wrap{max-width:1120px;margin:0 auto;padding:0 24px 64px}
h1{margin:0;font-size:19px;letter-spacing:-.01em}
h1 b{color:var(--accent)}
.sub{color:var(--muted);font-size:13.5px;margin-top:4px;max-width:70ch}
.pill{display:inline-block;font-size:10.5px;letter-spacing:.09em;text-transform:uppercase;
  padding:2px 7px;border:1px solid currentColor;border-radius:2px;vertical-align:middle}
.grid{display:grid;gap:14px;margin:22px 0}
.g4{grid-template-columns:repeat(auto-fit,minmax(180px,1fr))}
.card{background:var(--surface);border:1px solid var(--rule);padding:15px 17px}
.k{font-size:10.5px;letter-spacing:.11em;text-transform:uppercase;color:var(--muted)}
.v{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:25px;font-weight:600;
  margin-top:5px;letter-spacing:-.02em;font-variant-numeric:tabular-nums}
.n2{font-size:12.5px;color:var(--muted);margin-top:3px;line-height:1.4}
.pos{color:var(--pos)}.neg{color:var(--neg)}.warn{color:var(--signal)}
h2{font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--muted);
  margin:34px 0 12px;font-weight:600}
.chain{background:var(--surface);border:1px solid var(--rule);padding:14px 17px;
  display:flex;gap:14px;align-items:center;flex-wrap:wrap}
.chain .dot{width:9px;height:9px;border-radius:50%;flex:none}
.tl{border-top:1px solid var(--rule)}
.row{display:grid;grid-template-columns:150px 122px 1fr;gap:14px;padding:11px 12px;
  border-bottom:1px solid var(--rule);align-items:start}
.row:hover{background:var(--surface)}
.row .ts{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:11.5px;color:var(--muted)}
.ev{font-size:10.5px;letter-spacing:.07em;font-weight:600;text-transform:uppercase}
.t-refuse{color:var(--signal)} .t-alarm{color:var(--neg)} .t-act{color:var(--accent)}
.t-approve{color:var(--pos)} .t-sys{color:var(--muted)}
.row.t-refuse{background:var(--signal-soft)} .row.t-alarm{background:var(--signal-soft)}
.body{font-size:13.5px;color:var(--ink2);word-break:break-word}
.body .why{color:var(--signal)}
details summary{cursor:pointer;color:var(--muted);font-size:12px;margin-top:5px}
pre{background:var(--surface2);border:1px solid var(--rule);padding:9px 11px;
  overflow-x:auto;font-size:11.5px;margin:7px 0 0}
.controls{display:flex;gap:9px;flex-wrap:wrap;margin-bottom:12px}
button{font:inherit;font-size:12.5px;padding:5px 11px;background:var(--surface);
  color:var(--ink);border:1px solid var(--rule);cursor:pointer;border-radius:2px}
button[aria-pressed=true]{background:var(--accent);color:#fff;border-color:var(--accent)}
button:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
svg{display:block;width:100%;height:auto}
.empty{color:var(--muted);padding:26px 12px;font-size:14px}
table{border-collapse:collapse;width:100%;font-size:13.5px}
th{text-align:left;font-size:10.5px;letter-spacing:.1em;text-transform:uppercase;
  color:var(--muted);padding:8px 10px;border-bottom:1px solid var(--rule)}
td{padding:8px 10px;border-bottom:1px solid var(--rule)}
@media(max-width:700px){.row{grid-template-columns:1fr;gap:3px}}
</style></head><body>

<header><div class="wrap" style="padding-bottom:0">
  <h1>Glass<b>box</b> <span class="pill" style="color:var(--accent)">read-only</span></h1>
  <p class="sub">An autonomous options agent where a language model writes the thesis,
  a deterministic kernel holds the gun, and a hash-chained journal proves nobody
  touched either one. This page holds no credentials and can place no orders.</p>
</div></header>

<div class="wrap">
  <div class="grid g4" id="stats"></div>

  <h2>Chain integrity</h2>
  <div class="chain" id="chain"><span class="mono">checking…</span></div>

  <h2>Account equity</h2>
  <div class="card"><div id="chart"></div></div>

  <h2>Scored window</h2>
  <div class="card"><table id="cal"></table></div>

  <h2>Decision journal</h2>
  <div class="controls">
    <button id="f-all"  aria-pressed="true">All</button>
    <button id="f-ref"  aria-pressed="false">Refusals only</button>
    <button id="f-ord"  aria-pressed="false">Orders only</button>
    <button id="f-raw"  aria-pressed="false">Include plumbing</button>
  </div>
  <div class="tl" id="tl"><div class="empty">loading…</div></div>
</div>

<script>
const TONE = {PLAN_REFUSED:"refuse",PLAN_DISCARDED_INVALID:"refuse",
  KILL_SWITCH_TRIPPED:"alarm",JOB_FAILED:"alarm",LEG_SUBMIT_FAILED:"alarm",
  EXIT_FAILED:"alarm",TORN_ENTRY_DISCARDED:"alarm",ORDER_ACCEPTED:"act",
  POSITION_CLOSED:"act",EXIT_TRIGGERED:"act",PLAN_APPROVED:"approve"};
const money = v => v==null ? "—" : "$"+Number(v).toLocaleString(undefined,{maximumFractionDigits:0});
const esc = s => String(s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

let RAW=false, FILTER="all";

async function j(u){ const r=await fetch(u); if(!r.ok) throw new Error(u); return r.json(); }

function stats(s){
  const pnl = (s.equity!=null && s.start_equity) ? s.equity - s.start_equity : null;
  const pct = (pnl!=null && s.start_equity) ? (pnl/s.start_equity*100) : null;
  const cls = pnl==null ? "" : (pnl>=0?"pos":"neg");
  document.getElementById("stats").innerHTML = `
    <div class="card"><div class="k">Total equity</div>
      <div class="v ${cls}">${money(s.equity)}</div>
      <div class="n2">${pct==null?"awaiting first heartbeat":
        (pct>=0?"+":"")+pct.toFixed(2)+"% vs start"}</div></div>
    <div class="card"><div class="k">Plans refused</div>
      <div class="v">${s.plans_refused}<span style="font-size:15px;color:var(--muted)">/${s.plans_proposed||0}</span></div>
      <div class="n2">${s.refusal_rate==null?"no plans yet":
        "the kernel rejected "+(s.refusal_rate*100).toFixed(0)+"% of what the model proposed"}</div></div>
    <div class="card"><div class="k">Orders placed</div>
      <div class="v">${s.orders_accepted}</div>
      <div class="n2">every one carries a broker order id</div></div>
    <div class="card"><div class="k">Restarts survived</div>
      <div class="v">${Math.max(0,s.restarts-1)}</div>
      <div class="n2">${s.crash_recoveries} torn write${s.crash_recoveries===1?"":"s"} recovered</div></div>`;
}

function chain(v,s){
  const el=document.getElementById("chain");
  const c = v.ok ? "var(--pos)":"var(--neg)";
  el.innerHTML = `<span class="dot" style="background:${c}"></span>
    <b style="color:${c}">${v.ok?"CHAIN INTACT":"CHAIN BROKEN"}</b>
    <span class="mono" style="font-size:12.5px;color:var(--muted)">${esc(v.detail)}</span>
    ${s.anchors?`<span class="pill" style="color:var(--accent)">${s.anchors} anchored</span>`:""}`;
}

function chart(pts){
  const box=document.getElementById("chart");
  if(pts.length<2){ box.innerHTML='<div class="empty">Equity is plotted from heartbeats. Nothing recorded yet.</div>'; return; }
  const W=1000,H=210,P=34;
  const ys=pts.map(p=>p.equity), lo=Math.min(...ys), hi=Math.max(...ys);
  const pad=(hi-lo)*0.12||1, LO=lo-pad, HI=hi+pad;
  const x=i=>P+i*(W-2*P)/(pts.length-1), y=v=>H-P-(v-LO)/(HI-LO)*(H-2*P);
  const d=pts.map((p,i)=>`${i?"L":"M"}${x(i).toFixed(1)},${y(p.equity).toFixed(1)}`).join("");
  const up=ys[ys.length-1]>=ys[0], col=up?"var(--pos)":"var(--neg)";
  const base=pts.map((p,i)=>`${i?"L":"M"}${x(i).toFixed(1)},${y(p.equity).toFixed(1)}`).join("")
    +`L${x(pts.length-1).toFixed(1)},${H-P} L${P},${H-P} Z`;
  box.innerHTML=`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Account equity over time">
    <line x1="${P}" x2="${W-P}" y1="${H-P}" y2="${H-P}" stroke="var(--rule)"/>
    <path d="${base}" fill="${col}" opacity=".07"/>
    <path d="${d}" fill="none" stroke="${col}" stroke-width="1.8"/>
    <circle cx="${x(pts.length-1)}" cy="${y(ys[ys.length-1])}" r="3.4" fill="${col}"/>
    <text x="${P}" y="18" font-size="11" fill="var(--muted)">${money(hi)}</text>
    <text x="${P}" y="${H-8}" font-size="11" fill="var(--muted)">${money(lo)}</text>
  </svg>`;
}

function line(r){
  const tone=TONE[r.event]||"sys";
  const p=r.payload||{};
  let body="";
  if(r.event==="PLAN_REFUSED"||r.event==="PLAN_DISCARDED_INVALID"){
    body=`<span class="why">refused: ${esc(p.reason||p.failed_invariant||"—")}</span>`;
  } else if(r.event==="PLAN_APPROVED"){
    body=`${esc(p.symbol||p.plan_id||"")} — passed ${esc(p.checks_passed??"all")} invariants`;
  } else if(r.event==="ORDER_ACCEPTED"){
    body=`${esc(p.symbol||"")} ${esc(p.side||"")} ${esc(p.qty||"")} @ ${esc(p.limit_price??"mkt")}
      <span style="color:var(--muted)">· broker ${esc(p.broker_order_id||"—")}</span>`;
  } else if(r.event==="THESIS_COMPLETE"){
    body=esc(p.thesis||`${p.plans??0} plan(s) proposed`);
  } else if(r.event==="STARTUP"){
    body=`account ${esc(p.account_number||"—")} · equity ${esc(p.equity||"—")} · env ${esc(p.env||"—")}`;
  } else if(r.event==="TORN_ENTRY_DISCARDED"){
    body=`recovered from an interrupted write (${esc(p.bytes)} bytes) at seq ${esc(p.resumed_at_seq)}`;
  } else {
    body=esc(JSON.stringify(p).slice(0,150));
  }
  return `<div class="row t-${tone}">
    <div class="ts">#${r.seq} · ${esc(r.ts.replace("T"," ").replace("Z"," UTC"))}</div>
    <div class="ev t-${tone}">${esc(r.event)}</div>
    <div class="body">${body}
      <details><summary>payload &amp; hash</summary>
      <pre>${esc(JSON.stringify(p,null,1))}
hash      ${esc(r.hash)}
prev_hash ${esc(r.prev_hash)}</pre></details>
    </div></div>`;
}

async function timeline(){
  const rows=await j(`/api/journal?limit=400&all_events=${RAW}`);
  let f=rows;
  if(FILTER==="ref") f=rows.filter(r=>r.event==="PLAN_REFUSED"||r.event==="PLAN_DISCARDED_INVALID");
  if(FILTER==="ord") f=rows.filter(r=>r.event==="ORDER_ACCEPTED"||r.event==="POSITION_CLOSED");
  const el=document.getElementById("tl");
  el.innerHTML = f.length ? f.slice().reverse().map(line).join("")
    : '<div class="empty">No entries match. The agent writes here on every decision.</div>';
}

function calendar(){
  fetch("/api/calendar").then(r=>r.json()).then(d=>{
    document.getElementById("cal").innerHTML =
      `<tr><th>Catalyst</th><th>When (ET)</th><th>In window</th></tr>` +
      d.events.map(e=>`<tr><td>${esc(e.name)}</td><td class="n">${esc(e.when)}</td>
        <td>${e.in_window?'<span class="pos">scored</span>'
          :'<span class="warn">after measurement — excluded</span>'}</td></tr>`).join("") +
      `<tr><td colspan="3" style="color:var(--muted);font-size:12.5px;border:0;padding-top:10px">
        Measured at <b class="mono">${esc(d.measurement)}</b> — ${esc(d.source)}</td></tr>`;
  });
}

function wire(){
  const set=(k,v)=>{FILTER=k;RAW=v;
    for(const [id,key] of [["f-all","all"],["f-ref","ref"],["f-ord","ord"]])
      document.getElementById(id).setAttribute("aria-pressed", String(key===k));
    document.getElementById("f-raw").setAttribute("aria-pressed", String(v));
    timeline();};
  document.getElementById("f-all").onclick=()=>set("all",RAW);
  document.getElementById("f-ref").onclick=()=>set("ref",RAW);
  document.getElementById("f-ord").onclick=()=>set("ord",RAW);
  document.getElementById("f-raw").onclick=()=>set(FILTER,!RAW);
}

async function tick(){
  try{
    const [s,v,e]=await Promise.all([j("/api/summary"),j("/api/verify"),j("/api/equity")]);
    stats(s); chain(v,s); chart(e); await timeline();
  }catch(err){ console.error(err); }
}
wire(); calendar(); tick(); setInterval(tick, 20000);
</script></body></html>
"""


@app.get("/api/calendar")
def api_calendar() -> JSONResponse:
    post = {e.name for e in post_measurement_events()}
    return JSONResponse({
        "measurement": MEASUREMENT_ET.strftime("%a %d %b %Y %H:%M ET"),
        "source": MEASUREMENT_SOURCE,
        "events": [{"name": e.name,
                    "when": e.when.strftime("%a %d %b %H:%M"),
                    "tier": e.tier,
                    "in_window": e.name not in post}
                   for e in CALENDAR],
    })
