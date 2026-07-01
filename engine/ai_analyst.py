"""
KI-Analyst (optional, Anthropic API).

Bekommt die aktuelle Champion-Konfiguration samt Performance und die der
Challenger und schlägt EINEN verbesserten Parameter-/Regel-Satz vor. Der
Vorschlag ist nur ein Kandidat — er wird als Challenger im Schatten getestet
und muss dieselben strengen Kriterien erfüllen wie jede Mutation, bevor er je
live geht. Die KI kann den echten Bot also nie direkt verstellen.

Nur aktiv, wenn ANTHROPIC_API_KEY gesetzt ist. Modell via ANALYST_MODEL
(Standard: claude-opus-4-8).
"""

import json

import config
from engine import store
from engine.params import MUTABLE, Params, clamp


def _schema():
    props = {}
    for field, spec in MUTABLE.items():
        if spec[0] == "bool":
            props[field] = {"type": "boolean"}
        elif spec[0] == "int":
            props[field] = {"type": "integer"}
        else:
            props[field] = {"type": "number"}
    return {
        "type": "object",
        "properties": props,
        "required": list(MUTABLE.keys()),
        "additionalProperties": False,
    }


def _bounds_text():
    lines = []
    for field, spec in MUTABLE.items():
        if spec[0] == "bool":
            lines.append(f"- {field}: bool")
        else:
            lines.append(f"- {field}: {spec[0]} in [{spec[1]}, {spec[2]}]")
    return "\n".join(lines)


SYSTEM = (
    "Du bist ein quantitativer Trading-Analyst. Du betreust einen regelbasierten "
    "Daytrading-Bot im Stil von Steven Cohen (Katalysator + Chart-Timing + striktes "
    "Risikomanagement) auf US-Aktien, der mit Spielgeld auf Live-Daten simuliert. "
    "Deine Aufgabe: anhand der bisherigen Performance EINE konkrete, plausibel "
    "bessere Konfiguration vorschlagen. Bleibe Cohen-treu: kleine Verluste, große "
    "Gewinner, Konzentration, Survival vor Trefferquote. Vermeide Overfitting — "
    "ändere nur wenige Parameter gezielt. Halte ALLE Werte in den genannten Grenzen."
)


def propose(champion_params: Params, champ_metrics: dict, challenger_metrics: list):
    if not config.ANTHROPIC_API_KEY:
        return None
    try:
        import anthropic
    except ImportError:
        store.log("WARN", "anthropic-Paket fehlt – KI-Analyst inaktiv.")
        return None

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    payload = {
        "current_champion": {
            "params": champion_params.to_dict(),
            "performance": champ_metrics,
        },
        "challengers": challenger_metrics,
        "parameter_bounds": "siehe Systemkontext",
    }
    user = (
        "Aktuelle Konfiguration und Performance (Equity-Rendite %, max Drawdown %, "
        "Trefferquote %, Profit-Faktor, risikoadjustierter Score):\n\n"
        f"{json.dumps(payload, indent=2, ensure_ascii=False)}\n\n"
        "Erlaubte Felder und Grenzen:\n" + _bounds_text() + "\n\n"
        "Schlage genau EINE verbesserte Konfiguration vor. Gib NUR das JSON-Objekt "
        "mit allen Feldern zurück (keine Erklärung im JSON)."
    )

    try:
        resp = client.messages.create(
            model=config.ANALYST_MODEL,
            max_tokens=1500,
            system=SYSTEM,
            output_config={"format": {"type": "json_schema", "schema": _schema()}},
            messages=[{"role": "user", "content": user}],
        )
    except Exception as ex:
        store.log("WARN", f"KI-Analyst API-Fehler: {ex}")
        return None

    text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), None)
    if not text:
        return None
    try:
        proposed = clamp(Params.from_dict(json.loads(text)))
    except Exception as ex:
        store.log("WARN", f"KI-Antwort nicht verwertbar: {ex}")
        return None

    if proposed.to_dict() == champion_params.to_dict():
        return None  # kein echter Vorschlag
    store.log("INFO", f"KI schlägt vor: {proposed.short_summary()}")
    return proposed
