"""
Paper-Broker: simuliert Order-Ausführung mit Spielgeld, aber echten Live-Kursen.

Realismus-Annahmen:
  * Market-Order füllt sofort zum aktuellen Kurs.
  * Slippage: kleiner Aufschlag/Abschlag (Standard 5 bps), realistisch für Large-Caps.
  * Keine Kommission (US-Broker handeln Aktien heute kommissionsfrei).
  * Long und Short möglich. Short = negative qty in der Position.
"""

from config import COMMISSION_BPS, COMMISSION_MIN_USD, START_CASH
from engine import store

SLIPPAGE_BPS = 5  # 0.05 %


def _fill_price(price, side, action):
    """Käufe etwas teurer, Verkäufe etwas billiger (gegen uns) – realistisch."""
    slip = price * SLIPPAGE_BPS / 10000.0
    if action in ("BUY", "PRESS", "COVER"):     # wir kaufen
        return price + slip
    return price - slip                          # wir verkaufen


def commission(notional):
    """Handelsgebühr je Fill: Basispunkte des Notionals, optional Mindestbetrag."""
    n = abs(notional)
    if n <= 0:
        return 0.0
    return round(max(n * COMMISSION_BPS / 10000.0, COMMISSION_MIN_USD), 4)


def equity(marks: dict):
    """Gesamt-Equity = Cash + Marktwert aller Positionen (long + short).
    Cash kann bei Hebel negativ werden (geliehenes Kapital)."""
    cash = store.get_cash()
    val = cash
    for p in store.get_positions():
        mark = marks.get(p["symbol"])
        if mark is None:
            mark = p["avg_price"]
        if p["side"] == "long":
            val += p["qty"] * mark
        else:  # short: Gewinn wenn Kurs fällt
            val += p["qty"] * (2 * p["avg_price"] - mark)
    return cash, round(val, 2)


def gross_exposure(marks: dict):
    """Summe der Positions-Notionale (Long + |Short|) = eingesetztes Brutto-Kapital."""
    g = 0.0
    for p in store.get_positions():
        mark = marks.get(p["symbol"], p["avg_price"])
        g += p["qty"] * mark
    return round(g, 2)


def open_long(symbol, qty, price, stop, target, risk_r, thesis, high_water):
    # Kauf auf Kredit erlaubt (Hebel) – die Kaufkraft-Grenze prüft die Engine.
    if qty <= 0:
        return False
    fill = _fill_price(price, "long", "BUY")
    cost = qty * fill
    fee = commission(cost)
    store.set_cash(store.get_cash() - cost - fee)
    store.add_fee(fee)
    store.upsert_position({
        "symbol": symbol, "side": "long", "qty": qty, "avg_price": fill,
        "stop": stop, "target": target, "risk_r": risk_r, "thesis": thesis,
        "opened_at": store._now(), "high_water": high_water,
    })
    store.add_trade(symbol, "BUY", "long", qty, fill, 0.0, thesis)
    store.log("TRADE", f"LONG {qty}x {symbol} @ {fill:.2f} | Stop {stop:.2f} Ziel {target:.2f} | Gebühr {fee:.2f} | {thesis}")
    return True


def open_short(symbol, qty, price, stop, target, risk_r, thesis, high_water):
    if qty <= 0:
        return False
    fill = _fill_price(price, "short", "SHORT")
    fee = commission(qty * fill)
    # Short: Erlös fließt zu (vereinfachtes Margin-Modell für Spielgeld), abzgl. Gebühr
    store.set_cash(store.get_cash() + qty * fill - fee)
    store.add_fee(fee)
    store.upsert_position({
        "symbol": symbol, "side": "short", "qty": qty, "avg_price": fill,
        "stop": stop, "target": target, "risk_r": risk_r, "thesis": thesis,
        "opened_at": store._now(), "high_water": high_water,
    })
    store.add_trade(symbol, "SHORT", "short", qty, fill, 0.0, thesis)
    store.log("TRADE", f"SHORT {qty}x {symbol} @ {fill:.2f} | Stop {stop:.2f} Ziel {target:.2f} | Gebühr {fee:.2f} | {thesis}")
    return True


def reduce_position(symbol, qty, price, reason):
    """Schließt qty Stück (Teil- oder Voll-Schließung). Bucht realisierten P&L NETTO nach Gebühren."""
    p = store.get_position(symbol)
    if not p:
        return 0.0
    qty = min(qty, p["qty"])
    open_fee = commission(qty * p["avg_price"])  # anteilige Einstiegsgebühr (schon bezahlt)
    if p["side"] == "long":
        fill = _fill_price(price, "long", "SELL")
        gross = qty * (fill - p["avg_price"])
        close_fee = commission(qty * fill)
        store.set_cash(store.get_cash() + qty * fill - close_fee)
        action = "SELL" if qty >= p["qty"] else "HALF_OUT"
    else:
        fill = _fill_price(price, "short", "COVER")
        gross = qty * (p["avg_price"] - fill)
        close_fee = commission(qty * fill)
        store.set_cash(store.get_cash() - qty * fill - close_fee)
        action = "COVER" if qty >= p["qty"] else "HALF_OUT"

    store.add_fee(close_fee)
    pnl = gross - open_fee - close_fee  # Netto-P&L nach Ein- und Ausstiegsgebühr
    remaining = round(p["qty"] - qty, 6)
    store.add_trade(symbol, action, p["side"], qty, fill, round(pnl, 2), reason)
    store.log("TRADE", f"{action} {qty}x {symbol} @ {fill:.2f} | P&L {pnl:+.2f} (netto) | Gebühr {close_fee:.2f} | {reason}")

    if remaining <= 1e-6:
        store.delete_position(symbol)
    else:
        p["qty"] = remaining
        store.upsert_position(p)
    return round(pnl, 2)


def press_winner(symbol, add_qty, price, new_stop):
    """Kauft zur bestehenden Gewinner-Position nach (Cohen: 'press winners')."""
    p = store.get_position(symbol)
    if not p or add_qty <= 0:
        return False
    if p["side"] == "long":
        fill = _fill_price(price, "long", "PRESS")
        cost = add_qty * fill
        fee = commission(cost)
        store.set_cash(store.get_cash() - cost - fee)
        new_qty = p["qty"] + add_qty
        p["avg_price"] = (p["avg_price"] * p["qty"] + fill * add_qty) / new_qty
        p["qty"] = new_qty
    else:
        fill = _fill_price(price, "short", "PRESS")
        fee = commission(add_qty * fill)
        store.set_cash(store.get_cash() + add_qty * fill - fee)
        new_qty = p["qty"] + add_qty
        p["avg_price"] = (p["avg_price"] * p["qty"] + fill * add_qty) / new_qty
        p["qty"] = new_qty
    store.add_fee(fee)
    p["stop"] = new_stop
    store.upsert_position(p)
    store.add_trade(symbol, "PRESS", p["side"], add_qty, fill, 0.0, "press winner")
    store.log("TRADE", f"PRESS +{add_qty}x {symbol} @ {fill:.2f} | Gebühr {fee:.2f} | neuer Stop {new_stop:.2f}")
    return True
