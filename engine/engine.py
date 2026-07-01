"""
Trading-Engine: orchestriert Daten -> Strategie -> Paper-Broker im Dauerlauf.

Läuft in einem Hintergrund-Thread (gestartet von app.py). Der Prozess läuft 24/7
(cron-job.org hält Render wach), gehandelt wird aber nur zur US-Börsenzeit.
"""

import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from config import (ANALYST_ENABLED, CYCLE_SECONDS_CLOSED, CYCLE_SECONDS_MARKET,
                    EARNINGS_LOOKAHEAD_DAYS, MARKET_CLOSE, MARKET_ETF,
                    MARKET_HOLIDAYS_2026, MARKET_OPEN, MARKET_TZ,
                    MAX_ACTIVE_WATCHLIST, SECTOR_ETF)
from engine import analyst, broker, data, store, strategy
from engine import params as PRM
from engine.universe import UNIVERSE, all_symbols, sector_of

_stop = threading.Event()
_status = {"running": False, "market_open": False, "last_cycle": None,
           "active_watchlist": [], "halted_today": False, "phase": "init"}

# Katalysator-Buch: symbol -> {"has":bool,"dir":int,"detail":str}
_catalysts = {}
_last_catalyst_day = None
_sector_trends = {}
_market_trend = "flat"
_regime_ts = 0
_last_marks = {}  # symbol -> letzter bekannter Kurs (für Dashboard-P&L, ohne Extra-Calls)
_live = PRM.Params()  # aktive Champion-Params des Live-Bots (wird neu geladen)
_cycle_count = 0
_BACKUP_EVERY = 60    # DB-Backup ~stündlich (60 Zyklen × 60s)


def _reload_params():
    """Lädt die aktuellen Champion-Params (kann sich nach Promotion ändern)."""
    global _live
    _live = PRM.load_champion()
    return _live


# ---- Marktzeiten -----------------------------------------------------------

def market_open(now_et=None):
    now = now_et or datetime.now(ZoneInfo(MARKET_TZ))
    if now.weekday() >= 5:
        return False
    if now.strftime("%Y-%m-%d") in MARKET_HOLIDAYS_2026:
        return False
    o = now.replace(hour=MARKET_OPEN[0], minute=MARKET_OPEN[1], second=0, microsecond=0)
    c = now.replace(hour=MARKET_CLOSE[0], minute=MARKET_CLOSE[1], second=0, microsecond=0)
    return o <= now <= c


# ---- Regime (Markt/Sektor-Gate) -------------------------------------------

def refresh_regime():
    """Markt- und Sektortrends (gecached über yfinance-Tagesdaten, ~1x/h)."""
    global _market_trend, _sector_trends, _regime_ts
    if time.time() - _regime_ts < 3600 and _sector_trends:
        return
    _market_trend = strategy.trend_from_history(data.daily_history(MARKET_ETF), _live)
    trends = {}
    for sector, etf in SECTOR_ETF.items():
        trends[sector] = strategy.trend_from_history(data.daily_history(etf), _live)
    _sector_trends = trends
    _regime_ts = time.time()
    store.log("INFO", f"Regime aktualisiert: Markt={_market_trend} | "
                      + ", ".join(f"{s}:{t}" for s, t in trends.items()))


# ---- Katalysatoren & aktive Watchlist --------------------------------------

def build_catalysts():
    """Einmal pro Tag: Earnings-Kalender + Analysten/News -> Katalysator-Buch."""
    global _catalysts, _last_catalyst_day
    _catalysts = {}
    store.clear_catalysts()

    # 1) Earnings in den nächsten Tagen, gefiltert aufs Universum
    earnings_syms = []
    try:
        for e in data.earnings_calendar(EARNINGS_LOOKAHEAD_DAYS):
            sym = (e.get("symbol") or "").upper()
            if sym in UNIVERSE:
                earnings_syms.append((sym, e.get("date", "")))
    except Exception as ex:
        store.log("WARN", f"Earnings-Kalender Fehler: {ex}")

    seen = set()
    for sym, date in earnings_syms:
        if sym in seen:
            continue
        seen.add(sym)
        cdir, bits = 0, ["Earnings bevorstehend"]
        rec = data.recommendation(sym)
        if rec:
            if rec["bull"] > rec["bear"]:
                cdir = 1
                bits.append("Analysten bullish")
            elif rec["bear"] > rec["bull"]:
                cdir = -1
                bits.append("Analysten bearish")
        detail = " · ".join(b for b in bits if b)
        _catalysts[sym] = {"has": True, "dir": cdir, "detail": detail}
        store.set_catalyst(sym, "EARNINGS", detail, date)

    # 2) Aktive Watchlist: Earnings-Namen + rotierender Momentum-Scan
    active = list(seen)
    if len(active) < MAX_ACTIVE_WATCHLIST:
        pool = [s for s in all_symbols() if s not in seen]
        offset = datetime.utcnow().timetuple().tm_yday % max(len(pool), 1)
        rotated = pool[offset:] + pool[:offset]
        active += rotated[: MAX_ACTIVE_WATCHLIST - len(active)]

    _status["active_watchlist"] = active
    _last_catalyst_day = datetime.now(ZoneInfo(MARKET_TZ)).strftime("%Y-%m-%d")
    store.log("INFO", f"Watchlist gebaut: {len(active)} Symbole, "
                      f"{len(seen)} mit Earnings-Katalysator.")


def catalyst_for(symbol, q):
    """Kombiniert Tages-Katalysator mit Live-Momentum (Schwelle aus Champion-Params)."""
    base = dict(_catalysts.get(symbol, {"has": False, "dir": 0, "detail": ""}))
    change = q.get("change_pct", 0.0) if q else 0.0
    if _live.use_momentum_catalyst and abs(change) >= _live.momentum_catalyst_pct:
        base["has"] = True
        mom = f"Momentum {change:+.1f}% heute"
        base["detail"] = (base["detail"] + " · " + mom).strip(" ·") if base["detail"] else mom
        if base.get("dir", 0) == 0:
            base["dir"] = 1 if change > 0 else -1
    return base


# ---- Daily-Loss-Limit ------------------------------------------------------

def check_daily_limit(equity_value):
    today = datetime.now(ZoneInfo(MARKET_TZ)).strftime("%Y-%m-%d")
    day = store.get_state("loss_day")
    if day != today:
        store.set_state("loss_day", today)
        store.set_state("day_start_equity", equity_value)
        _status["halted_today"] = False
        return False
    start = float(store.get_state("day_start_equity", equity_value))
    if start > 0 and equity_value <= start * (1 - _live.daily_loss_limit_pct / 100.0):
        if not _status["halted_today"]:
            store.log("RISK", f"Daily-Loss-Limit erreicht (-{_live.daily_loss_limit_pct}%). "
                              f"Keine neuen Trades heute.")
        _status["halted_today"] = True
        return True
    return False


# ---- Ein Handelszyklus -----------------------------------------------------

def run_cycle(day):
    _reload_params()  # aktuelle Champion-Params (kann nach Promotion gewechselt haben)
    P = _live
    refresh_regime()

    # aktuelle Quotes für Watchlist + offene Positionen (einmal abrufen, geteilt)
    open_positions = {p["symbol"]: p for p in store.get_positions()}
    symbols = list(dict.fromkeys(_status["active_watchlist"] + list(open_positions)))
    marks, quotes, hist_map = {}, {}, {}
    for sym in symbols:
        q = data.quote(sym)
        if q:
            quotes[sym] = q
            marks[sym] = q["price"]
            _last_marks[sym] = q["price"]
            hist_map[sym] = data.daily_history(sym)  # gecached

    cash, equity_value = broker.equity(marks)
    halted = check_daily_limit(equity_value)

    # 1) Offene Positionen managen (zuerst Risiko!)
    for sym, p in open_positions.items():
        q = quotes.get(sym)
        if not q:
            continue
        action, info = strategy.manage_position(p, q, P)
        if action == "exit":
            broker.reduce_position(sym, p["qty"], q["price"], info.get("reason", ""))
        elif action == "half_out":
            broker.reduce_position(sym, p["qty"] / 2.0, q["price"], info.get("reason", ""))
        elif action == "trail":
            p["stop"] = info["new_stop"]
            store.upsert_position(p)

    # 2) Neue Einstiege suchen (nur wenn nicht gehaltet & Slots frei)
    open_count = len(store.get_positions())
    gross = broker.gross_exposure(marks)          # aktuell eingesetztes Brutto-Kapital
    buying_power = max(equity_value, 0) * P.leverage  # Grenze: Hebel × Equity
    if not halted:
        for sym in _status["active_watchlist"]:
            if open_count >= P.max_open_positions:
                break
            if store.get_position(sym):
                continue
            q = quotes.get(sym)
            if not q:
                continue
            cat = catalyst_for(sym, q)
            sec_trend = _sector_trends.get(sector_of(sym), "flat")
            plan = strategy.entry_decision(q, hist_map.get(sym), cat,
                                           _market_trend, sec_trend, P)
            if not plan:
                continue
            qty = strategy.size_position(equity_value, plan["price"], plan["risk_per_share"], P)
            # Kaufkraft-Grenze (Hebel): Notional kürzen, damit Brutto <= Hebel×Equity
            room = buying_power - gross
            if room < plan["price"]:
                continue
            qty = min(qty, int(room // plan["price"]))
            if qty < 1:
                continue
            ok = False
            if plan["side"] == "long":
                ok = broker.open_long(sym, qty, plan["price"], plan["stop"],
                                      plan["target"], plan["risk_per_share"],
                                      plan["thesis"], plan["price"])
            else:
                ok = broker.open_short(sym, qty, plan["price"], plan["stop"],
                                       plan["target"], plan["risk_per_share"],
                                       plan["thesis"], plan["price"])
            if ok:
                open_count += 1
                gross += qty * plan["price"]

    # 3) Equity-Snapshot fürs Dashboard
    cash, equity_value = broker.equity(marks)
    store.snapshot_equity(round(cash, 2), equity_value)

    # 4) Analyst: alle Schatten-Bücher auf denselben Quotes mitlaufen lassen
    if ANALYST_ENABLED:
        analyst.on_cycle(_status["active_watchlist"], quotes, hist_map, _catalysts,
                         _market_trend, _sector_trends, sector_of, day)

    # 5) Regelmäßiges DB-Backup (Absturz-/Beschädigungsschutz)
    global _cycle_count
    _cycle_count += 1
    if _cycle_count % _BACKUP_EVERY == 0:
        store.backup_db()


# ---- Hauptschleife ---------------------------------------------------------

def loop():
    store.maybe_restore_backup()  # Free-Plan: DB ggf. aus Backup wiederherstellen
    store.init_db()
    _status["running"] = True
    store.log("INFO", "Cohen-Bot Engine gestartet.")
    # initiale Katalysatoren/Regime/Analyst
    try:
        _reload_params()
        refresh_regime()
        build_catalysts()
        if ANALYST_ENABLED:
            analyst.init()
    except Exception as ex:
        store.log("ERROR", f"Init-Fehler: {ex}")

    while not _stop.is_set():
        try:
            now_et = datetime.now(ZoneInfo(MARKET_TZ))
            is_open = market_open(now_et)
            _status["market_open"] = is_open
            today = now_et.strftime("%Y-%m-%d")

            # Watchlist täglich neu bauen (vor Open)
            if today != _last_catalyst_day:
                build_catalysts()

            if is_open:
                _status["phase"] = "TRADING"
                run_cycle(today)
                if ANALYST_ENABLED:
                    analyst.maybe_on_day(today)  # self-gated: 1x pro Bewertungstag
            else:
                _status["phase"] = "ÜBERWACHUNG (Markt zu)"
                # auch außerhalb gelegentlich Equity snapshoten (für Kontinuität)
                positions = store.get_positions()
                marks = {p["symbol"]: p["avg_price"] for p in positions}
                cash, val = broker.equity(marks)
                store.snapshot_equity(round(cash, 2), val)

            _status["last_cycle"] = store._now()
        except Exception as ex:
            store.log("ERROR", f"Cycle-Fehler: {ex}")

        # Schlafen (in kleinen Schritten, damit Stop schnell greift)
        delay = CYCLE_SECONDS_MARKET if _status["market_open"] else CYCLE_SECONDS_CLOSED
        for _ in range(delay):
            if _stop.is_set():
                break
            time.sleep(1)

    _status["running"] = False
    store.log("INFO", "Engine gestoppt.")


def start():
    t = threading.Thread(target=loop, name="cohen-engine", daemon=True)
    t.start()
    return t


def stop():
    _stop.set()


def status():
    return dict(_status, market_trend=_market_trend, sector_trends=_sector_trends)


def last_marks():
    return dict(_last_marks)
