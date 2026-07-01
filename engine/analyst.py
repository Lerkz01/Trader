"""
Der Analyst — die selbst-optimierende Schicht.

Hält den Champion-Schatten (gleiche Params wie der echte Live-Bot) plus mehrere
Challenger (mutierte Varianten + ggf. KI-Vorschläge). Alle laufen jeden Zyklus
auf denselben Live-Quotes mit (shadow.Book.step).

Täglich (Tageswechsel) bewertet der Analyst alle Bücher risikoadjustiert und
**befördert einen Challenger nur, wenn er die strengen Kriterien erfüllt**:
  - mind. PROMO_MIN_DAYS Handelstage gelaufen
  - mind. PROMO_MIN_TRADES abgeschlossene Trades
  - Score >= Champion-Score * (1 + PROMO_IMPROVEMENT)
  - und das an PROMO_STREAK_DAYS Bewertungstagen am Stück (beat_streak)

Wird befördert, übernimmt der echte Live-Bot ab dann die neuen Params
(params.save_champion). Die KI (ai_analyst) liefert höchstens Vorschläge —
sie müssen denselben Test bestehen wie jede Mutation.
"""

import json
import random

import config
from engine import params as P
from engine import store
from engine.shadow import Book

_champion = None          # Book
_challengers = []         # list[Book]
_last_eval_day = None
_last_ai_day = -999
_rng = random.Random(1337)
_cycle_n = 0
_PERSIST_EVERY = 3        # Schatten-Zustand alle N Zyklen sichern (Absturzschutz)


def _spawn_challengers(base_params, n, ai_book=None):
    out = []
    for i in range(n):
        mp = P.mutate(base_params, _rng, n_changes=_rng.choice([1, 2, 2, 3]))
        out.append(Book(f"C{i+1}", mp, role="challenger", kind="mutation"))
    if ai_book is not None and out:
        out[-1] = ai_book  # KI ersetzt einen Slot
    return out


def init():
    global _champion, _challengers, _last_eval_day, _last_ai_day
    raw = store.load_shadow()
    if raw:
        try:
            d = json.loads(raw)
            _champion = Book.from_dict(d["champion"])
            _challengers = [Book.from_dict(b) for b in d["challengers"]]
            _last_eval_day = d.get("last_eval_day")
            _last_ai_day = d.get("last_ai_day", -999)
            store.log("INFO", f"Analyst-Schatten geladen: 1 Champion + {len(_challengers)} Challenger.")
            return
        except Exception as ex:
            store.log("WARN", f"Schatten konnte nicht geladen werden ({ex}) – Neustart.")
    champ_params = P.load_champion()
    _champion = Book("Champion", champ_params, role="champion", kind="seed")
    _challengers = _spawn_challengers(champ_params, config.NUM_CHALLENGERS)
    store.log("INFO", f"Analyst gestartet: 1 Champion + {len(_challengers)} Challenger.")


def on_cycle(active_symbols, quotes, hist_map, base_catalysts,
             market_trend, sector_trends, sector_of, day):
    global _cycle_n
    if not config.ANALYST_ENABLED or _champion is None:
        return
    for book in [_champion] + _challengers:
        try:
            book.step(active_symbols, quotes, hist_map, base_catalysts,
                      market_trend, sector_trends, sector_of, day)
        except Exception as ex:
            store.log("WARN", f"Schatten-Buch {book.name} Fehler: {ex}")
    # Regelmäßig sichern, damit ein Absturz höchstens wenige Zyklen kostet
    _cycle_n += 1
    if _cycle_n % _PERSIST_EVERY == 0:
        persist()


def maybe_on_day(day):
    """Bei Tageswechsel: bewerten, beat_streaks pflegen, ggf. befördern."""
    global _last_eval_day, _last_ai_day
    if not config.ANALYST_ENABLED or _champion is None:
        return
    if day == _last_eval_day:
        return
    _last_eval_day = day

    champ_m = _champion.metrics()
    champ_score = champ_m["score"]

    # beat_streaks aktualisieren
    for c in _challengers:
        m = c.metrics()
        eligible = (m["days"] >= config.PROMO_MIN_DAYS and
                    m["trades"] >= config.PROMO_MIN_TRADES)
        beats = m["score"] >= champ_score * (1 + config.PROMO_IMPROVEMENT) and m["total_return_pct"] > champ_m["total_return_pct"]
        if eligible and beats:
            c.beat_streak += 1
        else:
            c.beat_streak = 0

    # Promotions-Kandidat: erfüllt Streak, höchster Score
    candidates = [c for c in _challengers if c.beat_streak >= config.PROMO_STREAK_DAYS]
    if candidates:
        winner = max(candidates, key=lambda c: c.metrics()["score"])
        _promote(winner, champ_score)
    else:
        # KI-Vorschlag in Kadenz, sonst schwächsten Challenger erneuern (Exploration)
        champ_day = champ_m["days"]
        ai_book = None
        if (config.ANTHROPIC_API_KEY and
                champ_day - _last_ai_day >= config.AI_PROPOSE_EVERY_DAYS):
            ai_book = _try_ai_proposal(champ_m)
            if ai_book is not None:
                _last_ai_day = champ_day
        _refresh_weakest(ai_book)

    persist()


def _promote(winner, old_score):
    global _champion, _challengers
    new_params = winner.params.copy()
    P.save_champion(new_params)
    wm = winner.metrics()
    store.add_promotion(winner.kind, old_score, wm["score"],
                        f"{winner.name} ({winner.kind}) befördert: "
                        f"Score {wm['score']} vs {round(old_score,3)}, "
                        f"{wm['trades']} Trades, {wm['days']} Tage, Streak {winner.beat_streak}",
                        json.dumps(new_params.to_dict()))
    store.log("PROMO", f"🏆 Neuer Champion ({winner.kind}): {new_params.short_summary()} "
                       f"| Score {wm['score']} schlägt {round(old_score,3)}")
    # Frischer Champion-Schatten + neue Challenger-Generation aus neuem Champion
    _champion = Book("Champion", new_params, role="champion", kind="seed")
    _challengers = _spawn_challengers(new_params, config.NUM_CHALLENGERS)


def _refresh_weakest(ai_book):
    """Ersetzt den schwächsten ausgereiften Challenger durch eine neue Variante."""
    global _challengers
    if not _challengers:
        return
    matured = [c for c in _challengers if c.metrics()["days"] >= config.PROMO_MIN_DAYS]
    if ai_book is not None:
        # KI-Buch ersetzt den schwächsten ausgereiften (oder den global schwächsten)
        pool = matured or _challengers
        worst = min(pool, key=lambda c: c.metrics()["score"])
        idx = _challengers.index(worst)
        _challengers[idx] = ai_book
        store.log("INFO", f"KI-Challenger eingesetzt: {ai_book.params.short_summary()}")
        return
    # ohne KI: nur erneuern, wenn ein ausgereifter Verlierer existiert
    losers = [c for c in matured if c.metrics()["score"] < 0 and c.beat_streak == 0]
    if losers:
        worst = min(losers, key=lambda c: c.metrics()["score"])
        idx = _challengers.index(worst)
        nm = P.mutate(_champion.params, _rng, n_changes=_rng.choice([2, 3]))
        _challengers[idx] = Book(worst.name, nm, role="challenger", kind="mutation")


def _try_ai_proposal(champ_m):
    try:
        from engine import ai_analyst
        proposed = ai_analyst.propose(_champion.params, champ_m,
                                      [c.metrics() for c in _challengers])
        if proposed is not None:
            return Book("AI", proposed, role="challenger", kind="ai")
    except Exception as ex:
        store.log("WARN", f"KI-Analyst Fehler: {ex}")
    return None


def persist():
    if _champion is None:
        return
    blob = {
        "champion": _champion.to_dict(),
        "challengers": [c.to_dict() for c in _challengers],
        "last_eval_day": _last_eval_day,
        "last_ai_day": _last_ai_day,
    }
    store.save_shadow(json.dumps(blob))


def state():
    if _champion is None:
        return {"enabled": config.ANALYST_ENABLED, "books": [], "promotions": []}
    books = [_champion.metrics()] + [c.metrics() for c in _challengers]
    return {
        "enabled": config.ANALYST_ENABLED,
        "ai_enabled": bool(config.ANTHROPIC_API_KEY),
        "criteria": {
            "min_days": config.PROMO_MIN_DAYS,
            "min_trades": config.PROMO_MIN_TRADES,
            "improvement_pct": round(config.PROMO_IMPROVEMENT * 100),
            "streak_days": config.PROMO_STREAK_DAYS,
        },
        "books": books,
        "promotions": store.get_promotions(20),
    }
