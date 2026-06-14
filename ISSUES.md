# Offene Issues

## [M-OPEN-01] RSM Gesamt — Signalfarben in Tabellenzeilen

**Status:** Offen  
**Priorität:** Niedrig (kosmetisch)  
**Seite:** `/rsm`

### Problem

Die Zeilen der RSM-Gesamt-Tabelle sollen je nach Signal eingefärbt werden
(BUY = grün, SELL = rot, HOLD = blau), analog zu den Watchlist-Ticker-Items.
Die Farben erscheinen nicht, obwohl die CSS-Klassen korrekt gesetzt werden.
Ursache unklar.

### Fehlgeschlagene Ansätze

1. **CSS-Klasse auf `<tr>` (`.rsm-sell { background-color: … }`)** — Browser
   rendert `tr`-Hintergrund nicht, wenn `td`-Zellen einen eigenen (transparenten)
   Hintergrund haben. CSS-Table-Rendering-Modell: `td` sitzt über `tr`.

2. **CSS Custom Property `--row-bg` auf `<tr>`, Regel `.signal-row td { background: var(--row-bg, transparent) }`** —
   Property wird nicht an `td` vererbt, weil `background` kein inherited property
   ist.

3. **Inline `style="background-color:…"` direkt auf jedem `<td>` per Jinja2** —
   Scheinbar nicht wirksam (Server möglichweise nicht neu gestartet zum
   Zeitpunkt des Tests; nicht abschließend verifiziert).

4. **Embedded `<style>` im Template mit `.rsm-sell td { … !important }`** —
   Konfirmiert sichtbar bei 40 % Deckkraft + gelbem Debug-Text (Ctrl+Shift+R).
   Bei Reduktion auf 30 % / 20 % nicht mehr wahrnehmbar. Nach weiteren
   Server-Neustarts komplett verschwunden — Ursache unklar (Jinja2-Cache?
   Browser-Cache? Konflikt mit externer CSS?).

5. **Signal-Farben in externe `style.css` verlagert, embedded `<style>` entfernt,
   Cache auf `?v=4` gebumpt** (aktueller Stand) — Keine Änderung sichtbar.
   Der externe CSS-Konflikt `.signal-row td { background: var(--row-bg, transparent) }`
   wurde entfernt. Trotzdem keine Farben.

### Hypothesen für nächste Session

- Jinja2 liefert trotz `--reload`-Flag altes Template aus (In-Memory-Cache).
  → Diagnose: `curl -s http://localhost:8080/rsm | grep rsm-sell` prüfen, ob
    die Klassen im gerenderten HTML vorhanden sind.
- `border-collapse: collapse` auf `.rsm-table` erzeugt ein separates Stacking-
  Context-Verhalten für `td`-Backgrounds.
- Anderer noch unbekannter CSS-Konflikt in `style.css`.

### Workaround

Watchlist-Ticker-Items verwenden `div`-Elemente (keine Tabelle) — dort
funktionieren Hintergrundfarben zuverlässig (`.ticker-item--sell` etc.).
Falls das Problem hartnäckig bleibt: RSM-Gesamt auf `div`-basiertes Layout
umstellen statt `<table>`.
