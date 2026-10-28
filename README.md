# haos-petlibro-local — Petlibro feeders, fully local

Take your Petlibro smart feeders **off the vendor cloud** and run them entirely on
your own hardware — Home Assistant OS for the camera feeder path, any Linux box for
the wet-food feeder path. Local MQTT control without a cloud subscription. Your data,
your schedules, your control.

Two toolkits in one repo:

| Path | Device | What you get |
|---|---|---|
| 🐱 **Tower toolkit** | Petlibro **PLAF203** "Granary Camera Feeder" (fw 3.1.54 tested) | Local MQTT control via HAOS, live camera, motion notifications to phone + watch with snapshots, 30-day local video recording, full Lovelace dashboard |
| 🐾 **Polar toolkit** | Petlibro **PLAF109** wet-food feeder (fw 2.0.32 tested) | Credential capture, transparent relay for protocol discovery, **permanent local-only operation** (device stores YOUR broker), local "cloud" service that answers local MQTT requests, local feed commands, MQTT-managed feeding schedules (add/remove/enable/clear) with a Home Assistant authoring UI |

Protocols were developed on real hardware. Generalized installers need your own
configuration; read the guides and REVIEW.md before deploying.

> **Credits & foundations.** The Tower path builds on the excellent community
> project [tannerln7/Petlibro-Home-Assistant](https://github.com/tannerln7/Petlibro-Home-Assistant)
> (custom state-agent firmware + HAOS addon). This repo adds: the HAOS deployment
> hardening, the message-protocol patches that make newer firmware (3.1.54)
> complete its handshake, the notification pipeline, the 30-day recorder, the
> dashboards — and the entire Polar (PLAF109) methodology, which is from-scratch
> reverse engineering published here for the first time.

---

## ⚡ Quickstart

### 🐾 Polar wet feeder — full local takeover

**You need:** the feeder on your WiFi, a Debian/Ubuntu Linux host with systemd and Python 3.9+ for setup helpers, plus the prebuilt Rust binaries below
(any always-on machine — a Proxmox host, a Pi, a VM), and ~30 minutes.

```bash
# First build/copy the Rust binaries (see build section below).
# On your always-on Linux box (host that will run the local broker):
sudo bash scripts/install-polar.sh --serial EXAMPLE_SERIAL --broker-ip 192.168.x.x --timezone UTC

# Then follow docs/02-polar-plaf109.md for the takeover wizard:
#   step 1: put the feeder's MQTT traffic on a leash  (router redirect)
#   step 2: optionally capture connection credentials (wizard + reconnect)
#   step 3: serve it locally                          (broker + local cloud)
#   step 4: resend and verify stored local endpoint
```

Replace placeholders before running. After verification the feeder stores your
broker endpoint across ordinary reboots; it still needs that endpoint reachable. The vendor app stops working for it (that's the point); feed it
from Home Assistant, a script, or an MQTT message.

### 🐱 Tower camera feeder — local control + camera + recording

**You need:** Home Assistant OS (with SSH), a spare Linux host for the recorder
(optional), and the feeder.

```bash
# 1. Flash + provision using the upstream project (their docs): 
#    https://github.com/tannerln7/Petlibro-Home-Assistant
# 2. Then apply this repo's hardening + features:
PETLIBRO_SERIAL=EXAMPLE_SERIAL PETLIBRO_BROKER=YOUR_BROKER_HOST \
  bash scripts/install-tower.sh <haos-host>
```

This deploys the protocol patch kit through HAOS host debug SSH. Notifications,
dashboard and optional recorder are separate commands in the guide.

Full walkthrough: **docs/01-tower-plaf203.md**

---

## Build standalone Rust services

`polar-rs/` is a Cargo workspace with three binaries: `polar-local-cloud`,
`petlibro-cam-recorder`, and `polar-relay`. Build on a Linux machine with a current
stable Rust toolchain; deployment hosts need the matching binaries, not Rust.
The recorder also needs `ffmpeg`. HAOS addon code stays Python.

```bash
cargo build --manifest-path polar-rs/Cargo.toml --workspace --release --offline
cargo test --manifest-path polar-rs/Cargo.toml --workspace --offline
```

Offline builds require a populated Cargo dependency cache (including `rumqttc`).
On a build machine where downloads are allowed, omit `--offline` for the initial
build. Copy all three binaries from `polar-rs/target/release/` to the deployment
host, built for its CPU architecture and libc. Rust timezone rules are embedded
via `chrono-tz`; rebuild with updated dependency data when timezone laws change.

The shell installers now require these prebuilt binaries before changing the
host. By default they read `polar-rs/target/release/`; set
`PETLIBRO_RUST_BIN_DIR=/absolute/path/to/binaries` to use a copied build. Their
systemd units run `/root/polar-local-cloud` and `/root/petlibro-cam-recorder`;
the takeover wizard runs `/root/polar-relay`. Existing environment files and
schedule/credential paths remain compatible. Do not run Python and Rust copies
of the same service together: their MQTT client IDs are intentionally identical.

The Rust cloud accepts `polar/cmd/set` with the same allowlisted attribute shape
as Python, e.g. `{"attr":"volume","value":50}`, and sends `ATTR_SET_SERVICE` with
generated `ts`/`msgId`. `polar/cmd/bell` sends the vendor cloud's
`DEVICE_FUNCTION_TEST_SERVICE {action:"AUDIO"}` — the attribute-write form is
accepted by the device but never makes a sound. `polar/cmd/schedule` manages the
feeding schedule (`add`/`remove`/`enable`/`disable`/`clear`/`list`, selected by
1-based `index` or entry `id`), persists it atomically and republishes the
retained `polar/schedule` state on every change and on connect. Retained
commands are ignored. Heartbeat status includes tracked attributes and the
schedule entries.

See [RUST-PORT.md](RUST-PORT.md) for parity details, test coverage, and validation
status. The Python files remain as reference implementations and support the
existing one-shot Python helpers; installers run the Rust services.

## 📁 Repo map

```
docs/                  full step-by-step guides (read these!)
├── 01-tower-plaf203.md      Tower: flash → local → notifications → recording
├── 02-polar-plaf109.md      Polar: capture → relay → local-only, permanent
├── 03-protocol-reference.md the MQTT protocol for both device families
├── 04-troubleshooting.md    every pitfall we hit, and the fix
├── 05-other-devices.md      methodology for ANY other Petlibro device
├── 06-credentials-and-connections.md  what's shared, capture + exploit recipes
└── protocol/                full packet-level reference (wire packets, topics,
                             every message field-by-field, flows, field dictionary)

CONTRIBUTING.md        how to submit a device dossier so we can add support
devices/               community dossiers + submission template

tower/
├── patch-kit/         HAOS-side hardening (binding patch, fixer, server-aid daemon)
├── installer-patches/ bootstrap/OTA-flow patches for firmware 3.1.49+ (the '54 unlock)
├── recorder/          30-day motion/feeding video recorder + systemd units
├── automations/       motion → snapshot → phone/watch notification builder
├── dashboard/         Lovelace dashboard builder
└── 99-tower-ha-bridge.conf   MQTT bridge config (split-broker setups)

polar-rs/              standalone Rust services (Cargo workspace)
├── polar-cloud/       Polar local MQTT cloud
├── tower-recorder/    Tower MQTT-triggered ffmpeg recorder
└── polar-relay/       transparent TCP capture relay

polar/                Python references + one-shot helpers
├── polar_relay.py     transparent relay: device → you → vendor cloud (protocol capture)
├── polar_make_local.py  one-shot: pin the device's broker to YOUR address (permanent)
├── polar_local_feed.py  CLI: feed now (plate + duration)
├── polar_local_cloud.py the Python reference service (same MQTT surface as the Rust one)
├── ha/                HA helper package + scripts (per-plate feeds, schedule editor)
├── dashboard/         dashboard README + ready-made Lovelace view
├── systemd/           service units (local cloud, discovery publisher)
└── tests/             34 unit tests (protocol payloads, echo routing, scheduler, schedule API)

scripts/
├── install-tower.sh          one-command Tower deployment (HAOS)
├── install-tower-recorder.sh one-command 30-day recorder deployment
├── install-polar.sh          one-command Polar broker + service setup
├── polar-takeover.sh         guided capture → verify → pin-local wizard
└── sanitize-capture.py       redact credentials before sharing captures
```

## 🔐 About credentials (important)

Your feeder authenticates to MQTT with **per-device credentials** (username/password,
visible in plaintext on the wire — the devices use plain MQTT). These are unique to
your device; nobody can hand you a working set.

This repo does the next best thing: **the tooling captures your device's own
credentials automatically** the first time it connects through your redirect —
you'll see them in step 2 of the Polar guide (and the Tower addon does the same
under the hood). Shared infrastructure details (cloud broker hostnames, API
endpoints, topic structures) that are the same for every user are documented in
docs/03-protocol-reference.md.

Placeholder convention: `EXAMPLE_SERIAL` for serial examples; `192.168.x.x` for
your LAN address; `YOUR_*` for other required configuration. Angle-bracket fields
are descriptive templates, not literal shell input. Tests use `TESTSERIAL` and
reserved documentation IPs. Sanitized fields use `<REDACTED>`, `<MAC>`, `<EMAIL>`,
`<MEMBER_ID>`, `<SSID>`, and `<LAN_IP>`.

**Never commit captured credentials.** The scripts write them to files with mode
0600; keep them out of version control.

## ⚠️ Read this first

- These tools intercept traffic between your device and the vendor cloud. Your
  device will disappear from the vendor app once on the local path (by design).
  The vendor cannot help you with a device running this stack.
- The Polar path includes commands that **physically operate the feeder**
  (feed/dispense). Test while supervised; don't run blind schedules until you've
  verified the basics.
- Everything is provided as-is (MIT). You are responsible for your own devices,
  network, and local regulations.

## License

MIT — see LICENSE.
