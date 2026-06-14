#!/usr/bin/env bash
# =============================================================================
# Finanz-Dashboard LXC Installation Script
# =============================================================================
# Zweck: Frischen Debian-12-LXC auf dem W541-Proxmox aufsetzen mit
#        finanz-dashboard (FastAPI + uvicorn) als systemd-Service.
#
# PostgreSQL läuft im LXC 520 (finance-auto) oder auf dem W541-Host.
# Dieses LXC braucht KEINE IBKR-Verbindung und KEINE Cronjobs.
#
# Ausführen: Als root auf dem Proxmox-Host W541, im rsm-live- oder
#            finanz-dashboard-Verzeichnis:
#
#   export LXC_ROOT_PW=<passwort>
#   export DB_URL=postgresql://finanz:<pw>@192.168.1.XX/finanz
#   export TELEGRAM_BOT_TOKEN=<token>
#   export TELEGRAM_CHAT_ID=<chat-id>
#   bash infra/install_dashboard_lxc.sh
#
# Voraussetzungen:
#   - W541 Proxmox läuft, Debian-12-Template vorhanden (oder wird geladen)
#   - PostgreSQL pg_hba.conf erlaubt Verbindung von LXC_IP (einmalig manuell)
#   - GitHub SSH-Key ist auf dem Proxmox-Host eingerichtet (für git clone)
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------
LXC_ID="${LXC_ID:-521}"
LXC_HOSTNAME="${LXC_HOSTNAME:-finance-dashboard}"
LXC_IP="${LXC_IP:-192.168.1.57}"
LXC_GW="${LXC_GW:-192.168.1.2}"
LXC_BRIDGE="${LXC_BRIDGE:-vmbr0}"
LXC_STORAGE="${LXC_STORAGE:-local-lvm}"
LXC_DISK_GB="${LXC_DISK_GB:-8}"
LXC_RAM_MB="${LXC_RAM_MB:-512}"
LXC_CORES="${LXC_CORES:-1}"

DASHBOARD_PORT="${DASHBOARD_PORT:-8080}"
REPO_URL="${REPO_URL:-git@github.com:nabussan/finanz-dashboard.git}"
REPO_PATH="/opt/finanz-dashboard"
APP_USER="${APP_USER:-finanz}"

: "${LXC_ROOT_PW:?Fehler: LXC_ROOT_PW muss gesetzt sein}"
: "${DB_URL:?Fehler: DB_URL muss gesetzt sein (postgresql://finanz:<pw>@<host>/finanz)}"
: "${TELEGRAM_BOT_TOKEN:?Fehler: TELEGRAM_BOT_TOKEN muss gesetzt sein}"
: "${TELEGRAM_CHAT_ID:?Fehler: TELEGRAM_CHAT_ID muss gesetzt sein}"

# ---------------------------------------------------------------------------
log() { echo "[$(date '+%H:%M:%S')] $*"; }
lxc() { pct exec "$LXC_ID" -- bash -c "$1"; }
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

log "=== Phase 2: System-Pakete ==="

lxc "apt-get update -qq && apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip git curl ca-certificates \
    postgresql-client libpq-dev gcc python3-dev"

log "=== Phase 3: App-User + Verzeichnis ==="

lxc "id $APP_USER &>/dev/null || useradd -r -m -s /bin/bash $APP_USER"
lxc "mkdir -p $REPO_PATH && chown $APP_USER:$APP_USER $REPO_PATH"

log "=== Phase 4: SSH-Key für GitHub ==="

# SSH-Key vom Proxmox-Host in den LXC kopieren
# (Voraussetzung: ~/.ssh/id_ed25519 auf dem Proxmox-Host)
mkdir -p "/var/lib/lxc/${LXC_ID}/rootfs/home/${APP_USER}/.ssh"
cp ~/.ssh/id_ed25519     "/var/lib/lxc/${LXC_ID}/rootfs/home/${APP_USER}/.ssh/"
cp ~/.ssh/id_ed25519.pub "/var/lib/lxc/${LXC_ID}/rootfs/home/${APP_USER}/.ssh/"
cat > "/var/lib/lxc/${LXC_ID}/rootfs/home/${APP_USER}/.ssh/config" <<EOF
Host github.com
    IdentityFile ~/.ssh/id_ed25519
    StrictHostKeyChecking no
EOF
lxc "chown -R $APP_USER:$APP_USER /home/$APP_USER/.ssh && chmod 700 /home/$APP_USER/.ssh && chmod 600 /home/$APP_USER/.ssh/id_ed25519"

log "=== Phase 5: Repo klonen ==="

lxc "su - $APP_USER -c 'git clone $REPO_URL $REPO_PATH 2>/dev/null || (cd $REPO_PATH && git pull)'"

log "=== Phase 6: Python-Virtualenv + Abhängigkeiten ==="

lxc "su - $APP_USER -c 'python3 -m venv $REPO_PATH/.venv && \
    $REPO_PATH/.venv/bin/pip install --upgrade pip -q && \
    $REPO_PATH/.venv/bin/pip install -r $REPO_PATH/requirements.txt -q'"

log "=== Phase 7: .env schreiben ==="

ENV_FILE="/var/lib/lxc/${LXC_ID}/rootfs/opt/finanz-dashboard/.env"
cat > "$ENV_FILE" <<EOF
DB_URL=${DB_URL}
TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
TELEGRAM_CHAT_ID=${TELEGRAM_CHAT_ID}
EOF
lxc "chown $APP_USER:$APP_USER $REPO_PATH/.env && chmod 600 $REPO_PATH/.env"

log "=== Phase 8: systemd-Service ==="

# dashboard.service aus Repo kopieren und anpassen
SERVICE_PATH="/var/lib/lxc/${LXC_ID}/rootfs/etc/systemd/system/dashboard.service"
cat > "$SERVICE_PATH" <<EOF
[Unit]
Description=Finanz Dashboard
After=network.target

[Service]
Type=simple
User=${APP_USER}
WorkingDirectory=${REPO_PATH}
EnvironmentFile=${REPO_PATH}/.env
ExecStart=${REPO_PATH}/.venv/bin/uvicorn app:app --host 0.0.0.0 --port ${DASHBOARD_PORT} --workers 1
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

lxc "systemctl daemon-reload && systemctl enable dashboard && systemctl start dashboard"
sleep 3
lxc "systemctl is-active dashboard || (journalctl -u dashboard -n 20; exit 1)"

log "=== Phase 9: PostgreSQL pg_hba.conf-Hinweis ==="

cat <<HINT

╔══════════════════════════════════════════════════════════════════╗
║  PostgreSQL läuft in LXC 522 (192.168.1.58).                    ║
║  install_postgresql_lxc.sh richtet pg_hba.conf automatisch ein. ║
║  DB_URL in .env muss zeigen auf:                                 ║
║    postgresql://finanz:<pw>@192.168.1.58/finanz                  ║
║                                                                  ║
║  Reihenfolge beim W541-Rollout:                                  ║
║    1. install_postgresql_lxc.sh  (LXC 522)                       ║
║    2. install_finance_auto_lxc.sh (LXC 520, rsm-live)            ║
║    3. install_dashboard_lxc.sh   (LXC 521, dieses Skript)        ║
╚══════════════════════════════════════════════════════════════════╝

Dashboard erreichbar unter: http://${LXC_IP}:${DASHBOARD_PORT}
(Tailscale-Zugriff: wie gewohnt via Tailscale-IP des W541)

HINT

log "=== Installation abgeschlossen ==="
