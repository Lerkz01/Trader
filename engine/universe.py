"""
Handelsuniversum: liquide S&P-500-Namen, gruppiert nach Cohen-relevanten
Sektoren. Jeder Eintrag mappt Ticker -> Sektor (für das Sektor-ETF-Gate).

Die Liste deckt die liquidesten Large-Caps ab. Sie ist bewusst auf ~120 Namen
begrenzt (statt aller 500), weil:
  1. Cohen handelt catalyst-getrieben, nicht blind das ganze Universum.
  2. Das Finnhub-Free-Tier-Rate-Limit (60 Calls/Min) keinen Voll-Scan erlaubt.
Die aktive Watchlist wird täglich aus diesem Universum per Katalysator gefiltert.
Erweiterbar: einfach weitere {Ticker: Sektor}-Paare ergänzen.
"""

UNIVERSE = {
    # --- Semiconductors (Cohen-Kernsektor) ---
    "NVDA": "Semiconductors", "AMD": "Semiconductors", "INTC": "Semiconductors",
    "AVGO": "Semiconductors", "QCOM": "Semiconductors", "MU": "Semiconductors",
    "TXN": "Semiconductors", "AMAT": "Semiconductors", "LRCX": "Semiconductors",
    "KLAC": "Semiconductors", "ADI": "Semiconductors", "MRVL": "Semiconductors",
    "NXPI": "Semiconductors", "MCHP": "Semiconductors", "ON": "Semiconductors",

    # --- Technology ---
    "AAPL": "Technology", "MSFT": "Technology", "ORCL": "Technology",
    "CRM": "Technology", "ADBE": "Technology", "CSCO": "Technology",
    "IBM": "Technology", "NOW": "Technology", "INTU": "Technology",
    "AMD ": "Technology", "PANW": "Technology", "SNPS": "Technology",
    "CDNS": "Technology", "ANET": "Technology", "DELL": "Technology",

    # --- Communication / Internet ---
    "GOOGL": "Communication", "META": "Communication", "NFLX": "Communication",
    "DIS": "Communication", "CMCSA": "Communication", "T": "Communication",
    "VZ": "Communication", "TMUS": "Communication",

    # --- Consumer Discretionary ---
    "AMZN": "Consumer Discretionary", "TSLA": "Consumer Discretionary",
    "HD": "Consumer Discretionary", "NKE": "Consumer Discretionary",
    "MCD": "Consumer Discretionary", "SBUX": "Consumer Discretionary",
    "LOW": "Consumer Discretionary", "BKNG": "Consumer Discretionary",
    "TGT": "Consumer Discretionary", "CMG": "Consumer Discretionary",

    # --- Consumer Staples ---
    "WMT": "Consumer Staples", "COST": "Consumer Staples", "PG": "Consumer Staples",
    "KO": "Consumer Staples", "PEP": "Consumer Staples", "MDLZ": "Consumer Staples",
    "CL": "Consumer Staples", "PM": "Consumer Staples",

    # --- Health Care / Pharma (Cohen-Kernsektor) ---
    "LLY": "Health Care", "UNH": "Health Care", "JNJ": "Health Care",
    "MRK": "Health Care", "PFE": "Health Care", "ABBV": "Health Care",
    "TMO": "Health Care", "ABT": "Health Care", "DHR": "Health Care",
    "BMY": "Health Care", "AMGN": "Health Care", "CVS": "Health Care",
    "ISRG": "Health Care", "MDT": "Health Care",

    # --- Biotech (Cohen-Kernsektor, katalysator-getrieben: FDA) ---
    "GILD": "Biotech", "VRTX": "Biotech", "REGN": "Biotech",
    "MRNA": "Biotech", "BIIB": "Biotech",

    # --- Financials ---
    "JPM": "Financials", "BAC": "Financials", "WFC": "Financials",
    "GS": "Financials", "MS": "Financials", "C": "Financials",
    "BLK": "Financials", "AXP": "Financials", "SCHW": "Financials",
    "V": "Financials", "MA": "Financials",

    # --- Industrials ---
    "BA": "Industrials", "CAT": "Industrials", "GE": "Industrials",
    "HON": "Industrials", "UPS": "Industrials", "RTX": "Industrials",
    "DE": "Industrials", "LMT": "Industrials", "UNP": "Industrials",

    # --- Energy ---
    "XOM": "Energy", "CVX": "Energy", "COP": "Energy",
    "SLB": "Energy", "EOG": "Energy", "MPC": "Energy",

    # --- Materials / Utilities / Real Estate ---
    "LIN": "Materials", "FCX": "Materials", "NEM": "Materials",
    "NEE": "Utilities", "DUK": "Utilities", "SO": "Utilities",
    "AMT": "Real Estate", "PLD": "Real Estate",
}

# Tippfehler-Schutz: doppelte/whitespace-Keys entfernen
UNIVERSE = {k.strip(): v for k, v in UNIVERSE.items() if k.strip()}


def all_symbols():
    return list(UNIVERSE.keys())


def sector_of(symbol):
    return UNIVERSE.get(symbol.strip().upper(), "Technology")
