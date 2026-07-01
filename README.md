# Cohen-Bot 🧠📈

Ein **Paper-Trading-Bot im Stil von Steven A. Cohen** (SAC Capital / Point72).
Zieht **echte Live-Kurse**, handelt aber mit **Spielgeld** – simuliert alles 24/7
und zeigt jeden Trade in einem **HTML-Dashboard**.

> Spielgeld, echte Zahlen. **Kein Anlageratschlag.** Reines Lern-/Simulationsprojekt.

---

## Was der Bot macht (Cohens Methode als Code)

| Cohen-Prinzip | Umsetzung |
|---|---|
| Markt 40 % / Sektor 30 % / Aktie 30 % | Entry-Gate: SPY- & Sektor-ETF-Trend müssen passen |
| Kein Katalysator → kein Trade | Earnings-Kalender, News, Analysten-Trends, Momentum (Finnhub) |
| Chart fürs Timing | Gleitende Durchschnitte, ATR, Breakout / Dip-Stabilisierung |
| These · Zeitrahmen · Invalidierung · Größe | Pro Position gespeichert; Stop = Invalidierung |
| Kleine Verluste, große Gewinner | Risiko 1 %/Trade, Ziel ≥ 2,5R |
| „Fokus auf Verlierer“ + Halbierungs-Regel | Bei Schwäche vor dem Stop: halbe Position raus |
| Gewinner kontrolliert nachkaufen | Stop auf Breakeven, Trailing |
| Konzentration ~10 % | max. 20 % Notional/Position, max. 6 Positionen |
| Hebel (Cohen/SAC ~4×) | Brutto-Exposure bis **Hebel × Equity** (Standard 2×, Analyst tunt 1–4×) |
| Survival > Trefferquote | Daily-Loss-Limit 3 % → Handelsstopp für den Tag |

**Persistenz & Absturzsicherheit:** Alles (Positionen, Trades, Cash, Equity, Log,
Analyst-Schattenbücher, Champion-Params) liegt laufend in SQLite und wird beim
Start automatisch weitergeführt — der Bot macht nach einem Absturz/Neustart genau
dort weiter, wo er aufgehört hat. Schattenbücher werden alle paar Zyklen gesichert,
zusätzlich läuft ~stündlich ein konsistentes DB-Backup (`data/cohen_bot.backup.sqlite3`).
Der Datenpfad ist via Env `DATA_DIR` auslagerbar (z.B. auf eine **Render-Disk**,
damit die Historie auch Redeploys überlebt — siehe `render.yaml`).

**Free-Plan (ohne Disk):** Der Zustand überlebt jeden *Absturz/Neustart*, wird
aber bei einem *Redeploy* (neuer `git push`) zurückgesetzt. Dafür gibt es im
Dashboard zwei Buttons: **💾 Snapshot speichern** (lädt den kompletten Zustand
als JSON) und **📥 Snapshot laden** (spielt ihn in einen frisch gestarteten Bot
ein → er macht dort weiter). Workflow: vor dem Redeploy Snapshot speichern,
nach dem Redeploy laden. Der Import wird abgelehnt, solange der Bot schon Daten
hat (kein versehentliches Überschreiben). Endpoints: `GET /api/export`,
`POST /api/import`.

Alle Parameter stehen in [`config.py`](config.py) und sind frei anpassbar.

---

## 🔬 Selbst-optimierender Analyst (Champion / Challenger)

Hinter dem Bot läuft ein **Analyst**, der die Methode beobachtet und automatisch
verbessert — aber **nur, wenn eine Variante über längere Zeit nachweislich besser
performt**. Prinzip: **die KI schlägt vor, die Statistik entscheidet.**

- **Champion** = die Konfiguration, mit der der echte Paper-Bot live handelt.
- **Challenger** = mutierte Varianten (Parameter *und* Signal-Regeln) + optional
  ein **KI-Vorschlag** (Claude). Sie laufen als **Schatten-Portfolios** parallel
  auf denselben Live-Quotes mit — mit eigenem virtuellem Geld, ohne den echten
  Account zu berühren (Forward-Testing).
- **Beförderung (streng):** Ein Challenger ersetzt den Champion nur, wenn er
  - ≥ **20 Handelstage** im Schatten lief,
  - ≥ **30 abgeschlossene Trades** hat,
  - den Champion risikoadjustiert (Score = Rendite / Max-Drawdown) um ≥ **15 %**
    schlägt — und das an **3 Bewertungstagen am Stück**.

Die KI (`engine/ai_analyst.py`) liefert höchstens Kandidaten; sie muss denselben
Test bestehen wie jede Mutation und kann den echten Bot **nie direkt verstellen**.

**KI aktivieren (optional):** `ANTHROPIC_API_KEY` in `.env` / Render-Env setzen
(Key: <https://console.anthropic.com/>). Modell via `ANALYST_MODEL` (Standard
`claude-opus-4-8`; für weniger Kosten z.B. `claude-haiku-4-5`). Ohne Key läuft
nur der statistische (evolutionäre) Analyst — voll funktionsfähig und kostenlos.

Alles ist im Dashboard unter **„Analyst-Labor"** einsehbar: jede Variante mit
Rendite, Drawdown, Trefferquote, Score, Laufzeit, Beförderungs-Streak sowie die
Promotions-Historie. Kriterien stehen in [`config.py`](config.py) (`PROMO_*`).

---

## Schnellstart (lokal)

1. **Python 3.11** installieren.
2. Abhängigkeiten:
   ```bash
   pip install -r requirements.txt
   ```
3. **Finnhub-Key** kostenlos holen: <https://finnhub.io/register>
4. `.env.example` zu `.env` kopieren und Key eintragen:
   ```
   FINNHUB_API_KEY=dein_key
   START_CASH=10000
   ```
5. Starten:
   ```bash
   python app.py
   ```
6. Dashboard öffnen: <http://localhost:8000>

---

## Deployment auf Render.com (24/7)

1. Projekt zu einem **GitHub-Repo** pushen.
2. In Render: **New + → Blueprint → Repo wählen** (nutzt `render.yaml`).
3. Unter **Environment** den `FINNHUB_API_KEY` setzen (geheim).
4. Deploy abwarten → du bekommst eine URL wie `https://cohen-bot.onrender.com`.

### 24/7 wach halten mit cron-job.org
Render-Free schläft nach 15 Min ohne Traffic ein. Damit der Bot durchläuft:

1. Auf <https://cron-job.org> kostenlos anmelden.
2. Neuen Cronjob anlegen:
   - **URL:** `https://DEINE-RENDER-URL.onrender.com/ping`
   - **Intervall:** alle **5 Minuten**
3. Speichern. Der Ping hält den Service wach → Engine läuft rund um die Uhr.

> **Hinweis Datenpersistenz:** Auf dem Free-Tier ist das Dateisystem ephemer –
> bei einem *Redeploy* wird die SQLite-Historie zurückgesetzt (laufender Betrieb
> bleibt erhalten). Für dauerhafte Historie später auf **Render-PostgreSQL**
> umstellen (siehe `engine/store.py`).

---

## Architektur

```
app.py              FastAPI: Dashboard, JSON-API, /ping (cron-job.org), startet Engine
config.py           Alle Strategie-/Risiko-Parameter
engine/
  engine.py         Hauptschleife: Daten → Strategie → Broker, Marktzeiten, Risiko
  strategy.py       Cohen-Regeln (parametrierbar): Einstieg, Sizing, Management
  params.py         Parameter-Modell (Werte + Signal-Regeln) + Mutation/Grenzen
  broker.py         Paper-Broker: simulierte Fills, Cash, P&L (Long & Short)
  data.py           Finnhub (Quotes/Katalysatoren) + yfinance (Tages-Charts)
  universe.py       Handelsuniversum (liquide S&P-Namen + Sektoren)
  shadow.py         Schatten-Portfolios (Forward-Testing der Challenger)
  analyst.py        Champion/Challenger-Logik: bewerten, mutieren, befördern
  ai_analyst.py     Optionaler KI-Analyst (Anthropic) — schlägt Challenger vor
  store.py          SQLite-Persistenz (Positionen, Trades, Equity, Schatten, …)
static/             HTML/CSS/JS-Dashboard (Chart.js via CDN)
```

## Endpoints

| Pfad | Zweck |
|---|---|
| `/` | Dashboard |
| `/ping` | Keep-alive für cron-job.org |
| `/health` | Status-Check |
| `/api/state` | KPIs (Equity, P&L, Trefferquote …) |
| `/api/positions` | offene Positionen inkl. Live-P&L |
| `/api/trades` | Trade-Historie |
| `/api/equity` | Equity-Kurve |
| `/api/catalysts` | heutige Katalysatoren + Watchlist |
| `/api/sectors` | Markt-/Sektor-Trends |
| `/api/analyst` | Champion/Challenger-Stände + Promotions |
| `/api/logs` | Engine-Log |

---

## Disclaimer
Reines Simulations- und Lernprojekt. Keine echten Order, keine Anlageberatung.
Vergangene (simulierte) Performance sagt nichts über die Zukunft.
