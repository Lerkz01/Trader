"""
Zentrale Konfiguration des Cohen-Bots.

Alle Strategie-Parameter sind hier gebündelt und entsprechen Steven Cohens
öffentlich bekannter Methodik (Katalysator + Chart + striktes Risikomanagement).
Werte können hier gefahrlos angepasst werden – es ist Spielgeld.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
# Datenpfad auslagerbar (z.B. auf eine Render-Disk) via Env DATA_DIR,
# damit die SQLite-Datenbank Redeploys/Neustarts überlebt.
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "cohen_bot.sqlite3"
BACKUP_PATH = DATA_DIR / "cohen_bot.backup.sqlite3"

# ----------------------------------------------------------------------------
# API / Daten
# ----------------------------------------------------------------------------
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "").strip()
FINNHUB_BASE = "https://finnhub.io/api/v1"
# Finnhub Free-Tier: 60 Calls/Minute. Wir bleiben konservativ darunter.
FINNHUB_MAX_CALLS_PER_MIN = 55

# ----------------------------------------------------------------------------
# Kapital
# ----------------------------------------------------------------------------
START_CASH = float(os.getenv("START_CASH", "10000"))

# ----------------------------------------------------------------------------
# Handelszeiten (US-Markt, Eastern Time)
# ----------------------------------------------------------------------------
MARKET_TZ = "America/New_York"
MARKET_OPEN = (9, 30)    # 09:30 ET
MARKET_CLOSE = (16, 0)   # 16:00 ET
# US-Feiertage 2026 (Markt geschlossen) – grobe Liste, erweiterbar.
MARKET_HOLIDAYS_2026 = {
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
    "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
}

# Loop-Frequenz
CYCLE_SECONDS_MARKET = 60     # während Börsenzeit alle 60s
CYCLE_SECONDS_CLOSED = 300    # außerhalb alle 5 Min (Überwachung/Vorbereitung)

# ----------------------------------------------------------------------------
# Cohen-Strategie: Risiko & Sizing
# ----------------------------------------------------------------------------
RISK_PER_TRADE_PCT = 1.0       # % der Equity, die bei Stop maximal verloren wird
MAX_POSITION_PCT = 20.0        # max. Notional einer Position in % der Equity
MAX_OPEN_POSITIONS = 6         # max. gleichzeitige Positionen (Konzentration)
DAILY_LOSS_LIMIT_PCT = 3.0     # ab -3% am Tag: keine neuen Trades mehr (Survival)
MAX_LEVERAGE = 2.0             # Hebel: Brutto-Exposure bis Hebel × Equity (Cohen/SAC ~4x)

# Handelsgebühren (je Fill = je Kauf UND je Verkauf). Umweltkosten, kein
# Strategie-Parameter -> für Champion & alle Challenger identisch (fairer Vergleich).
COMMISSION_BPS = 1.0           # Gebühr in Basispunkten des Notionals (1.0 bp = 0.01%)
COMMISSION_MIN_USD = 0.0       # optionale Mindestgebühr je Order (0 = keine)

# Stop / Invalidierung
ATR_STOP_MULT = 1.5            # Stop = Einstieg -/+ ATR_STOP_MULT * ATR
FALLBACK_STOP_PCT = 4.0        # falls kein ATR verfügbar: fester %-Stop
TARGET_R_MULTIPLE = 2.5        # Take-Profit-Ziel als Vielfaches des Risikos (R)

# Cohen "Fokus auf Verlierer": Halbierungs-Regel
HALF_OUT_TRIGGER_R = 0.6       # bei -0.6R UND Schwäche-Signal: halbe Position raus

# Gewinner nachkaufen ("press winners") nur wenn Stop >= Breakeven
PRESS_WINNER_AT_R = 1.0        # ab +1R Stop auf Breakeven ziehen und ggf. nachkaufen

# ----------------------------------------------------------------------------
# Cohen-Strategie: Signale
# ----------------------------------------------------------------------------
# Markt-/Sektor-Gate (40% Markt / 30% Sektor / 30% Aktie)
TREND_MA_DAYS = 50             # Tages-MA für Trendbestimmung (SPY & Sektor-ETF)
# Momentum/Chart
FAST_MA_DAYS = 10
ATR_DAYS = 14
# Katalysator-Fenster
EARNINGS_LOOKAHEAD_DAYS = 5    # Earnings in den nächsten X Tagen = Katalysator
NEWS_LOOKBACK_DAYS = 2         # News der letzten X Tage prüfen
# Aktive Watchlist-Größe (catalyst-getrieben, schont Rate-Limit)
MAX_ACTIVE_WATCHLIST = 35

# ----------------------------------------------------------------------------
# Sektor -> ETF Mapping (für das Sektor-Gate)
# ----------------------------------------------------------------------------
SECTOR_ETF = {
    "Technology": "XLK",
    "Semiconductors": "SMH",
    "Health Care": "XLV",
    "Biotech": "XBI",
    "Financials": "XLF",
    "Consumer Discretionary": "XLY",
    "Consumer Staples": "XLP",
    "Energy": "XLE",
    "Industrials": "XLI",
    "Communication": "XLC",
    "Materials": "XLB",
    "Utilities": "XLU",
    "Real Estate": "XLRE",
}
MARKET_ETF = "SPY"

# ----------------------------------------------------------------------------
# Analyst (selbst-optimierende Schicht: Champion vs. Challenger)
# ----------------------------------------------------------------------------
ANALYST_ENABLED = True
NUM_CHALLENGERS = 4            # parallele Schatten-Varianten
ANALYST_EVAL_EVERY_DAYS = 1   # wie oft evaluiert/bewertet wird (Tageswechsel)

# Promotion-Kriterien ("streng"):
PROMO_MIN_DAYS = 20           # Challenger muss >= X Handelstage gelaufen sein
PROMO_MIN_TRADES = 30         # und >= X abgeschlossene Trades haben
PROMO_IMPROVEMENT = 0.15      # Score muss Champion um >= 15% übertreffen
PROMO_STREAK_DAYS = 3         # an >= X Bewertungstagen am Stück besser sein

# KI-Analyst (Anthropic). Optional — nur aktiv, wenn ANTHROPIC_API_KEY gesetzt.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
ANALYST_MODEL = os.getenv("ANALYST_MODEL", "claude-opus-4-8").strip()
AI_PROPOSE_EVERY_DAYS = 5     # KI schlägt höchstens alle X Tage einen Challenger vor

