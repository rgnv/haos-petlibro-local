# Credentials & connection recipes (the practical guide)

Everything you need to actually connect to a Petlibro device's broker — what's
shared across the ecosystem, what's unique to your device, how the captures look,
and copy-paste recipes for watching and commanding a device locally or on the wire.

## How the credentials work (facts from real captures)

Every Petlibro device authenticates to MQTT with **its own account**, issued by the
vendor cloud. The credentials are visible in plaintext in the CONNECT packet
(Petlibro uses plain MQTT on port 1883 — no TLS):

| Field | What it looks like | Shared? |
|---|---|---|
| `client_id` | the device serial, e.g. `EXAMPLE_SERIAL` | unique per device |
| `username` | 16-char token, e.g. `<MQTT_USERNAME>` | unique per device |
| `password` | 32-char token, e.g. `<MQTT_PASSWORD>` | unique per device |
| Backend account name | `petlibro_backend` | **shared** — the name your own broker backend uses (see the upstream project's example config: `backend_mqtt_username: "petlibro_backend"`, with a user-chosen password) |
| Cloud broker | `mqtt.us.petlibro.com:1883` (plain MQTT) | shared |
| Cloud API | `api.us.petlibro.com:443` | shared |
| Device topic root | `dl/<MODEL>/<SERIAL>/device/…` (e.g. `dl/PLAF203/…`, `dl/PLAF109/…`) | shared format, serial unique |

**There is no universal device password.** Captures from multiple devices show the
device accounts are independently generated — the only "generic" credential in the
ecosystem is the `petlibro_backend` account *name* you use on your **own** broker
(the password is whatever you provision). So the trick isn't "a magic login" — it's
**capturing your own device's** account with the tooling here (requires a fresh
connection). The wizard handles the local listener; router configuration and
reconnect steps are manual. No MQTT-credential retrieval through the cloud API is
implemented here; a device record without credentials does not establish what
every vendor endpoint exposes.

## Capture recipe (get your device's credentials)

1. Install and configure the local stack first (Polar guide step 0), then redirect the device's `:1883` to a host you control (one DNAT rule — see
   docs/02-polar-plaf109.md, step 1).
2. Run the wizard: `sudo bash scripts/polar-takeover.sh capture <DEVICE_IP> <DEVICE_MAC>`
   — it listens raw and prints candidate credential strings when the device reconnects
   (force a reconnect if needed: deauth / power-cycle).
3. You'll get: `client_id` (your serial), `username`, `password`. Store them 0600.

## Exploit / usage recipes

### A. Watch your device on YOUR broker (local mode)

```bash
# everything the device says:
mosquitto_sub -h 127.0.0.1 -p 1884 -t '#' -v

# just events (motion/output events):
mosquitto_sub -h 127.0.0.1 -p 1884 -t 'dl/+/+/device/event/post' -v

# as a script, with the device's own account (optional — anonymous works on the
# capture broker; use credentials on brokers that require auth):
mosquitto_sub -h <BROKER> -p 1884 -u <USERNAME> -P <PASSWORD> -t '#' -v
```

### B. Command your device (local mode)

```bash
# PLAF109 feed (plate, seconds):
POLAR_SERIAL=EXAMPLE_SERIAL python3 polar/polar_local_feed.py 1 240
# or let the configured local service build current timestamps and unique IDs:
mosquitto_pub -h 127.0.0.1 -p 1884 -t polar/cmd/feed -q 1 \
  -m '{"plate":1,"duration":240}'

# PLAF203 manual feed (portions):
mosquitto_pub -h <BROKER> -t 'dl/PLAF203/EXAMPLE_SERIAL/device/service/sub' \
  -q 1 -m '{"cmd":"MANUAL_FEEDING_SERVICE","ts":0,"msgId":"cli","grainNum":2}'
```

### C. Connect TO the vendor cloud broker with your device's account

Replace every example value, including client ID; authentication and ACL behavior
depend on the vendor. Your device's credentials authenticate against the real cloud broker
(`mqtt.us.petlibro.com:1883`). **Caveat: one MQTT session per client id** — if you
connect while the device itself is online, you'll kick each other off in a loop.
Use it only when the device is off-cloud, or on a broker where it isn't present:

```bash
mosquitto_sub -h mqtt.us.petlibro.com -p 1883 -u <USERNAME> -P <PASSWORD> \
  -i EXAMPLE_SERIAL -t 'dl/<MODEL>/EXAMPLE_SERIAL/device/#' -v
```

This is exactly how the relay capture works: the device's own session passes
through a transparent proxy. Logs truncate each TCP chunk to 600 bytes. To learn a
command's exact shape, trigger it in the vendor app and read the relay log.

### D. Replay captured commands locally

Take the payload from a relay/capture log and publish it to your local broker
(NOT the cloud) on the matching `/sub` topic. Done for the PLAF109 feed — it
executes when valid. Use fresh timestamps, dates and message IDs; do not blindly
replay old feed commands or assume other models share the same protocol.

## Security notes (read)

- Treat any capture file as sensitive: it contains credentials. The sanitizer
  (`scripts/sanitize-capture.py`) heuristically redacts textual logs; it rejects
  binary/socat captures. Always **review the output**, especially split TCP chunks.
- Device credentials let whoever holds them control that device's cloud session.
  Broker ACL scope is unverified; guard them like a password.
- Because the vendor uses plain MQTT, anyone on the network path can see these
  credentials — one more reason your device belongs on your own broker.
