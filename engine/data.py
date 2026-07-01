"""
Datenzugriff.

  * Finnhub  -> Echtzeit-Quotes, Earnings-Kalender, News, Analysten-Empfehlungen
               (= die Katalysatoren, die Cohens Methode antreiben)
  * yfinance -> Tages-Charts (für Trend-MA & ATR), kein API-Key nötig

Ein einfacher Token-Bucket hält uns unter dem Finnhub-Free-Limit (60/min).
"""

import threading
import time
from datetime import datetime, timedelta

import requests

from config import (FINNHUB_API_KEY, FINNHUB_BASE, FINNHUB_MAX_CALLS_PER_MIN,
                    NEWS_LOOKBACK_DAYS)

_session = requests.Session()
_rate_lock = threading.Lock()
_calls = []  # Zeitstempel der letzten Calls
_warned_no_key = False


def _throttle():
    """Blockiert, bis ein weiterer Call im Minutenbudget liegt."""
    with _rate_lock:
        now = time.time()
        while _calls and now - _calls[0] > 60:
            _calls.pop(0)
        if len(_calls) >= FINNHUB_MAX_CALLS_PER_MIN:
            sleep_for = 60 - (now - _calls[0]) + 0.05
            time.sleep(max(sleep_for, 0))
            now = time.time()
            while _calls and now - _calls[0] > 60:
                _calls.pop(0)
        _calls.append(time.time())


def _get(path, params=None):
    global _warned_no_key
    if not FINNHUB_API_KEY:
        if not _warned_no_key:
            _warned_no_key = True
            print("[WARN] FINNHUB_API_KEY fehlt – Datenabruf deaktiviert, "
                  "bitte in .env / Render-Env setzen.", flush=True)
        return None
    _throttle()
    params = dict(params or {})
    params["token"] = FINNHUB_API_KEY
    try:
        r = _session.get(f"{FINNHUB_BASE}{path}", params=params, timeout=15)
        if r.status_code == 429:
            time.sleep(2)
            return None
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


# ---- Quotes ----------------------------------------------------------------

def quote(symbol):
    """
    Liefert {price, open, high, low, prev_close, change_pct} oder None.
    Finnhub /quote ist Echtzeit (US): c=current, o=open, h=high, l=low, pc=prev close.
    """
    d = _get("/quote", {"symbol": symbol})
    if not d or d.get("c") in (None, 0):
        return None
    c, pc = d.get("c"), d.get("pc") or d.get("c")
    return {
        "price": c,
        "open": d.get("o"),
        "high": d.get("h"),
        "low": d.get("l"),
        "prev_close": pc,
        "change_pct": round(100 * (c - pc) / pc, 2) if pc else 0.0,
    }


# ---- Katalysatoren ---------------------------------------------------------

def earnings_calendar(days_ahead=7):
    today = datetime.utcnow().date()
    d = _get("/calendar/earnings", {
        "from": today.isoformat(),
        "to": (today + timedelta(days=days_ahead)).isoformat(),
    })
    if not d:
        return []
    return d.get("earningsCalendar", []) or []


def company_news(symbol):
    today = datetime.utcnow().date()
    d = _get("/company-news", {
        "symbol": symbol,
        "from": (today - timedelta(days=NEWS_LOOKBACK_DAYS)).isoformat(),
        "to": today.isoformat(),
    })
    return d if isinstance(d, list) else []


def recommendation(symbol):
    """Neueste Analysten-Empfehlung -> Trend long/short als Katalysator."""
    d = _get("/stock/recommendation", {"symbol": symbol})
    if not d or not isinstance(d, list) or not d:
        return None
    latest = d[0]
    bull = latest.get("strongBuy", 0) + latest.get("buy", 0)
    bear = latest.get("strongSell", 0) + latest.get("sell", 0)
    return {"bull": bull, "bear": bear, "hold": latest.get("hold", 0)}


# ---- Tages-Charts via yfinance (Trend & ATR) -------------------------------

_yf_cache = {}
_yf_cache_ttl = 60 * 60  # 1h


def daily_history(symbol, lookback=70):
    """
    Liefert dict mit {closes:[...], highs:[...], lows:[...]} der letzten Tage
    oder None. Gecached (1h), da yfinance langsam ist und sich Tagesdaten
    intraday kaum ändern.
    """
    key = symbol.upper()
    now = time.time()
    cached = _yf_cache.get(key)
    if cached and now - cached[0] < _yf_cache_ttl:
        return cached[1]
    try:
        import yfinance as yf
        df = yf.Ticker(symbol).history(period=f"{lookback}d", interval="1d",
                                       auto_adjust=False)
        if df is None or df.empty:
            _yf_cache[key] = (now, None)
            return None
        hist = {
            "closes": [float(x) for x in df["Close"].tolist()],
            "highs": [float(x) for x in df["High"].tolist()],
            "lows": [float(x) for x in df["Low"].tolist()],
        }
        _yf_cache[key] = (now, hist)
        return hist
    except Exception:
        _yf_cache[key] = (now, None)
        return None
