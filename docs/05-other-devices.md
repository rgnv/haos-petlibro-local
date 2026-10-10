# Other Petlibro devices — the general approach

This repo covers two families in depth. This page is the **methodology for any
other Petlibro device**, plus what we know about the wider landscape.

## Step 1 — Identify your device

- **Model code** (e.g. `PLAF203`, `PLAF109`): shown in the Petlibro app under the
  device's Settings/Info, and in the HA cloud integration's device registry.
- **Connection type:** Petlibro's newer fleet is MQTT-based (like both devices
  here). Old or simple devices may use plain HTTPS polling — you'll see the
  difference immediately in a capture (MQTT = a persistent connection on port
  1883 with a `dl/<MODEL>/<SERIAL>/...` topic tree).

## Step 2 — See what it talks to (safe, read-only)

Watch your router's connection table for the device's IP; you'll typically see:

- `mqtt.us.petlibro.com:1883` (MQTT devices) — same cloud broker as here.
- `api.us.petlibro.com:443` (all devices — metadata, schedules, commands).

If you see the MQTT broker: the device is a takeover candidate. If it's only
HTTPS: local control still may be possible, but it starts with replaying the REST
API (a different project shape — still send us a dossier, we'll assess).

## Step 3 — Capture (the universal method)

The recipe that worked for both families here:

1. Redirect the device's port-1883 traffic to a host you control (one iptables
   DNAT rule — see `docs/02-polar-plaf109.md` step 1).
2. Capture the raw connection once (it reveals the device's client id / username /
   password — plaintext MQTT).
3. Serve it with a local broker + responder, OR relay it transparently to the real
   cloud (`polar/polar_relay.py`) — the relay is best for *learning*: trigger
   actions in the vendor app and read the exact commands from the log.

## Step 4 — Map the protocol

Compare your capture against `docs/03-protocol-reference.md`:

- **Topic family:** `dl/<PRODUCT>/<SERIAL>/device/<channel>/post` and `/sub`.
  Channels seen: `heart`, `ntp`, `event`, `service`, `config`, `system`.
- **Observed patterns, not universal laws:** QoS 1 publishes get MQTT PUBACK;
  allowlisted event posts get JSON `code:0` echoes on the matching `/sub`; service replies get none;
  `NTP` gets a timezone answer; a `DEVICE_CONFIG_SYNC` with `mqttAddr` re-points
  the device's broker (that's the local "pin", how permanent localization works).
- **Commands differ per family** (e.g. the PLAF109's feed = a `feedNowState` plan
  write) — discover them via the relay + vendor app method, never by guessing
  (we burned a dozen probe attempts proving guesses don't work).

## Step 5 — Make it local

Once commands are understood, the same architecture generalizes:

- local broker + a service that answers the device (echoes, NTP, config) —
  see `polar/polar_local_cloud.py` as the reference implementation;
- pin the device to your broker with `DEVICE_CONFIG_SYNC` (permanent);
- re-add conveniences (schedules, notifications) on your own terms.

## Known landscape (as of 2026-10)

| Model | Name | Status here |
|---|---|---|
| PLAF203 | Granary camera feeder ("Tower") | ✅ full toolkit (docs/01) |
| PLAF109 | Wet food feeder ("Polar") | ✅ full toolkit (docs/02) |
| other PLAF*/PLWF* models | various feeders/fountains | ❓ unverified — send a dossier |

**Have a different model?** Don't brute-force it — follow
[CONTRIBUTING.md](../CONTRIBUTING.md): capture a dossier (boot + a few actions,
sanitized), open a PR, and we'll map it and publish support. The busiest unknowns
right now are the fountain/water devices (`PLWF*`) and the older feeders
(`PLAF101/103/107`) — if you own one, your dossier is genuinely valuable.

## Safety notes for ANY device

- These techniques intercept the device's cloud link. Have a rollback (usually:
  before pinning, remove the redirect and reconnect; after a stored pin, restore
  the original endpoint or use a device-supported reset/reprovisioning procedure).
- Battery devices sleep hard — force reconnects for captures (docs/04).
- Never run unknown commands blindly on a device that **dispenses food/water or
  moves motors** — replay only what you captured, while supervised.
