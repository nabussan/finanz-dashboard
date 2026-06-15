# W541 Rollout-Checkliste

Ziel: Drei LXCs auf dem W541-Proxmox installieren, Daten migrieren,
testen, dann live schalten. P53 bleibt danach als Dev-System.

**Reihenfolge ist bindend** — jede Phase setzt die vorherige voraus.

---

## Phase 0 — Vorbereitung

Bevor irgendein Skript läuft, müssen diese Punkte erfüllt sein:

- [ ] W541 Proxmox läuft, Shell-Zugriff als root vorhanden
- [ ] Debian-12-Template verfügbar (`pveam list local | grep debian-12`)
- [ ] IBKR Gateway läuft auf W541 (LXC 510), Port 4002 erreichbar
- [ ] GitHub SSH-Key auf dem Proxmox-Host vorhanden (`~/.ssh/id_ed25519`)
- [ ] Alle Credentials bereit (aus P53 `.env` abschreiben):

```bash
# Auf W541 Proxmox-Host vor den Installationsskripten setzen:
export LXC_ROOT_PW=...
export FINANZ_DB_PW=...              # neu vergeben, sicher aufbewahren
export DB_URL=postgresql://finanz:${FINANZ_DB_PW}@192.168.1.58/finanz
export TELEGRAM_BOT_TOKEN=...
export TELEGRAM_CHAT_ID=...
export FRED_API_KEY=...
export TWELVE_DATA_API_KEY=...
export IBKR_HOST=<W541-IBKR-LXC-IP>  # IP des IBKR-Gateway LXC auf W541
```

---

## Phase 1 — LXC 522: PostgreSQL

```bash
cd /pfad/zu/finanz-dashboard   # nach git clone oder rsync vom P53
bash infra/install_postgresql_lxc.sh
```

**Danach verifizieren:**

- [ ] LXC 522 läuft: `pct status 522`
- [ ] PostgreSQL erreichbar: `pct exec 522 -- psql -U finanz -h localhost finanz -c '\dt'`
- [ ] Alle Tabellen vorhanden (rsm_prices, signals, positions, ...)

**Datenmigration — P53 → LXC 522:**

```bash
# Auf P53: pg_dump erzeugen
pg_dump -U finanz finanz > /tmp/finanz_migration.sql

# Auf W541 Proxmox: Dump in LXC 522 kopieren und einspielen
scp christoph@192.168.1.113:/tmp/finanz_migration.sql /tmp/
pct push 522 /tmp/finanz_migration.sql /tmp/finanz_migration.sql
pct exec 522 -- su - postgres -c "psql finanz < /tmp/finanz_migration.sql"
pct exec 522 -- rm /tmp/finanz_migration.sql
```

- [ ] Datenmigration abgeschlossen
- [ ] Zeilenanzahl plausibel: `pct exec 522 -- su - postgres -c "psql finanz -c 'SELECT COUNT(*) FROM signals;'"`

**Backup-Test (vor Go-Live):**

```bash
# Restore-Prozedur einmal durchspielen:
pct exec 522 -- su - postgres -c "pg_dump finanz | gzip > /tmp/test_backup.sql.gz"
pct exec 522 -- su - postgres -c "createdb finanz_restore_test"
pct exec 522 -- su - postgres -c "zcat /tmp/test_backup.sql.gz | psql finanz_restore_test"
pct exec 522 -- su - postgres -c "psql finanz_restore_test -c 'SELECT COUNT(*) FROM signals;'"
pct exec 522 -- su - postgres -c "dropdb finanz_restore_test"
```

- [ ] Restore-Test erfolgreich

---

## Phase 2 — LXC 520: finance-auto (rsm-live)

```bash
cd /pfad/zu/rsm-live
bash infra/install_finance_auto_lxc.sh
```

**Danach verifizieren:**

- [ ] LXC 520 läuft: `pct status 520`
- [ ] Python-Imports OK: `pct exec 520 -- /opt/rsm-live/.venv/bin/python -c "import ib_insync, psycopg2; print('OK')"`
- [ ] DB-Verbindung: `pct exec 520 -- /opt/rsm-live/.venv/bin/python -c "import psycopg2, os; psycopg2.connect(os.environ['DB_URL']); print('DB OK')" --login`

**SQLite-Migration — P53 → LXC 520:**

```bash
# rsm_data.db vom P53 in den LXC kopieren
scp christoph@192.168.1.113:/home/christoph/Finanz/rsm-live/data/rsm_data.db /tmp/
pct push 520 /tmp/rsm_data.db /opt/rsm-live/data/rsm_data.db
pct exec 520 -- chown root:root /opt/rsm-live/data/rsm_data.db
```

- [ ] SQLite-Migration abgeschlossen
- [ ] Ticker-Anzahl plausibel: `pct exec 520 -- sqlite3 /opt/rsm-live/data/rsm_data.db "SELECT COUNT(*) FROM tickers;"`

**Skripte testen (ohne Live-IBKR, read-only):**

```bash
# check_w3 ohne Notify (kein Telegram-Alert)
pct exec 520 -- /opt/rsm-live/.venv/bin/python /opt/rsm-live/src/check_w3.py --skip-notify --dry-run
```

- [ ] check_w3 läuft durch ohne Fehler
- [ ] make_charts erzeugt HTML: `pct exec 520 -- /opt/rsm-live/.venv/bin/python /opt/rsm-live/src/make_charts.py`
- [ ] Keine kritischen Fehler im Log: `pct exec 520 -- tail -50 /opt/rsm-live/logs/rsm.log`

---

## Phase 3 — LXC 521: finanz-dashboard

```bash
cd /pfad/zu/finanz-dashboard
bash infra/install_dashboard_lxc.sh
```

**Danach verifizieren:**

- [ ] LXC 521 läuft: `pct status 521`
- [ ] Service aktiv: `pct exec 521 -- systemctl is-active dashboard`
- [ ] HTTP-Antwort: `curl -s -o /dev/null -w "%{http_code}" http://192.168.1.57:8080/`  → `200`

**pytest im LXC ausführen:**

```bash
pct exec 521 -- su - finanz -c "cd /opt/finanz-dashboard && .venv/bin/pytest tests/ -v"
```

- [ ] Alle Tests grün (oder bekannte Skips für leere Tabellen)

**Browser-Check (vom P53 oder Tailscale):**

- [ ] `/` — Übersicht lädt
- [ ] `/rsm` — RSM-Seite mit Signalen
- [ ] `/portfolios?broker=ibkr` — Portfolio-Seite (leer bis Go-Live OK)
- [ ] `/portfolio-charts` — Chart-iframe rendert
- [ ] `/watchlists` — Watchlists vorhanden
- [ ] `/micro` — Micro-Ranking mit RSM-Chart-Iframe (W/D/TV-Toggle, Tabelle mit allen Spalten)

---

## Go/No-Go Gate

Alle Punkte müssen erfüllt sein bevor Phase 4 beginnt:

- [ ] Phase 1–3 vollständig abgehakt
- [ ] Restore-Test aus Phase 1 erfolgreich
- [ ] pytest grün
- [ ] Dashboard im Browser bedienbar
- [ ] Daten in DB plausibel (Signale, Preise, Positionen)
- [ ] **Entscheidung: Go**

---

## Phase 4 — Go-Live

**P53 abschalten (zuerst):**

```bash
# Auf P53: Cronjobs deaktivieren
crontab -l > /tmp/crontab_backup_p53.txt   # Backup
crontab -r                                  # löschen
# Laufende Services stoppen
systemctl --user stop dashboard 2>/dev/null || true
```

- [ ] P53 Crons gestoppt
- [ ] P53 Dashboard-Service gestoppt

**W541 aktivieren:**

```bash
# LXC 520: Crons einschalten
pct exec 520 -- crontab /opt/rsm-live/infra/run_w3_cron.sh   # Cron aus Datei
# oder manuell prüfen:
pct exec 520 -- crontab -l
```

- [ ] W541 Crons aktiv
- [ ] IBKR_HOST in LXC 520 `.env` zeigt auf W541-IBKR-LXC (nicht P53)
- [ ] IBKR TrustedIPs aktualisiert (LXC 520 IP in IBKR Gateway config)

**Telegram-Alert Test:**

```bash
pct exec 520 -- /opt/rsm-live/.venv/bin/python -c "
from portfolio_alert import send_telegram
send_telegram('✅ W541 Go-Live: rsm-live aktiv')
"
```

- [ ] Telegram-Nachricht empfangen
- [ ] Erster EOD-Update-Lauf am nächsten Handelstag erfolgreich (Log prüfen)

---

## Backup & Restore — Prozeduren

### Nightly pg_dump (automatisch, LXC 522)
Cron läuft täglich 03:00, speichert nach `/var/backups/finanz/`.
Retention: 14 Tage. Manuell prüfen:
```bash
pct exec 522 -- ls -lh /var/backups/finanz/
```

### SQLite-Backup (manuell, LXC 520)
```bash
pct exec 520 -- sqlite3 /opt/rsm-live/data/rsm_data.db ".backup /var/backups/rsm_data_$(date +%Y%m%d).db"
```
SQLite hat keinen automatischen Backup-Cron — bei größeren OHLCV-Updates manuell ausführen.

### Restore PostgreSQL
```bash
pct exec 522 -- su - postgres -c \
  "zcat /var/backups/finanz/finanz_YYYYMMDD.sql.gz | psql finanz"
```

### Restore SQLite
```bash
pct push 520 /pfad/zur/backup.db /opt/rsm-live/data/rsm_data.db
```

---

## P53 bleibt Dev

Nach Go-Live:
- DB_URL in `/home/christoph/Finanz/.env` bleibt auf `localhost` (lokale PG-Instanz)
- Keine Crons, kein IBKR Live
- `git pull` in rsm-live + finanz-dashboard für Updates
- Änderungen: entwickeln auf P53 → push → auf W541 `git pull` + `systemctl restart`
