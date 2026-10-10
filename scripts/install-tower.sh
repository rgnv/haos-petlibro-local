#!/usr/bin/env bash
# install-tower.sh — deploy this repo's Tower (PLAF203) hardening + features to HAOS.
#
# Prerequisites:
#   - The upstream addon is installed and the feeder already flashed/provisioned
#     (see docs/01-tower-plaf203.md).
#   - SSH to HAOS works (HAOS host debug SSH, port 22222; Docker access required).
#
# Usage: bash scripts/install-tower.sh <haos-host> [ssh-port] [ssh-key]
set -euo pipefail

HAOS_HOST="${1:?usage: install-tower.sh <haos-host> [ssh-port] [ssh-key]}"
SSH_PORT="${2:-22222}"
SSH_KEY="${3:-}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Required configuration is copied into the addon on every recreation.
: "${PETLIBRO_SERIAL:?Set PETLIBRO_SERIAL to your feeder serial}"
: "${PETLIBRO_BROKER:?Set PETLIBRO_BROKER to the broker hostname/IP reachable from the addon}"
PETLIBRO_TIMEZONE="${PETLIBRO_TIMEZONE:-UTC}"
[[ "$PETLIBRO_SERIAL" =~ ^[A-Za-z0-9]+$ && ! "$PETLIBRO_SERIAL" =~ ^(EXAMPLE|YOUR) ]] || { echo "ERROR: invalid serial"; exit 2; }
[[ "$PETLIBRO_BROKER" =~ ^[A-Za-z0-9.-]+$ && "$PETLIBRO_TIMEZONE" =~ ^[A-Za-z0-9/_+-]+$ ]] || { echo "ERROR: invalid broker/timezone"; exit 2; }
[[ "$HAOS_HOST" =~ ^[A-Za-z0-9.-]+$ && "$SSH_PORT" =~ ^[0-9]+$ ]] || { echo "ERROR: invalid SSH host/port"; exit 2; }
SSHC=(ssh -p "$SSH_PORT" -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new)
[ -z "$SSH_KEY" ] || SSHC+=(-i "$SSH_KEY")
SSHC+=("root@$HAOS_HOST")

echo "==> Checking SSH to HAOS ($HAOS_HOST:$SSH_PORT)..."
"${SSHC[@]}" 'command -v docker >/dev/null && command -v flock >/dev/null' >/dev/null || { echo "ERROR: SSH failed — check port/key"; exit 1; }

echo "==> Deploying patch kit to /share/petlibro-patches/ ..."
"${SSHC[@]}" 'mkdir -p /mnt/data/supervisor/share/petlibro-patches'
for f in "$REPO_ROOT"/tower/patch-kit/binding_patch.py \
         "$REPO_ROOT"/tower/patch-kit/server_aid_daemon.py \
         "$REPO_ROOT"/tower/patch-kit/fix-addon.sh; do
  cat "$f" | "${SSHC[@]}" "cat > /mnt/data/supervisor/share/petlibro-patches/$(basename "$f")"
done
"${SSHC[@]}" 'chmod +x /mnt/data/supervisor/share/petlibro-patches/fix-addon.sh; ls -la /mnt/data/supervisor/share/petlibro-patches/'

printf 'PETLIBRO_SERIAL=%s\nPETLIBRO_BROKER=%s\nPETLIBRO_TIMEZONE=%s\n' "$PETLIBRO_SERIAL" "$PETLIBRO_BROKER" "$PETLIBRO_TIMEZONE" | "${SSHC[@]}" 'umask 077; cat > /mnt/data/supervisor/share/petlibro-patches/tower.env'

echo "==> Applying patches (fix-addon.sh)..."
"${SSHC[@]}" 'sh /mnt/data/supervisor/share/petlibro-patches/fix-addon.sh'

echo "==> Reapply after addon restarts: use an external scheduler with host SSH."
echo "    HAOS host has no supported persistent cron service; see docs/01-tower-plaf203.md."

echo ""
echo "=============================================================="
echo " Tower patch kit deployed."
echo " Next:"
echo "  1. Check the addon log for: device connect -> DEVICE_START_EVENT firing."
echo "  2. Notifications: edit + run tower/automations/make_motion_automation.py"
echo "  3. 30-day recorder (on an always-on Linux box):"
echo "       See docs/01-tower-plaf203.md for required serial, RTSP URL and broker settings."
echo "  4. Dashboard: edit + run tower/dashboard/build_dashboard.py"
echo "=============================================================="
