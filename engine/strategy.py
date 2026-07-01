"""
Die Cohen-Strategie als konkrete Regeln — vollständig über ein `Params`-Objekt
parametrierbar (Champion wie Challenger nutzen denselben Code, nur andere P).

Aufbau pro Aktie (wie Cohen):  Katalysator  +  Chart-Timing  +  Risiko.
  1. Markt-/Sektor-Gate (40/30/30): nur in Trendrichtung handeln (abschaltbar).
  2. Katalysator (Earnings/News/Analysten/Momentum) — per P.require_catalyst.
  3. Chart bestätigt Einstieg (Breakout/Dip bzw. Top-Rollover) — per Schalter.
  4. Jede Position: These, Stop (=Invalidierung), Ziel, Größe nach Risiko.
"""

import math


# ---- Indikatoren -----------------------------------------------------------

def sma(values, n):
    if not values or len(values) < n:
        return None
    return sum(values[-n:]) / n


def atr(highs, lows, closes, n):
    if not closes or len(closes) < n + 1:
        return None
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    if len(trs) < n:
        return None
    return sum(trs[-n:]) / n


def trend_from_history(hist, P):
    """'up' / 'down' / 'flat' anhand Schlusskurs vs. Trend-MA."""
    if not hist or not hist.get("closes"):
        return "flat"
    closes = hist["closes"]
    ma = sma(closes, P.trend_ma_days)
    if ma is None:
        ma = sma(closes, max(5, len(closes) // 2))
    if ma is None:
        return "flat"
    last = closes[-1]
    if last > ma * 1.005:
        return "up"
    if last < ma * 0.995:
        return "down"
    return "flat"


# ---- Einstiegs-Entscheidung ------------------------------------------------

def entry_decision(q, hist, catalyst, market_trend, sector_trend, P):
    """
    Gibt zurück: dict mit side/thesis/stop/target/risk_per_share  oder None.
    `catalyst` = {'dir': +1/-1/0, 'has': bool, 'detail': str}
    """
    if not q:
        return None
    if P.require_catalyst and not (catalyst and catalyst.get("has")):
        return None  # Cohen: kein Katalysator -> kein Trade

    price = q["price"]
    closes = (hist or {}).get("closes") or []
    highs = (hist or {}).get("highs") or []
    lows = (hist or {}).get("lows") or []

    fast = sma(closes, P.fast_ma_days) if closes else None
    a = atr(highs, lows, closes, P.atr_days) if closes else None
    change = q.get("change_pct", 0.0)
    cdir = (catalyst or {}).get("dir", 0)

    above_fast = fast is not None and price >= fast
    below_fast = fast is not None and price <= fast

    market_ok_long = (not P.require_market_gate) or market_trend != "down"
    sector_ok_long = (not P.require_sector_gate) or sector_trend != "down"
    market_ok_short = (not P.require_market_gate) or market_trend != "up"
    sector_ok_short = (not P.require_sector_gate) or sector_trend != "up"

    side = None
    # ---- LONG
    if P.allow_long and cdir >= 0 and market_ok_long and sector_ok_long:
        breakout = P.allow_breakout and above_fast and change > P.entry_long_change_min
        dip = (P.allow_dip and change > P.entry_dip_change_min
               and fast and price >= fast * P.entry_dip_ma_frac)
        if breakout or dip:
            side = "long"
    # ---- SHORT
    if side is None and P.allow_short and cdir <= 0 and market_ok_short and sector_ok_short:
        if below_fast and change < P.entry_short_change_max:
            side = "short"

    if side is None:
        return None

    if a and a > 0:
        stop_dist = P.atr_stop_mult * a
    else:
        stop_dist = price * P.fallback_stop_pct / 100.0
    if stop_dist <= 0:
        return None

    if side == "long":
        stop = price - stop_dist
        target = price + P.target_r_multiple * stop_dist
    else:
        stop = price + stop_dist
        target = price - P.target_r_multiple * stop_dist

    return {
        "side": side,
        "thesis": (catalyst or {}).get("detail", "Setup"),
        "stop": round(stop, 2),
        "target": round(target, 2),
        "risk_per_share": round(stop_dist, 4),
        "price": price,
    }


def size_position(equity, price, risk_per_share, P):
    """Cohen-Sizing: feste Risiko-Einheit pro Trade, gedeckelt auf Notional-%."""
    if risk_per_share <= 0 or price <= 0:
        return 0
    risk_amount = equity * P.risk_per_trade_pct / 100.0
    qty = math.floor(risk_amount / risk_per_share)
    max_notional = equity * P.max_position_pct / 100.0
    qty = min(qty, math.floor(max_notional / price))
    return max(qty, 0)


# ---- Management offener Positionen -----------------------------------------

def manage_position(p, q, P):
    """
    Rückgabe: ('exit'|'half_out'|'trail'|'hold', info)
    """
    if not q:
        return ("hold", {})
    price = q["price"]
    side = p["side"]
    entry = p["avg_price"]
    risk_r = p["risk_r"] or (abs(entry - p["stop"]) or 1e-9)

    if side == "long":
        r_now = (price - entry) / risk_r
        stop_hit = price <= p["stop"]
        target_hit = price >= p["target"]
    else:
        r_now = (entry - price) / risk_r
        stop_hit = price >= p["stop"]
        target_hit = price <= p["target"]

    if stop_hit:
        return ("exit", {"reason": "Stop/Invalidierung erreicht"})
    if target_hit:
        return ("exit", {"reason": f"Ziel {P.target_r_multiple}R erreicht"})
    if r_now <= -P.half_out_trigger_r:
        return ("half_out", {"reason": f"Schwäche bei {r_now:.1f}R – halbe Position raus"})

    if r_now >= P.press_winner_at_r:
        be = entry
        if side == "long":
            new_stop = max(p["stop"], be, price - risk_r)
            if new_stop > p["stop"] + 1e-6:
                return ("trail", {"new_stop": round(new_stop, 2)})
        else:
            new_stop = min(p["stop"], be, price + risk_r)
            if new_stop < p["stop"] - 1e-6:
                return ("trail", {"new_stop": round(new_stop, 2)})

    return ("hold", {})
