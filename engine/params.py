"""
Parameter-Modell der Strategie.

Ein `Params`-Objekt bündelt ALLES, was der Analyst verändern darf — sowohl
numerische Werte (Risiko, Stops, MAs, Schwellen) als auch Signal-Regeln
(Schalter wie allow_short, require_catalyst, allow_dip ...). Champion und
Challenger unterscheiden sich nur durch ihr Params-Objekt; der Strategie-Code
ist identisch. So bleibt jede Variante fair vergleichbar.

MUTABLE definiert pro Feld die Mutations-Grenzen (Typ, min, max, Schrittweite).
Bool-Felder werden mit kleiner Wahrscheinlichkeit gekippt.
"""

import json
import random
from dataclasses import asdict, dataclass

import config


@dataclass
class Params:
    # --- Risiko & Sizing ---
    risk_per_trade_pct: float = config.RISK_PER_TRADE_PCT
    max_position_pct: float = config.MAX_POSITION_PCT
    max_open_positions: int = config.MAX_OPEN_POSITIONS
    daily_loss_limit_pct: float = config.DAILY_LOSS_LIMIT_PCT
    leverage: float = config.MAX_LEVERAGE
    # --- Stop / Ziel ---
    atr_stop_mult: float = config.ATR_STOP_MULT
    fallback_stop_pct: float = config.FALLBACK_STOP_PCT
    target_r_multiple: float = config.TARGET_R_MULTIPLE
    half_out_trigger_r: float = config.HALF_OUT_TRIGGER_R
    press_winner_at_r: float = config.PRESS_WINNER_AT_R
    # --- Indikatoren ---
    trend_ma_days: int = config.TREND_MA_DAYS
    fast_ma_days: int = config.FAST_MA_DAYS
    atr_days: int = config.ATR_DAYS
    # --- Katalysator/Chart-Schwellen ---
    momentum_catalyst_pct: float = 3.0
    entry_long_change_min: float = -0.5
    entry_dip_change_min: float = 1.0
    entry_dip_ma_frac: float = 0.98
    entry_short_change_max: float = 0.5
    # --- Signal-Regeln (Schalter) ---
    allow_long: bool = True
    allow_short: bool = True
    require_catalyst: bool = True
    use_momentum_catalyst: bool = True
    require_market_gate: bool = True
    require_sector_gate: bool = True
    allow_breakout: bool = True
    allow_dip: bool = True

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        fields = {f for f in cls().__dict__}
        return cls(**{k: v for k, v in (d or {}).items() if k in fields})

    def copy(self):
        return Params.from_dict(self.to_dict())

    def short_summary(self):
        """Kompakte, menschenlesbare Zusammenfassung fürs Dashboard/Logs."""
        rules = []
        if not self.allow_short: rules.append("nur Long")
        if not self.allow_long: rules.append("nur Short")
        if not self.require_catalyst: rules.append("ohne Katalysator")
        if not self.require_market_gate: rules.append("ohne Markt-Gate")
        if not self.require_sector_gate: rules.append("ohne Sektor-Gate")
        if not self.allow_dip: rules.append("kein Dip-Buy")
        if not self.allow_breakout: rules.append("kein Breakout")
        base = (f"Risk {self.risk_per_trade_pct:.2f}% · Hebel {self.leverage:.1f}x · "
                f"Stop {self.atr_stop_mult:.1f}ATR · Ziel {self.target_r_multiple:.1f}R · "
                f"maxPos {self.max_open_positions}")
        return base + (" · " + ", ".join(rules) if rules else "")


# Feld -> (typ, min, max, schritt). Nur diese Felder mutiert der Analyst.
MUTABLE = {
    "risk_per_trade_pct":    ("float", 0.25, 2.5, 0.25),
    "max_position_pct":      ("float", 5.0, 30.0, 2.5),
    "max_open_positions":    ("int", 3, 10, 1),
    "daily_loss_limit_pct":  ("float", 1.5, 6.0, 0.5),
    "leverage":              ("float", 1.0, 4.0, 0.5),
    "atr_stop_mult":         ("float", 0.8, 3.0, 0.1),
    "fallback_stop_pct":     ("float", 2.0, 8.0, 0.5),
    "target_r_multiple":     ("float", 1.5, 4.0, 0.25),
    "half_out_trigger_r":    ("float", 0.3, 1.0, 0.1),
    "press_winner_at_r":     ("float", 0.5, 2.0, 0.25),
    "trend_ma_days":         ("int", 20, 100, 5),
    "fast_ma_days":          ("int", 5, 20, 1),
    "atr_days":              ("int", 7, 21, 1),
    "momentum_catalyst_pct": ("float", 1.5, 6.0, 0.5),
    "entry_long_change_min": ("float", -2.0, 0.5, 0.25),
    "entry_dip_change_min":  ("float", 0.0, 3.0, 0.25),
    "entry_dip_ma_frac":     ("float", 0.95, 1.0, 0.005),
    "entry_short_change_max":("float", -0.5, 1.5, 0.25),
    # Bool-Regeln
    "allow_long":            ("bool",),
    "allow_short":           ("bool",),
    "require_catalyst":      ("bool",),
    "use_momentum_catalyst": ("bool",),
    "require_market_gate":   ("bool",),
    "require_sector_gate":   ("bool",),
    "allow_breakout":        ("bool",),
    "allow_dip":             ("bool",),
}


def clamp(p: "Params") -> "Params":
    """Hält alle Felder in ihren Grenzen und repariert unsinnige Kombinationen."""
    d = p.to_dict()
    for field, spec in MUTABLE.items():
        if spec[0] == "float":
            _, lo, hi, _ = spec
            d[field] = round(min(max(float(d[field]), lo), hi), 4)
        elif spec[0] == "int":
            _, lo, hi, _ = spec
            d[field] = int(min(max(int(round(d[field])), lo), hi))
    # Mindestens eine Handelsrichtung muss erlaubt sein
    if not d["allow_long"] and not d["allow_short"]:
        d["allow_long"] = True
    # Mindestens ein Long-Einstiegsmuster
    if not d["allow_breakout"] and not d["allow_dip"]:
        d["allow_breakout"] = True
    return Params.from_dict(d)


def mutate(p: "Params", rng: random.Random, n_changes=2, bool_flip_prob=0.5) -> "Params":
    """Erzeugt eine mutierte Kopie: ändert n_changes zufällige Felder."""
    d = p.to_dict()
    fields = list(MUTABLE.keys())
    for field in rng.sample(fields, min(n_changes, len(fields))):
        spec = MUTABLE[field]
        if spec[0] == "bool":
            if rng.random() < bool_flip_prob:
                d[field] = not d[field]
        elif spec[0] == "float":
            _, lo, hi, step = spec
            d[field] = round(d[field] + rng.choice([-1, 1]) * step, 4)
        elif spec[0] == "int":
            _, lo, hi, step = spec
            d[field] = int(d[field] + rng.choice([-1, 1]) * step)
    return clamp(Params.from_dict(d))


# ---- Champion-Persistenz (in der state-Tabelle als JSON) -------------------

def load_champion():
    from engine import store
    raw = store.get_state("champion_params")
    if raw:
        try:
            return Params.from_dict(json.loads(raw))
        except Exception:
            pass
    return Params()  # Default = Cohen-treue Konfiguration aus config.py


def save_champion(p: "Params"):
    from engine import store
    store.set_state("champion_params", json.dumps(p.to_dict()))
