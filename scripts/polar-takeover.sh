#!/usr/bin/env bash
# polar-takeover.sh — guided takeover for Petlibro MQTT devices.
#
# The ROUTER WORK IS MANUAL BY DESIGN — redirect commands differ per environment
# (OpenWrt/GL.iNet/OPNsense/consumer routers/managed switches). This tool does
# everything on YOUR host side and prints exactly what to run on your router.
#
# Subcommands:
#   discover                     — find Petlibro-ish devices on your LAN (ARP + OUI)
#   capture <feeder-ip> [mac]    — raw listener; prints router steps + waits for the
#                                  device's MQTT CONNECT, then extracts credentials
#   serve                        — switch to the local broker + polar-local-cloud
#   pin [broker-ip]              — store YOUR broker address in the device (permanent)
#   verify                       — show live device status
#   relay [upstream-host]        — foreground transparent relay
#
# Run install-polar.sh first.
set -euo pipefail

umask 077
set -a
. /etc/default/polar
set +a
PORT="$POLAR_PORT"
[ "$(id -u)" = 0 ] || { echo "ERROR: run as root"; exit 1; }

BROKER_IP="$POLAR_LOCAL_HOST"

raw_listen_start() {
  command -v socat >/dev/null
  if [ -e /root/polar-capture.log ]; then
    echo "ERROR: move /root/polar-capture.log aside before capturing again"; exit 1
  fi
  systemctl stop polar-local-cloud mosquitto
  : > /root/polar-capture.log
  setsid socat -u TCP-LISTEN:$PORT,reuseaddr,fork OPEN:/root/polar-capture.log,append &
  listener=$!
  trap 'kill -- -"$listener" 2>/dev/null || true; wait "$listener" 2>/dev/null || true' EXIT
  sleep 1
  kill -0 "$listener" || { echo "ERROR: capture listener failed"; exit 1; }
}

wait_for_connect() {
  local i
  for i in $(seq 1 60); do
    grep -aq "MQTT" /root/polar-capture.log 2>/dev/null && return 0
    sleep 5
  done
  return 1
}

extract_creds() {
  python3 - <<'PY'
import re
raw = open("/root/polar-capture.log", "rb").read()
strings = [s.decode() for s in re.findall(rb"[ -~]{6,}", raw)
           if 6 <= len(s) <= 64 and b"MQTT" not in s]
picks = []
for s in strings:
    if re.fullmatch(r"AF[0-9A-Z]{10,}", s):
        picks.append(("client_id", s))
    elif re.fullmatch(r"[A-Z0-9]{16}", s):
        picks.append(("username", s))
    elif re.fullmatch(r"[A-Za-z0-9+/=]{24,64}", s):
        picks.append(("password", s))
seen, uniq = set(), []
for role, val in picks:
    if (role, val) not in seen:
        seen.add((role, val)); uniq.append((role, val))
if not uniq:
    print("  (no credential-like strings found — capture may be incomplete)")
else:
    print("  == CREDENTIAL CANDIDATES (heuristic; store securely; never commit) ==")
    for role, val in uniq[:6]:
        print(f"    {role}: {val}")
PY
  chmod 600 /root/polar-capture.log
}

print_router_steps() {
  local FEEDER_IP="$1" FEEDER_MAC="${2:-}"
  echo ""
  echo "==> ROUTER WORK (manual — depends on your environment):"
  echo ""
  echo "    A) Redirect the device's cloud MQTT to this host ($BROKER_IP:$PORT)."
  echo "       Linux-based routers (OpenWrt / GL.iNet):"
  echo "         iptables -t nat -I PREROUTING 1 -s $FEEDER_IP -p tcp --dport 1883 \\"
  echo "           -j DNAT --to-destination $BROKER_IP:$PORT"
  echo "       Allow forwarding and configure hairpin SNAT if return traffic bypasses the router."
  echo "       Do not redirect TLS port 8883 to this plaintext listener."
  echo "       Consumer routers: look for 'port forwarding' / 'static route' / a"
  echo "       per-device policy rule; some firmware cannot do this — see"
  echo "       docs/02-polar-plaf109.md (Without router redirection) for alternatives."
  echo ""
  echo "    B) Force the device to reconnect so we can capture its CONNECT."
  echo "       hostapd-based APs (OpenWrt/GL.iNet), from the router shell:"
  echo "         ls /var/run/hostapd*                # find the socket dir"
  echo "         hostapd_cli -p /var/run/hostapd-wifi0 -i wlan0 deauthenticate $FEEDER_MAC"
  echo "       Other APs: toggle the device's WiFi block in the router UI, or"
  echo "       power-cycle the device."
  echo ""
}

discover() {
  echo "==> Reading the local ARP cache for likely Petlibro devices..."
  echo "    (Petlibro uses Espressif WiFi modules; matching common OUIs)"
  local OUI_RE='(50:78:7d|cc:64:1a|24:0a:c4|ac:67:b2|48:27:e2|c4:4f:33|d8:3a:dd|b4:8a:0a|a4:cf:12|68:c6:3a)'
  local found=0 ip mac macl name
  while read -r ip _ _ mac _ _ _; do
    [ "$ip" = "IP address" ] && continue
    macl=$(echo "$mac" | tr 'A-F' 'a-f')
    if echo "$macl" | grep -qE "$OUI_RE"; then
      name=$(getent hosts "$ip" 2>/dev/null | awk '{print $2}' || true)
      echo "  $ip  $mac  ${name:-}"
      found=$((found+1))
    fi
  done < /proc/net/arp
  [ "$found" = 0 ] && echo "  (none matched — check your router's client list; the device may be asleep)"
  echo "==> Next: sudo bash $0 capture <IP> <MAC>"
}

capture() {
  local FEEDER_IP="${1:?usage: capture <feeder-ip> [feeder-mac]}" FEEDER_MAC="${2:-}"
  echo "==> Raw capture listener on :$PORT (/root/polar-capture.log, mode 600)"
  raw_listen_start
  print_router_steps "$FEEDER_IP" "$FEEDER_MAC"
  echo "==> Waiting up to 5 minutes for the MQTT CONNECT..."
  if wait_for_connect; then
    extract_creds
    echo "==> Next: sudo bash $0 serve"
  else
    echo "!! No CONNECT captured. Re-check the redirect / force a reconnect, then re-run."
    exit 1
  fi
}

serve() {
  echo "==> Switching to local broker + polar-local-cloud..."
  # keep the redirect as the takeover net; if you removed it, re-add it or the
  # device will drift back to the cloud until 'pin' runs.
  systemctl start mosquitto polar-local-cloud
  sleep 2
  systemctl is-active --quiet mosquitto
  systemctl is-active --quiet polar-local-cloud
  echo "==> Watching 30s for the device (HEARTBEAT = success):"
  timeout 30 mosquitto_sub -h 127.0.0.1 -p "$PORT" -t '#' -v 2>/dev/null || true
  echo "==> Next: sudo bash $0 pin"
}

pin() {
  local B="${1:-$BROKER_IP}"
  echo "==> Pinning the device to $B:$PORT (permanent local)..."
  POLAR_LOCAL_HOST="$B" POLAR_LOCAL_PORT="$PORT" python3 /root/polar_make_local.py
  echo "==> PROOF: remove the router redirect, 'systemctl restart mosquitto', watch it reconnect directly."
}

verify() {
  systemctl is-active mosquitto polar-local-cloud 2>/dev/null || true
  echo "== recent device traffic =="
  timeout 15 mosquitto_sub -h 127.0.0.1 -p "$PORT" -t '#' -v 2>/dev/null | tail -15 || true
}

relay() {
  local upstream="${1:-mqtt.us.petlibro.com}"
  systemctl stop polar-local-cloud mosquitto
  echo "==> Foreground relay; Ctrl-C to stop, then run serve. Log contains credentials (0600)."
  POLAR_UPSTREAM="$upstream" /root/polar-relay
}

cmd="${1:-help}"; shift || true
case "$cmd" in
  discover) discover ;;
  capture) capture "$@" ;;
  serve) serve ;;
  pin) pin "$@" ;;
  verify) verify ;;
  relay) relay "$@" ;;
  help) grep '^#' "$0" | head -22 ;;
  *) echo "ERROR: unknown command: $cmd"; exit 2 ;;
esac
