#!/usr/bin/env bash
# install-polar.sh — set up the Polar (PLAF109) local stack on THIS host.
#
# Installs: mosquitto broker on port 1884, the polar_local_cloud service,
# helper scripts in /root, and prints the router steps for the takeover.
#
# Usage: sudo bash scripts/install-polar.sh --serial <serial> --broker-ip <ip> [--port 1884] [--timezone UTC]
set -euo pipefail

PORT=1884
BROKER_IP=""
SERIAL=""
TIMEZONE=UTC
umask 077
while [ $# -gt 0 ]; do
  case "$1" in
    --serial) SERIAL="${2:?--serial needs a value}"; shift 2;;
    --timezone) TIMEZONE="${2:?--timezone needs a value}"; shift 2;;
    --port) PORT="$2"; shift 2;;
    --broker-ip) BROKER_IP="$2"; shift 2;;
    *) echo "unknown arg: $1"; exit 2;;
  esac
done

[ "$(id -u)" = 0 ] || { echo "ERROR: run as root (sudo bash $0)"; exit 1; }

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
[ -f "$REPO_ROOT/polar/polar_local_cloud.py" ] || {
  echo "ERROR: run this from the repo (scripts/install-polar.sh)"; exit 1; }

# Validate before changing the host. Never guess which interface the feeder can reach.
PYTHONPATH="$REPO_ROOT/polar" python3 - "$SERIAL" "$BROKER_IP" "$PORT" "$TIMEZONE" <<'PYTHON'
import sys
from zoneinfo import ZoneInfo
from polar_local_cloud import validate_serial, validate_local_address
validate_serial(sys.argv[1])
validate_local_address(sys.argv[2], int(sys.argv[3]))
ZoneInfo(sys.argv[4])
if not all(c.isalnum() or c in '/_+-' for c in sys.argv[4]):
    raise ValueError("unsupported timezone spelling")
PYTHON
RUST_BIN_DIR="${PETLIBRO_RUST_BIN_DIR:-$REPO_ROOT/polar-rs/target/release}"
for binary in polar-local-cloud polar-relay; do
  [ -x "$RUST_BIN_DIR/$binary" ] || {
    echo "ERROR: missing $RUST_BIN_DIR/$binary; build Rust workspace first (see README)"; exit 1;
  }
done
echo "==> Using broker address: $BROKER_IP:$PORT"

# --- 1. dependencies -----------------------------------------------------------
if ! command -v mosquitto >/dev/null || ! command -v mosquitto_pub >/dev/null || ! command -v socat >/dev/null; then
  apt-get update -qq
  apt-get install -y -qq mosquitto mosquitto-clients socat
fi
for tool in mosquitto mosquitto_pub mosquitto_sub socat setsid; do command -v "$tool" >/dev/null; done

# --- 2. broker listener on PORT ------------------------------------------------
install -d -m 0755 /etc/mosquitto/conf.d
cat > /etc/mosquitto/conf.d/polar-capture.conf <<EOF
# Polar local takeover — broker listener
listener $PORT 0.0.0.0
allow_anonymous true
max_keepalive 300
log_type all
EOF
chmod 0644 /etc/mosquitto/conf.d/polar-capture.conf
systemctl restart mosquitto
systemctl is-active --quiet mosquitto
echo "==> mosquitto active on $PORT"

# --- 3. deploy the service files ----------------------------------------------
install -m 0755 "$REPO_ROOT/polar/polar_local_cloud.py" /root/polar_local_cloud.py
install -m 0755 "$RUST_BIN_DIR/polar-local-cloud" /root/polar-local-cloud
install -m 0755 "$RUST_BIN_DIR/polar-relay" /root/polar-relay
install -m 0755 "$REPO_ROOT/polar/polar_make_local.py"  /root/polar_make_local.py
install -m 0755 "$REPO_ROOT/polar/polar_local_feed.py"  /root/polar_local_feed.py

install -d -m 0755 /etc/default
cat > /etc/default/polar <<EOF
POLAR_SERIAL=$SERIAL
POLAR_BROKER=127.0.0.1
POLAR_PORT=$PORT
POLAR_LOCAL_HOST=$BROKER_IP
POLAR_LOCAL_PORT=$PORT
POLAR_TIMEZONE=$TIMEZONE
EOF
chmod 0600 /etc/default/polar
install -m 0644 "$REPO_ROOT/polar/systemd/polar-local-cloud.service" /etc/systemd/system/polar-local-cloud.service
systemctl daemon-reload
systemctl enable mosquitto polar-local-cloud.service
systemctl restart polar-local-cloud.service
sleep 2
systemctl is-active --quiet polar-local-cloud
echo "==> polar-local-cloud active"

# --- 4. example schedule -------------------------------------------------------
if [ ! -f /root/polar_schedule.json ]; then
  cat > /root/polar_schedule.json <<'EOF'
[]
EOF
  echo '==> /root/polar_schedule.json created (empty; add entries like {"time":"06:00","plate":1,"duration":240,"days":[0,1,2,3,4,5]})'
fi

echo ""
echo "=============================================================="
echo " Polar local stack is UP on $BROKER_IP:$PORT"
echo "=============================================================="
echo " Next steps (full details: docs/02-polar-plaf109.md):"
echo ""
echo "  1. On your ROUTER, redirect the feeder's cloud traffic here:"
echo "       iptables -t nat -I PREROUTING 1 -s <FEEDER_IP> -p tcp --dport 1883 \\"
echo "         -j DNAT --to-destination $BROKER_IP:$PORT"
echo ""
echo "  2. Capture credentials:   sudo bash $REPO_ROOT/scripts/polar-takeover.sh capture <FEEDER_IP> <FEEDER_MAC>"
echo "  3. Serve:                 sudo bash $REPO_ROOT/scripts/polar-takeover.sh serve"
echo "  4. Resend local pin:  sudo bash $REPO_ROOT/scripts/polar-takeover.sh pin"
echo ""
echo "  Feed test:  mosquitto_pub -h 127.0.0.1 -p $PORT -t polar/cmd/feed -m '{\"plate\":1,\"duration\":60}'"
echo "  Watch:      mosquitto_sub -h 127.0.0.1 -p $PORT -t '#' -v"
echo "=============================================================="
