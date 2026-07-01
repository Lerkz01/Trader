"""
SQLite-Persistenz. Thread-sicher über kurzlebige Verbindungen + WAL-Modus,
damit Engine-Thread und FastAPI-Thread gleichzeitig zugreifen können.

Hinweis Render-Free: Das Dateisystem ist ephemer – bei einem *Redeploy* wird
die SQLite-Datei zurückgesetzt. Solange der Service läuft (cron-job.org-Ping
hält ihn wach), bleiben die Daten erhalten. Für dauerhafte Historie über
Redeploys hinweg später auf Render-PostgreSQL umstellen (Upgrade-Pfad im README).
"""

import sqlite3
import threading
import time
from contextlib import contextmanager

from config import BACKUP_PATH, DB_PATH, START_CASH

_lock = threading.Lock()


@contextmanager
def _conn():
    con = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA journal_mode=WAL;")
        yield con
        con.commit()
    finally:
        con.close()


def init_db():
    with _lock, _conn() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS state (
                key   TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE IF NOT EXISTS positions (
                symbol        TEXT PRIMARY KEY,
                side          TEXT,         -- 'long' | 'short'
                qty           REAL,
                avg_price     REAL,
                stop          REAL,
                target        REAL,
                risk_r        REAL,         -- Risiko pro Aktie in $ (1R)
                thesis        TEXT,         -- Katalysator-Begründung
                opened_at     TEXT,
                high_water    REAL          -- bester Preis seit Entry (für Trailing/Press)
            );
            CREATE TABLE IF NOT EXISTS trades (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                ts         TEXT,
                symbol     TEXT,
                action     TEXT,            -- BUY / SELL / SHORT / COVER / HALF_OUT / PRESS
                side       TEXT,
                qty        REAL,
                price      REAL,
                pnl        REAL,            -- realisierter P&L (nur bei Schließung/Teilschließung)
                reason     TEXT
            );
            CREATE TABLE IF NOT EXISTS equity (
                ts     TEXT PRIMARY KEY,
                cash   REAL,
                value  REAL                 -- Gesamt-Equity (cash + Positionswert)
            );
            CREATE TABLE IF NOT EXISTS logs (
                id    INTEGER PRIMARY KEY AUTOINCREMENT,
                ts    TEXT,
                level TEXT,
                msg   TEXT
            );
            CREATE TABLE IF NOT EXISTS catalysts (
                symbol  TEXT PRIMARY KEY,
                kind    TEXT,               -- EARNINGS / NEWS / UPGRADE / DOWNGRADE
                detail  TEXT,
                date    TEXT,
                ts      TEXT
            );
            CREATE TABLE IF NOT EXISTS shadow (
                id     INTEGER PRIMARY KEY CHECK (id = 1),
                blob   TEXT
            );
            CREATE TABLE IF NOT EXISTS promotions (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                ts        TEXT,
                source    TEXT,              -- mutation | ai | seed
                old_score REAL,
                new_score REAL,
                detail    TEXT,
                params    TEXT
            );
            """
        )
        # Startkapital initialisieren
        row = con.execute("SELECT value FROM state WHERE key='cash'").fetchone()
        if row is None:
            con.execute("INSERT INTO state(key,value) VALUES('cash',?)", (str(START_CASH),))
            con.execute("INSERT OR REPLACE INTO state(key,value) VALUES('start_cash',?)", (str(START_CASH),))


# ---- State (cash etc.) -----------------------------------------------------

def get_cash():
    with _lock, _conn() as con:
        row = con.execute("SELECT value FROM state WHERE key='cash'").fetchone()
        return float(row["value"]) if row else 0.0


def set_cash(v):
    with _lock, _conn() as con:
        con.execute("INSERT OR REPLACE INTO state(key,value) VALUES('cash',?)", (str(v),))


def get_state(key, default=None):
    with _lock, _conn() as con:
        row = con.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def set_state(key, value):
    with _lock, _conn() as con:
        con.execute("INSERT OR REPLACE INTO state(key,value) VALUES(?,?)", (key, str(value)))


def get_start_cash():
    v = get_state("start_cash")
    return float(v) if v else START_CASH


def add_fee(amount):
    """Kumulierte Handelsgebühren mitzählen (fürs Dashboard)."""
    cur = float(get_state("total_fees", 0) or 0)
    set_state("total_fees", cur + amount)


def get_fees():
    return float(get_state("total_fees", 0) or 0)


# ---- Positionen ------------------------------------------------------------

def upsert_position(p: dict):
    with _lock, _conn() as con:
        con.execute(
            """INSERT OR REPLACE INTO positions
               (symbol, side, qty, avg_price, stop, target, risk_r, thesis, opened_at, high_water)
               VALUES (:symbol,:side,:qty,:avg_price,:stop,:target,:risk_r,:thesis,:opened_at,:high_water)""",
            p,
        )


def get_position(symbol):
    with _lock, _conn() as con:
        row = con.execute("SELECT * FROM positions WHERE symbol=?", (symbol,)).fetchone()
        return dict(row) if row else None


def get_positions():
    with _lock, _conn() as con:
        return [dict(r) for r in con.execute("SELECT * FROM positions ORDER BY opened_at")]


def delete_position(symbol):
    with _lock, _conn() as con:
        con.execute("DELETE FROM positions WHERE symbol=?", (symbol,))


# ---- Trades ----------------------------------------------------------------

def add_trade(symbol, action, side, qty, price, pnl=0.0, reason=""):
    with _lock, _conn() as con:
        con.execute(
            """INSERT INTO trades(ts,symbol,action,side,qty,price,pnl,reason)
               VALUES (?,?,?,?,?,?,?,?)""",
            (_now(), symbol, action, side, qty, price, pnl, reason),
        )


def get_trades(limit=200):
    with _lock, _conn() as con:
        return [dict(r) for r in con.execute(
            "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,))]


def trade_stats():
    """Trefferquote, Ø-Gewinn/-Verlust aus geschlossenen Trades (pnl != 0)."""
    with _lock, _conn() as con:
        rows = con.execute("SELECT pnl FROM trades WHERE pnl != 0").fetchall()
    pnls = [r["pnl"] for r in rows]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    total = len(pnls)
    return {
        "closed_trades": total,
        "win_rate": round(100 * len(wins) / total, 1) if total else 0.0,
        "avg_win": round(sum(wins) / len(wins), 2) if wins else 0.0,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0.0,
        "total_realized": round(sum(pnls), 2),
    }


# ---- Equity-Kurve ----------------------------------------------------------

def snapshot_equity(cash, value):
    with _lock, _conn() as con:
        con.execute("INSERT OR REPLACE INTO equity(ts,cash,value) VALUES(?,?,?)",
                    (_now(), cash, value))


def get_equity_curve(limit=1000):
    with _lock, _conn() as con:
        rows = con.execute("SELECT * FROM equity ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in reversed(rows)]


# ---- Logs ------------------------------------------------------------------

def log(level, msg):
    with _lock, _conn() as con:
        con.execute("INSERT INTO logs(ts,level,msg) VALUES(?,?,?)", (_now(), level, msg))
    print(f"[{level}] {msg}", flush=True)


def get_logs(limit=200):
    with _lock, _conn() as con:
        return [dict(r) for r in con.execute(
            "SELECT * FROM logs ORDER BY id DESC LIMIT ?", (limit,))]


# ---- Katalysatoren ---------------------------------------------------------

def set_catalyst(symbol, kind, detail, date):
    with _lock, _conn() as con:
        con.execute(
            "INSERT OR REPLACE INTO catalysts(symbol,kind,detail,date,ts) VALUES(?,?,?,?,?)",
            (symbol, kind, detail, date, _now()),
        )


def clear_catalysts():
    with _lock, _conn() as con:
        con.execute("DELETE FROM catalysts")


def get_catalysts():
    with _lock, _conn() as con:
        return [dict(r) for r in con.execute("SELECT * FROM catalysts ORDER BY date")]


# ---- Analyst: Schatten-Bücher & Promotionen --------------------------------

def save_shadow(blob_json):
    with _lock, _conn() as con:
        con.execute("INSERT OR REPLACE INTO shadow(id, blob) VALUES(1, ?)", (blob_json,))


def load_shadow():
    with _lock, _conn() as con:
        row = con.execute("SELECT blob FROM shadow WHERE id=1").fetchone()
        return row["blob"] if row else None


def add_promotion(source, old_score, new_score, detail, params_json):
    with _lock, _conn() as con:
        con.execute(
            """INSERT INTO promotions(ts, source, old_score, new_score, detail, params)
               VALUES(?,?,?,?,?,?)""",
            (_now(), source, old_score, new_score, detail, params_json),
        )


def get_promotions(limit=50):
    with _lock, _conn() as con:
        return [dict(r) for r in con.execute(
            "SELECT * FROM promotions ORDER BY id DESC LIMIT ?", (limit,))]


# ---- Snapshot Export/Import (Free-Plan: Zustand über Redeploys retten) ------

_SNAP_TABLES = ["state", "positions", "trades", "equity", "catalysts",
                "shadow", "promotions"]


def is_fresh():
    """True, wenn der Bot noch keine Positionen/Trades hat (leerer Neustart)."""
    with _lock, _conn() as con:
        tr = con.execute("SELECT COUNT(*) c FROM trades").fetchone()["c"]
        po = con.execute("SELECT COUNT(*) c FROM positions").fetchone()["c"]
    return tr == 0 and po == 0


def export_state():
    """Kompletter, wieder-einspielbarer Zustand als dict (ohne Logs)."""
    out = {}
    with _lock, _conn() as con:
        for t in _SNAP_TABLES:
            out[t] = [dict(r) for r in con.execute(f"SELECT * FROM {t}")]
    return out


def import_state(data):
    """Spielt einen Snapshot ein – NUR in einen frischen (leeren) Bot,
    damit ein laufender Bot nie versehentlich überschrieben wird."""
    if not is_fresh():
        return False, "Bot läuft bereits mit Daten – Import nur in einen leeren Bot möglich."
    with _lock, _conn() as con:
        for t in _SNAP_TABLES:
            rows = (data or {}).get(t) or []
            if not rows:
                continue
            con.execute(f"DELETE FROM {t}")
            cols = list(rows[0].keys())
            ph = ",".join(["?"] * len(cols))
            con.executemany(
                f"INSERT INTO {t} ({','.join(cols)}) VALUES ({ph})",
                [tuple(r.get(c) for c in cols) for r in rows])
    return True, "Snapshot geladen – der Bot macht dort weiter."


def maybe_restore_backup():
    """Beim Start: falls Haupt-DB leer/fehlt, aber ein Backup mit Daten da ist,
    das Backup wiederherstellen (schützt gegen Beschädigung)."""
    import os
    import shutil
    if not os.path.exists(BACKUP_PATH):
        return
    fresh = True
    if os.path.exists(DB_PATH):
        try:
            fresh = is_fresh()
        except Exception:
            fresh = True
    if not fresh:
        return
    try:
        # WAL/SHM der leeren DB entfernen, dann sauberes Backup einspielen
        for ext in ("", "-wal", "-shm"):
            p = str(DB_PATH) + ext
            if os.path.exists(p):
                os.remove(p)
        shutil.copyfile(BACKUP_PATH, DB_PATH)
        print("[INFO] DB aus Backup wiederhergestellt.", flush=True)
    except Exception as ex:
        print(f"[WARN] Restore fehlgeschlagen: {ex}", flush=True)


def backup_db():
    """Konsistenter Snapshot der DB (SQLite Online-Backup, auch während Schreibzugriff).
    Schützt gegen Dateibeschädigung; ergänzt WAL für maximale Absturzsicherheit."""
    try:
        with _lock:
            src = sqlite3.connect(DB_PATH, timeout=30)
            dst = sqlite3.connect(BACKUP_PATH, timeout=30)
            with dst:
                src.backup(dst)
            dst.close()
            src.close()
        return True
    except Exception as ex:
        print(f"[WARN] Backup fehlgeschlagen: {ex}", flush=True)
        return False


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
