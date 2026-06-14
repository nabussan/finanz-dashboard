#!/usr/bin/env bash
# =============================================================================
# PostgreSQL LXC Installation Script (LXC 522)
# =============================================================================
# Zweck: Frischen Debian-12-LXC mit PostgreSQL aufsetzen.
#        Beide App-LXCs (520 finance-auto, 521 finanz-dashboard) verbinden sich
#        von ihren IPs aus.
#
# Ausführen: Als root auf dem Proxmox-Host W541:
#
#   export LXC_ROOT_PW=<passwort>
#   export FINANZ_DB_PW=<db-passwort>          # wird für User "finanz" gesetzt
#   bash infra/install_postgresql_lxc.sh
#
# Danach: DB_URL für LXC 520 + 521 setzen:
#   postgresql://finanz:<FINANZ_DB_PW>@192.168.1.58/finanz
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------
LXC_ID="${LXC_ID:-522}"
LXC_HOSTNAME="${LXC_HOSTNAME:-finance-db}"
LXC_IP="${LXC_IP:-192.168.1.58}"
LXC_GW="${LXC_GW:-192.168.1.2}"
LXC_BRIDGE="${LXC_BRIDGE:-vmbr0}"
LXC_STORAGE="${LXC_STORAGE:-local-lvm}"
LXC_DISK_GB="${LXC_DISK_GB:-20}"   # mehr Platz für Zeitreihendaten
LXC_RAM_MB="${LXC_RAM_MB:-512}"
LXC_CORES="${LXC_CORES:-1}"

PG_VERSION="${PG_VERSION:-15}"
FINANZ_DB="finanz"
FINANZ_USER="finanz"

# IPs der App-LXCs, die Verbindungen aufbauen dürfen
CLIENT_LXC_520="${CLIENT_LXC_520:-192.168.1.56}"   # finance-auto (rsm-live)
CLIENT_LXC_521="${CLIENT_LXC_521:-192.168.1.57}"   # finanz-dashboard

: "${LXC_ROOT_PW:?Fehler: LXC_ROOT_PW muss gesetzt sein}"
: "${FINANZ_DB_PW:?Fehler: FINANZ_DB_PW muss gesetzt sein}"

# ---------------------------------------------------------------------------
log() { echo "[$(date '+%H:%M:%S')] $*"; }
lxc() { pct exec "$LXC_ID" -- bash -c "$1"; }
lxc_pg() { pct exec "$LXC_ID" -- su - postgres -c "$1"; }
# ---------------------------------------------------------------------------

log "=== Phase 1: Template & LXC erstellen ==="

TEMPLATE_NAME="debian-12-standard_12.7-1_amd64.tar.zst"
if ! pveam list local 2>/dev/null | grep -q "debian-12-standard_12.7"; then
    log "Template herunterladen..."
    pveam update
    pveam download local "$TEMPLATE_NAME"
fi

if pct status "$LXC_ID" &>/dev/null; then
    log "LXC $LXC_ID existiert bereits — überspringe Erstellung."
else
    pct create "$LXC_ID" "local:vztmpl/${TEMPLATE_NAME}" \
        --hostname  "$LXC_HOSTNAME" \
        --rootfs    "${LXC_STORAGE}:${LXC_DISK_GB}" \
        --memory    "$LXC_RAM_MB" \
        --cores     "$LXC_CORES" \
        --net0      "name=eth0,bridge=${LXC_BRIDGE},ip=${LXC_IP}/24,gw=${LXC_GW}" \
        --password  "$LXC_ROOT_PW" \
        --unprivileged 1 \
        --features  nesting=1
    pct start "$LXC_ID"
    sleep 8
fi

log "=== Phase 2: PostgreSQL installieren ==="

lxc "apt-get update -qq && apt-get install -y --no-install-recommends \
    postgresql-${PG_VERSION} postgresql-client-${PG_VERSION} curl ca-certificates"

log "=== Phase 3: postgresql.conf — auf LAN-IP lauschen ==="

PG_CONF="/var/lib/lxc/${LXC_ID}/rootfs/etc/postgresql/${PG_VERSION}/main/postgresql.conf"
# listen_addresses auf alle Interfaces setzen (LAN-intern, kein öffentliches Netz)
sed -i "s/#listen_addresses = 'localhost'/listen_addresses = '*'/" "$PG_CONF"

log "=== Phase 4: pg_hba.conf — App-LXCs freischalten ==="

PG_HBA="/var/lib/lxc/${LXC_ID}/rootfs/etc/postgresql/${PG_VERSION}/main/pg_hba.conf"
cat >> "$PG_HBA" <<EOF

# Finanz App-LXCs
host  ${FINANZ_DB}  ${FINANZ_USER}  ${CLIENT_LXC_520}/32  scram-sha-256
host  ${FINANZ_DB}  ${FINANZ_USER}  ${CLIENT_LXC_521}/32  scram-sha-256
EOF

lxc "systemctl restart postgresql"
sleep 3

log "=== Phase 5: DB-User + Datenbank anlegen ==="

lxc_pg "psql -c \"CREATE USER ${FINANZ_USER} WITH PASSWORD '${FINANZ_DB_PW}';\" 2>/dev/null || true"
lxc_pg "createdb ${FINANZ_DB} --owner=${FINANZ_USER} 2>/dev/null || true"

log "=== Phase 6: Schema einspielen ==="

# schema.sql aus dem Repo-Verzeichnis (infra/) in den LXC kopieren
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCHEMA_FILE="${SCRIPT_DIR}/schema.sql"

if [[ ! -f "$SCHEMA_FILE" ]]; then
    echo "FEHLER: $SCHEMA_FILE nicht gefunden. Skript aus dem finanz-dashboard/infra/-Verzeichnis ausführen." >&2
    exit 1
fi

pct push "$LXC_ID" "$SCHEMA_FILE" /tmp/schema.sql
lxc_pg "psql -d ${FINANZ_DB} -f /tmp/schema.sql"
lxc "rm /tmp/schema.sql"

# Tabellenberechtigungen für finanz-User sicherstellen
lxc_pg "psql -d ${FINANZ_DB} -c 'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO ${FINANZ_USER};'"
lxc_pg "psql -d ${FINANZ_DB} -c 'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO ${FINANZ_USER};'"

log "=== Phase 7: Smoke-Test ==="

lxc_pg "psql -d ${FINANZ_DB} -c '\dt'" | grep -q "signals" && log "Schema OK — Tabellen vorhanden."

log "=== Phase 8: nightly pg_dump Cronjob ==="

# Backup nach /var/backups/finanz/ — Dropbox-Sync muss extern eingerichtet werden
lxc "mkdir -p /var/backups/finanz"
cat >> "/var/lib/lxc/${LXC_ID}/rootfs/etc/cron.d/finanz-backup" <<EOF
# Nightly pg_dump um 03:00
0 3 * * * postgres pg_dump ${FINANZ_DB} | gzip > /var/backups/finanz/finanz_\$(date +\%Y\%m\%d).sql.gz && find /var/backups/finanz/ -name '*.sql.gz' -mtime +14 -delete
EOF

log "=== Installation abgeschlossen ==="

cat <<SUMMARY

╔══════════════════════════════════════════════════════════════════╗
║  PostgreSQL LXC ${LXC_ID} bereit                                     ║
║                                                                  ║
║  Host:    ${LXC_IP}:5432                                   ║
║  DB:      ${FINANZ_DB}                                              ║
║  User:    ${FINANZ_USER}                                             ║
║                                                                  ║
║  DB_URL für LXC 520 + 521 .env:                                  ║
║  postgresql://${FINANZ_USER}:<pw>@${LXC_IP}/${FINANZ_DB}         ║
║                                                                  ║
║  Backup:  /var/backups/finanz/ (täglich 03:00, 14 Tage)         ║
║  TODO:    Dropbox-Sync für Backups einrichten                    ║
╚══════════════════════════════════════════════════════════════════╝

SUMMARY
