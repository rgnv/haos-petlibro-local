#!/usr/bin/env bash
# install-tower-recorder.sh — 30-day local video recorder for the PLAF203 Tower.
#
# Run on an always-on Linux host (not HAOS). Records 3-minute clips on motion /
# feeding events, keeps 30 days, auto-cleans.
#
# Usage: sudo bash scripts/install-tower-recorder.sh <clips-dir> [broker-host]
#   clips-dir    absolute path for clips (e.g. /mnt/backups/petlibro-cam)
#   broker-host  your MQTT broker IP (default 127.0.0.1 — set if broker is remote)
set -euo pipefail
umask 077

CLIPS_DIR="${1:?usage: install-tower-recorder.sh <clips-dir> [broker-host]}"
BROKER="${2:-127.0.0.1}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
[ "$(id -u)" = 0 ] || { echo "ERROR: run as root"; exit 1; }

: "${PETLIBRO_SERIAL:?Set PETLIBRO_SERIAL}"
: "${PETLIBRO_STREAM:?Set PETLIBRO_STREAM to the camera RTSP URL}"
[[ "$PETLIBRO_SERIAL" =~ ^[A-Za-z0-9]+$ && ! "$PETLIBRO_SERIAL" =~ ^(EXAMPLE|YOUR) ]] || { echo "ERROR: invalid serial"; exit 2; }
[[ "$CLIPS_DIR" =~ ^/[A-Za-z0-9/_-]+$ && "$CLIPS_DIR" != / ]] 2>/dev/null || { echo "ERROR: use an absolute clips path with letters, digits, /, _, -"; exit 2; }
[[ "$BROKER" =~ ^[A-Za-z0-9.-]+$ ]] || { echo "ERROR: invalid broker"; exit 2; }
[[ "$PETLIBRO_STREAM" =~ ^rtsps?://[^[:space:]\\\"\'%]+$ ]] || { echo "ERROR: invalid RTSP URL (no whitespace, quotes, backslashes or percent escapes)"; exit 2; }
[[ "$PETLIBRO_STREAM" != *YOUR_* && "$PETLIBRO_STREAM" != *EXAMPLE* ]] || { echo "ERROR: replace RTSP URL placeholders"; exit 2; }
[ ! -L "$CLIPS_DIR" ] || { echo "ERROR: clips directory cannot be a symlink"; exit 2; }

RUST_BIN_DIR="${PETLIBRO_RUST_BIN_DIR:-$REPO_ROOT/polar-rs/target/release}"
[ -x "$RUST_BIN_DIR/petlibro-cam-recorder" ] || {
  echo "ERROR: missing Rust recorder; build Rust workspace first (see README)"; exit 1;
}
echo "==> Checking dependencies..."
command -v ffmpeg >/dev/null || { apt-get update -qq && apt-get install -y -qq ffmpeg; }

# --- credentials ---------------------------------------------------------------
CREDS=/root/petlibro-cam-creds.json
if [ ! -f "$CREDS" ]; then
  echo "==> The recorder needs credentials accepted by your MQTT broker (for example"
  echo "    the HAOS addon's /data/options.json — fields mqtt_username/mqtt_password)."
  read -rp "    MQTT username: " MQTT_USER
  read -rsp "    MQTT password: " MQTT_PASS; echo
  export MQTT_USER MQTT_PASS
  python3 - <<'PY'
import json, os
json.dump({"username": os.environ["MQTT_USER"], "password": os.environ["MQTT_PASS"]}, open("/root/petlibro-cam-creds.json", "w"))
PY
  chmod 600 "$CREDS"
  unset MQTT_USER MQTT_PASS
fi
chmod 600 "$CREDS"
python3 - "$CREDS" <<'PY'
import json, sys
with open(sys.argv[1]) as f:
    creds = json.load(f)
if not all(isinstance(creds.get(k), str) and creds[k] for k in ("username", "password")):
    raise ValueError("MQTT username and password must be nonempty strings")
PY
echo "==> Credentials stored at $CREDS (mode 600)."

# --- clips dir -----------------------------------------------------------------
mkdir -p "$CLIPS_DIR"

# --- deploy --------------------------------------------------------------------
install -m 0755 "$RUST_BIN_DIR/petlibro-cam-recorder" /root/petlibro-cam-recorder
install -m 0755 "$REPO_ROOT/tower/recorder/petlibro-cam-retention.sh" /usr/local/bin/petlibro-cam-retention.sh

# substitute placeholders in units + scripts
sed -e "s|CLIPS_DIR_PLACEHOLDER|$CLIPS_DIR|g" \
    "$REPO_ROOT/tower/recorder/systemd/petlibro-cam-recorder.service" > /etc/systemd/system/petlibro-cam-recorder.service
sed "s|CLIPS_DIR_PLACEHOLDER|$CLIPS_DIR|g" \
    "$REPO_ROOT/tower/recorder/systemd/petlibro-cam-retention.service" > /etc/systemd/system/petlibro-cam-retention.service
cp "$REPO_ROOT/tower/recorder/systemd/petlibro-cam-retention.timer" /etc/systemd/system/

# Point scripts at the configured directory and broker.
install -d -m 0755 /etc/default
cat > /etc/default/petlibro-cam <<EOF
PETLIBRO_CLIPS_DIR=$CLIPS_DIR
PETLIBRO_BROKER=$BROKER
PETLIBRO_SERIAL=$PETLIBRO_SERIAL
PETLIBRO_STREAM=$PETLIBRO_STREAM
EOF
chmod 0600 /etc/default/petlibro-cam

systemctl daemon-reload
systemctl enable petlibro-cam-recorder.service petlibro-cam-retention.timer
systemctl restart petlibro-cam-recorder.service petlibro-cam-retention.timer
sleep 2
systemctl is-active --quiet petlibro-cam-recorder.service
echo "==> recorder active"

echo ""
echo "=============================================================="
echo " Recorder deployed: clips -> $CLIPS_DIR/YYYY-MM-DD/, 30-day retention."
echo " Verify:  journalctl -u petlibro-cam-recorder -f"
echo "          (trigger a feed/motion; a .part file appears then finalizes)"
echo "=============================================================="
