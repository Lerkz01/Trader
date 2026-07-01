"""
FastAPI-App: serviert das HTML-Dashboard, die JSON-API und den /ping-Endpoint
für cron-job.org (hält den Render-Free-Service wach). Beim Start wird die
Trading-Engine in einem Hintergrund-Thread hochgefahren.
"""

import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from config import BASE_DIR
from engine import broker, engine, store

app = FastAPI(title="Cohen-Bot", docs_url=None, redoc_url=None)


@app.on_event("startup")
def _startup():
    store.init_db()
    engine.start()


# ---- Keep-alive (cron-job.org pingt diese URL alle 5 Min) ------------------

@app.get("/ping", response_class=PlainTextResponse)
@app.head("/ping", response_class=PlainTextResponse)
def ping():
    return "ok"


@app.get("/health")
def health():
    return {"status": "ok", "engine": engine.status()}


# ---- API -------------------------------------------------------------------

@app.get("/api/state")
def api_state():
    from engine import params as PRM
    positions = store.get_positions()
    marks = {**{p["symbol"]: p["avg_price"] for p in positions}, **engine.last_marks()}
    cash, equity_value = broker.equity(marks)
    start = store.get_start_cash()
    stats = store.trade_stats()
    st = engine.status()
    gross = broker.gross_exposure(marks)
    lev = PRM.load_champion().leverage
    return {
        "cash": round(cash, 2),
        "equity": equity_value,
        "start_cash": start,
        "pnl_total": round(equity_value - start, 2),
        "pnl_pct": round(100 * (equity_value - start) / start, 2) if start else 0.0,
        "open_positions": len(positions),
        "leverage": lev,
        "gross_exposure": gross,
        "exposure_pct": round(100 * gross / equity_value, 1) if equity_value else 0.0,
        "total_fees": round(store.get_fees(), 2),
        "market_open": st.get("market_open"),
        "phase": st.get("phase"),
        "market_trend": st.get("market_trend"),
        "halted_today": st.get("halted_today"),
        "last_cycle": st.get("last_cycle"),
        "watchlist_size": len(st.get("active_watchlist", [])),
        **stats,
    }


@app.get("/api/positions")
def api_positions():
    marks = engine.last_marks()
    out = []
    for p in store.get_positions():
        mark = marks.get(p["symbol"], p["avg_price"])
        if p["side"] == "long":
            upnl = p["qty"] * (mark - p["avg_price"])
        else:
            upnl = p["qty"] * (p["avg_price"] - mark)
        risk = p["risk_r"] or abs(p["avg_price"] - p["stop"]) or 1e-9
        r_now = (mark - p["avg_price"]) / risk if p["side"] == "long" \
            else (p["avg_price"] - mark) / risk
        p = dict(p)
        p["mark"] = round(mark, 2)
        p["upnl"] = round(upnl, 2)
        p["upnl_pct"] = round(100 * upnl / (p["avg_price"] * p["qty"]), 2) if p["qty"] else 0.0
        p["r_now"] = round(r_now, 2)
        out.append(p)
    return out


@app.get("/api/trades")
def api_trades():
    return store.get_trades(200)


@app.get("/api/equity")
def api_equity():
    return store.get_equity_curve(1500)


@app.get("/api/catalysts")
def api_catalysts():
    return {"catalysts": store.get_catalysts(),
            "watchlist": engine.status().get("active_watchlist", [])}


@app.get("/api/logs")
def api_logs():
    return store.get_logs(150)


@app.get("/api/sectors")
def api_sectors():
    st = engine.status()
    return {"market": st.get("market_trend"), "sectors": st.get("sector_trends", {})}


@app.get("/api/analyst")
def api_analyst():
    from engine import analyst
    return analyst.state()


# ---- Snapshot: Zustand sichern/wiederherstellen (Free-Plan-Persistenz) -----

@app.get("/api/export")
def api_export():
    import time
    data = store.export_state()
    fn = f"cohen_snapshot_{time.strftime('%Y%m%d_%H%M', time.gmtime())}.json"
    return JSONResponse(data, headers={"Content-Disposition": f'attachment; filename="{fn}"'})


@app.post("/api/import")
async def api_import(request: Request):
    from engine import analyst
    try:
        data = await request.json()
    except Exception:
        return {"ok": False, "message": "Ungültige JSON-Datei."}
    ok, msg = store.import_state(data)
    if ok:
        analyst.init()  # Schatten-Bücher aus importiertem Zustand neu laden
    return {"ok": ok, "message": msg}


# ---- Statisches Dashboard (muss NACH den Routen gemountet werden) ----------

app.mount("/", StaticFiles(directory=str(BASE_DIR / "static"), html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("app:app", host="0.0.0.0", port=port)
