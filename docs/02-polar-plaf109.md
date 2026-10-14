# Polar (PLAF109 wet-food feeder) — local takeover

Tested protocol: firmware 2.0.32. Run commands from the repository root unless
shown with an installed `/root/` path. Use a Debian/Ubuntu Linux host with systemd,
Python 3.9+, pip, and root access. The installer uses apt/pip and therefore needs
internet during setup; operation afterward uses your local MQTT broker.

The feeder uses plaintext MQTT on port 1883. Its per-device credentials can be
captured on reconnect. The local broker below permits anonymous connections, so
capturing credentials is optional for this stack, but useful for protocol work.

**Security:** The installed listener allows anyone who can reach its port to send
physical feed commands. Use an isolated, trusted LAN/VLAN and firewall access to
only the feeder and your control hosts. Never expose this port to the internet.
Do not use an existing shared Mosquitto installation without reviewing its listener
and authentication configuration first. The installer restarts that broker.

## Step 0 — Install and configure

Find the serial in the app (Device → Settings → Device Info). Replace all values
below: `EXAMPLE_SERIAL` is never a usable serial; `192.168.x.x` means your host's
stable LAN IPv4 address reachable from the feeder. The installer rejects these
placeholders. Choose an IANA timezone for schedules and device time.

```bash
sudo bash scripts/install-polar.sh --serial EXAMPLE_SERIAL \
  --broker-ip 192.168.x.x --timezone America/Los_Angeles
```

Optional `--port 1884` selects the local listener (default 1884). The installer
installs Mosquitto, its client tools, socat and paho-mqtt 2.x; writes
`/etc/mosquitto/conf.d/polar-capture.conf`; deploys the Rust service binaries
and the Python helper/reference scripts to `/root/`; writes `/etc/default/polar`
(0600); and enables Mosquitto and
`polar-local-cloud.service`. Failures stop installation. Inspect failures with
`journalctl -u polar-local-cloud -u mosquitto`.

The service and takeover wizard use `/etc/default/polar`:

| Variable | Meaning |
|---|---|
| `POLAR_SERIAL` | Actual feeder serial |
| `POLAR_BROKER`, `POLAR_PORT` | Broker reached by the service and Python helpers (127.0.0.1 and chosen port) |
| `POLAR_LOCAL_HOST`, `POLAR_LOCAL_PORT` | Address stored in the feeder (LAN IPv4 and chosen port) |
| `POLAR_TIMEZONE` | IANA timezone; default UTC |
| `POLAR_SCHEDULE` | Optional schedule path; default `/root/polar_schedule.json` |

Direct Python commands need these variables exported; they do not read the file
themselves. After changing the file, restart `polar-local-cloud` and reload it in
any shell used for helpers. Changing the listening port also requires changing
the Mosquitto config and router redirect.

**The running service reasserts this broker address on DEVICE_START_EVENT.**
Connecting the feeder to this stack can persist its local address already in
step 3; step 4 explicitly resends and verifies that setting. Back up your original
endpoint before takeover. Removing a redirect alone does not undo a stored pin.

## Step 1 — Redirect on your router

Replace `DEVICE_IP` and `BROKER_IP` in this router command with actual addresses:

```bash
iptables -t nat -I PREROUTING 1 -s DEVICE_IP -p tcp --dport 1883 \
  -j DNAT --to-destination BROKER_IP:1884
```

Use your chosen port if different. Allow forwarding through the router firewall.
If feeder and broker share a subnet, the reply must return through the router;
configure corresponding hairpin SNAT/masquerading or use separate routed VLANs.
Exact forwarding/SNAT rules depend on your router. Clear the existing connection
or reconnect the feeder; NAT rules apply to new connections. Persist rules using
your router's supported mechanism. Do not redirect TLS port 8883 to this plaintext
listener: it cannot terminate TLS.

## Step 2 — Optional credential capture

Start the listener **before** forcing a reconnect:

```bash
# Optional hints from local ARP cache (not authoritative device identification):
sudo bash scripts/polar-takeover.sh discover
# Router configuration remains manual:
sudo bash scripts/polar-takeover.sh capture DEVICE_IP DEVICE_MAC
```

This stops the local broker/service, captures raw bytes on the configured port,
and waits up to five minutes for an MQTT marker. It prints candidate credential
strings, not a fully decoded CONNECT. `/root/polar-capture.log` is private (0600).
Move any previous capture aside first; it is never silently overwritten. The
listener stops on exit, including timeout or Ctrl-C. Run `serve` afterward.

From a separate router shell, reconnect the feeder. On hostapd-based APs:

```bash
hostapd_cli -p /var/run/hostapd-wifi0 -i wlan0 deauthenticate DEVICE_MAC
```

Find the correct socket and interface for your AP; otherwise use its UI or
power-cycle the feeder. Restarting your local broker cannot break a still-active
connection to the vendor cloud. Never publish raw captures or credentials.

## Step 3 — Serve locally

```bash
sudo bash scripts/polar-takeover.sh serve
```

The wizard starts both services and displays traffic for 30 seconds. Watch longer
with `mosquitto_sub -h 127.0.0.1 -p 1884 -t '#' -v`; substitute your configured
port. Expect HEARTBEAT and DEVICE_START_EVENT. There is no automatically created
`polar_topic_capture.log`. Service logs: `journalctl -u polar-local-cloud -f`.

For a supervised feed test, open a root shell and load helper configuration:

```bash
sudo -i
set -a
. /etc/default/polar
set +a
python3 /root/polar_local_feed.py 1 60
```

Or publish `{"plate":1,"duration":60}` to `polar/cmd/feed` on the configured broker.
Never retain feed commands. Broker publish success is not device execution
confirmation: watch WET_GRAIN_OUTPUT_EVENT for progress.

## Step 4 — Resend and verify the stored local address

```bash
sudo bash scripts/polar-takeover.sh pin
```

Alternatively, in the configured root shell above:
`python3 /root/polar_make_local.py`. Check for the matching device reply with
`code:0`; the helper reports broker acceptance only. Remove the redirect,
reconnect the feeder, and verify traffic reaches your broker directly. Keep a
stable broker address. Factory resets, reprovisioning or firmware changes may
undo this setting; moving to another network requires a reachable broker address.

## Schedules and Home Assistant

Edit `/root/polar_schedule.json` (created as `[]`):

```json
[{"time":"06:00","plate":1,"duration":240,"days":[0,1,2,3,4,5,6]}]
```

Days: Monday=0 through Sunday=6. Times use `POLAR_TIMEZONE`; entries reload roughly
every 20 seconds. Catch-up is limited to five minutes. Deduplication is in memory:
a service restart during a scheduled minute can feed twice. Do not run identical
schedules in HA and this service. No device-plan synchronization or HA discovery
is implemented; see [the HA examples](../polar/dashboard/README.md).

## Relay / vendor-app mode

```bash
sudo bash scripts/polar-takeover.sh relay
```

Runs in the foreground on the configured port, stopping the broker/service first.
Ctrl-C stops it; run `serve` to return to local mode. It forwards to
`mqtt.us.petlibro.com:1883` by default; optional argument overrides the upstream.
`POLAR_UPSTREAM_PORT` overrides its upstream port. The relay log at
`/root/polar_relay.log` is 0600 and includes secrets. It logs only the first 600
bytes of each TCP chunk, so it is not a complete packet transcript. App behavior
after pinning still depends on vendor provisioning; verify on your device.

## Without router redirection

Device-scoped DNS overrides can point the vendor hostname at your host, but DNS
cannot change port 1883 to 1884. Use `--port 1883` on a host with that port free,
or configure an explicit local redirect. A mirrored capture alone does not route
the device to your broker and cannot make a local publish reach a device still
connected to the vendor. Avoid global DNS overrides affecting other devices or
the relay's upstream resolution.

Standalone service and relay now use prebuilt Rust binaries. Build/copy them as
described in [README](../README.md#build-standalone-rust-services) before running
the shell installer. One-shot Python feed/pin helpers remain supported.
