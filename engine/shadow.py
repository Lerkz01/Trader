"""
Schatten-Portfolios (Forward-Testing).

Ein `Book` ist ein vollständiges virtuelles Konto mit eigenem Params-Satz, das
jeden Zyklus auf denselben Live-Quotes wie der echte Bot mitläuft — aber rein
im Speicher, ohne den echten Paper-Account zu berühren. So testen Challenger
(und der Champion-Schatten als Vergleichsbasis) über Wochen *vorwärts* in
Echtzeit. Erst wenn ein Challenger den Champion lange & robust schlägt,
befördert der Analyst ihn (siehe analyst.py).
"""

import time

import config
from engine import strategy
from engine.broker import commission

SLIPPAGE_BPS = 5
_EQUITY_CAP = 1500  # max gespeicherte Equity-Punkte pro Buch (Free-Plan: RAM sparen)


def _fill(price, buy):
    slip = price * SLIPPAGE_BPS / 10000.0
    return price + slip if buy else price - slip


class Book:
    def __init__(self, name, params, role="challenger", kind="mutation"):
        self.name = name
        self.params = params
        self.role = role          # "champion" | "challenger"
        self.kind = kind          # "seed" | "mutation" | "ai"
        self.cash = config.START_CASH
        self.start_cash = config.START_CASH
        self.positions = {}       # symbol -> dict
        self.equity_hist = []     # [(ts, value)]
        self.peak = config.START_CASH
        self.max_dd_pct = 0.0
        self.trades_closed = 0
        self.wins = 0
        self.losses = 0
        self.gross_win = 0.0
        self.gross_loss = 0.0
        self.total_fees = 0.0
        self.created_at = time.time()
        self.day_count = 0
        self.last_day = None
        self.day_start_equity = config.START_CASH
        self.halted_today = False
        self.beat_streak = 0      # vom Analyst gepflegt

    # ---- Bewertung ----
    def equity(self, marks):
        val = self.cash
        for sym, p in self.positions.items():
            mark = marks.get(sym, p["avg_price"])
            if p["side"] == "long":
                val += p["qty"] * mark
            else:
                val += p["qty"] * (2 * p["avg_price"] - mark)
        return val

    def _catalyst(self, sym, q, base_catalysts):
        base = dict(base_catalysts.get(sym, {"has": False, "dir": 0, "detail": ""}))
        if self.params.use_momentum_catalyst and q:
            ch = q.get("change_pct", 0.0)
            if abs(ch) >= self.params.momentum_catalyst_pct:
                base["has"] = True
                mom = f"Momentum {ch:+.1f}%"
                base["detail"] = (base["detail"] + " · " + mom).strip(" ·") if base["detail"] else mom
                if base.get("dir", 0) == 0:
                    base["dir"] = 1 if ch > 0 else -1
        return base

    # ---- Order-Logik (in-memory) ----
    def _gross(self, marks):
        g = 0.0
        for sym, p in self.positions.items():
            g += p["qty"] * marks.get(sym, p["avg_price"])
        return g

    def _open(self, sym, side, qty, price, stop, target, rps, thesis):
        # Kauf auf Kredit erlaubt (Hebel) – Kaufkraft prüft step().
        if qty <= 0:
            return
        if side == "long":
            avg = _fill(price, True)
            fee = commission(qty * avg)
            self.cash -= qty * avg + fee
        else:
            avg = _fill(price, False)
            fee = commission(qty * avg)
            self.cash += qty * avg - fee
        self.total_fees += fee
        self.positions[sym] = {
            "side": side, "qty": qty, "avg_price": avg, "stop": stop,
            "target": target, "risk_r": rps, "thesis": thesis,
        }

    def _reduce(self, sym, qty, price, _reason):
        p = self.positions.get(sym)
        if not p:
            return
        qty = min(qty, p["qty"])
        open_fee = commission(qty * p["avg_price"])
        if p["side"] == "long":
            fill = _fill(price, False)
            gross = qty * (fill - p["avg_price"])
            close_fee = commission(qty * fill)
            self.cash += qty * fill - close_fee
        else:
            fill = _fill(price, True)
            gross = qty * (p["avg_price"] - fill)
            close_fee = commission(qty * fill)
            self.cash -= qty * fill + close_fee
        self.total_fees += close_fee
        pnl = gross - open_fee - close_fee  # netto nach Gebühren
        remaining = round(p["qty"] - qty, 6)
        # Statistik nur bei Voll-Schließung als ein Trade zählen
        if remaining <= 1e-6:
            self.trades_closed += 1
            if pnl >= 0:
                self.wins += 1
                self.gross_win += pnl
            else:
                self.losses += 1
                self.gross_loss += abs(pnl)
            del self.positions[sym]
        else:
            p["qty"] = remaining

    # ---- Ein Zyklus ----
    def step(self, active_symbols, quotes, hist_map, base_catalysts,
             market_trend, sector_trends, sector_of, day):
        P = self.params

        # Tageswechsel
        if day != self.last_day:
            if self.last_day is not None:
                self.day_count += 1
            self.last_day = day
            self.day_start_equity = self.equity({s: q["price"] for s, q in quotes.items()})
            self.halted_today = False

        marks = {s: q["price"] for s, q in quotes.items()}
        equity_now = self.equity(marks)

        # Daily-Loss-Limit
        if (self.day_start_equity > 0 and
                equity_now <= self.day_start_equity * (1 - P.daily_loss_limit_pct / 100.0)):
            self.halted_today = True

        # 1) Positionen managen
        for sym, p in list(self.positions.items()):
            q = quotes.get(sym)
            if not q:
                continue
            action, info = strategy.manage_position(p, q, P)
            if action == "exit":
                self._reduce(sym, p["qty"], q["price"], info.get("reason", ""))
            elif action == "half_out":
                self._reduce(sym, p["qty"] / 2.0, q["price"], info.get("reason", ""))
            elif action == "trail":
                p["stop"] = info["new_stop"]

        # 2) Einstiege (mit Hebel-Kaufkraftgrenze)
        if not self.halted_today:
            gross = self._gross(marks)
            buying_power = max(equity_now, 0) * P.leverage
            for sym in active_symbols:
                if len(self.positions) >= P.max_open_positions:
                    break
                if sym in self.positions:
                    continue
                q = quotes.get(sym)
                if not q:
                    continue
                cat = self._catalyst(sym, q, base_catalysts)
                sec_trend = sector_trends.get(sector_of(sym), "flat")
                plan = strategy.entry_decision(q, hist_map.get(sym), cat,
                                               market_trend, sec_trend, P)
                if not plan:
                    continue
                qty = strategy.size_position(equity_now, plan["price"],
                                             plan["risk_per_share"], P)
                room = buying_power - gross
                if room < plan["price"]:
                    continue
                qty = min(qty, int(room // plan["price"]))
                if qty < 1:
                    continue
                self._open(sym, plan["side"], qty, plan["price"], plan["stop"],
                           plan["target"], plan["risk_per_share"], plan["thesis"])
                gross += qty * plan["price"]

        # 3) Equity-Snapshot + Drawdown
        equity_now = self.equity(marks)
        self.peak = max(self.peak, equity_now)
        dd = 100 * (self.peak - equity_now) / self.peak if self.peak > 0 else 0.0
        self.max_dd_pct = max(self.max_dd_pct, dd)
        self.equity_hist.append((time.strftime("%Y-%m-%d %H:%M", time.gmtime()), round(equity_now, 2)))
        if len(self.equity_hist) > _EQUITY_CAP:
            self.equity_hist = self.equity_hist[-_EQUITY_CAP:]

    # ---- Kennzahlen ----
    def metrics(self):
        marks = {}
        equity = self.equity(marks)  # nutzt avg_price-Fallback für offene Pos.
        total_ret = 100 * (equity - self.start_cash) / self.start_cash
        pf = (self.gross_win / self.gross_loss) if self.gross_loss > 0 else (
            self.gross_win and 99.0 or 0.0)
        win_rate = 100 * self.wins / self.trades_closed if self.trades_closed else 0.0
        # Risiko-adjustierter Score (Calmar-artig)
        score = total_ret / max(self.max_dd_pct, 1.0)
        return {
            "name": self.name,
            "role": self.role,
            "kind": self.kind,
            "equity": round(equity, 2),
            "total_return_pct": round(total_ret, 2),
            "max_dd_pct": round(self.max_dd_pct, 2),
            "trades": self.trades_closed,
            "win_rate": round(win_rate, 1),
            "profit_factor": round(pf, 2),
            "score": round(score, 3),
            "fees": round(self.total_fees, 2),
            "days": self.day_count,
            "open_positions": len(self.positions),
            "beat_streak": self.beat_streak,
            "summary": self.params.short_summary(),
        }

    # ---- Serialisierung (für Persistenz) ----
    def to_dict(self):
        return {
            "name": self.name, "role": self.role, "kind": self.kind,
            "params": self.params.to_dict(), "cash": self.cash,
            "positions": self.positions, "equity_hist": self.equity_hist[-300:],
            "peak": self.peak, "max_dd_pct": self.max_dd_pct,
            "trades_closed": self.trades_closed, "wins": self.wins,
            "losses": self.losses, "gross_win": self.gross_win,
            "gross_loss": self.gross_loss, "total_fees": self.total_fees,
            "created_at": self.created_at,
            "day_count": self.day_count, "last_day": self.last_day,
            "day_start_equity": self.day_start_equity, "beat_streak": self.beat_streak,
        }

    @classmethod
    def from_dict(cls, d):
        from engine.params import Params
        b = cls(d["name"], Params.from_dict(d["params"]), d.get("role", "challenger"),
                d.get("kind", "mutation"))
        b.cash = d.get("cash", config.START_CASH)
        b.positions = d.get("positions", {})
        b.equity_hist = d.get("equity_hist", [])
        b.peak = d.get("peak", b.cash)
        b.max_dd_pct = d.get("max_dd_pct", 0.0)
        b.trades_closed = d.get("trades_closed", 0)
        b.wins = d.get("wins", 0)
        b.losses = d.get("losses", 0)
        b.gross_win = d.get("gross_win", 0.0)
        b.gross_loss = d.get("gross_loss", 0.0)
        b.total_fees = d.get("total_fees", 0.0)
        b.created_at = d.get("created_at", time.time())
        b.day_count = d.get("day_count", 0)
        b.last_day = d.get("last_day")
        b.day_start_equity = d.get("day_start_equity", b.cash)
        b.beat_streak = d.get("beat_streak", 0)
        return b
