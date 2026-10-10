# Tower (PLAF203 camera feeder) — HAOS local toolkit guide

The complete procedure for a Petlibro **PLAF203** on local control, plus everything
this repo adds on top of the upstream project: the fw 3.1.54 handshake fixes, the
notification pipeline, 30-day recording, and the camera dashboard.

**Architecture:** the feeder runs community firmware (state agent) that points it at
**your** MQTT broker (the HAOS Mosquitto addon). A custom HAOS addon speaks the
feeder's protocol and exposes ~80 entities (controls, schedules, sensors). Camera
streams via go2rtc. Everything local.

> **Foundation:** firmware + base addon come from
> [tannerln7/Petlibro-Home-Assistant](https://github.com/tannerln7/Petlibro-Home-Assistant).
> Follow their flash/provisioning docs first; this repo picks up where they end
> (especially for firmware newer than 3.1.48, which needs the extra patches here).

---

## Part 1 — Get onto local (upstream method + our fixes)

1. **Flash** the feeder with the upstream state-agent firmware (their installer
   handles OTA). During this the feeder briefly talks to a bootstrap broker — do
   not power-cycle mid-flash; keep power stable.
2. **Install the HAOS addon** (upstream repo) and start it. The addon + Mosquitto
   run on your Home Assistant OS box; the feeder connects to it after the flash
   completes.
3. **Apply this repo's patch kit** (developed against fw 3.1.54; verify other versions):

```bash
PETLIBRO_SERIAL=EXAMPLE_SERIAL PETLIBRO_BROKER=YOUR_BROKER_HOST \
PETLIBRO_TIMEZONE=UTC bash scripts/install-tower.sh <haos-host>
```

Replace serial/host placeholders first. SSH must reach the **HAOS host debug
shell** (default port 22222), with Docker and flock available; the regular
Terminal & SSH addon is not that shell. Optional arguments: SSH port, SSH key.
Configuration is saved to `tower.env` beside the patch kit, then copied to
`/data/tower.env` in the addon. Choose your IANA timezone (default UTC).
The default container is `app_dfcf7f92_petlibro_local`; if your addon slug differs,
set `PETLIBRO_CONTAINER` when invoking `fix-addon.sh` on the HAOS host.

What it does (via `/share/petlibro-patches/` on HAOS):

- `binding_patch.py` — makes the addon **reply to the feeder's `BINDING`
  registration** with the protocol echo (`code:0` on the matching `/sub` topic).
  Without this, newer firmware never sends `DEVICE_START_EVENT` and the addon's
  bootstrap chain stalls. **This is the single most important fix.**
- `server_aid_daemon.py` — a standing responder in the addon container: after an observed
  new session it sends the opening pushes (plan ×2 → NTP_SYNC → proactive NTP
  answer) that the '54 expects, and answers NTP requests.
- `fix-addon.sh` — (re)applies everything idempotently. **Run it after any addon
  restart/reinstall** — supervisor restarts recreate the container and wipe /opt
  patches. The installer does not install a cron on HAOS; use an external
  scheduler to SSH to the host and run the command below.

On the HAOS host, reapply with:
`sh /mnt/data/supervisor/share/petlibro-patches/fix-addon.sh`.
The responder sends **empty feeding plans** during its handshake. Verify your
feeding schedules afterward; do not rely on unverified device-resident plans.
Its session detection uses 300 seconds of silence, so fast device reconnects may
not repeat the opening pushes.

4. **Verify:** in the addon log you should see the device connect, `DEVICE_START_EVENT`
   fire, then entity discovery (≈80 entities under "Petlibro cat feeder <serial>").

## Part 2 — Split-broker bridging (if your HA MQTT differs)

If your HA MQTT integration points at a different broker than the petlibro stack
(Mosquitto addon), entities won't appear. Bridge them with
`tower/99-tower-ha-bridge.conf`:

```ini
connection tower-to-ha-broker
address <your-main-broker-ip>:1883
topic homeassistant/# both 0
topic plaf203/# both 0
topic petlibro_local/# out 0
cleansession true
notifications false
try_private false
```

Replace `YOUR_MAIN_BROKER_HOST` in the supplied config. Add bridge authentication
if your main broker requires it. Copy into `/share/mosquitto/` and enable `customize.active: true, folder: mosquitto`
in the Mosquitto addon options.

## Part 3 — Motion notifications (phone + watch, snapshot, tap-to-live)

The '54 emits `{"cmd":"DETECTION_EVENT","type":"MOTION",...}` — the addon ignores it,
so we bridge it to HA:

1. Ensure `topic dl/+/+/device/# out 0` is present (already in the supplied file) to the bridge conf (so detection events reach
   your main broker) and restart the Mosquitto addon.
2. Edit camera and notify targets in
   `tower/automations/motion_snapshot_automation.example.json` (remove the watch
   action if unused). Ensure `/media` is writable and allowed by HA's
   `allowlist_external_dirs` for camera snapshots. Export `HASS_TOKEN` (a long-lived
   admin token), `HA_URL` (e.g. `http://homeassistant.local:8123`), and
   `PETLIBRO_SERIAL` (actual serial), then run
   `python3 tower/automations/make_motion_automation.py`. It creates/updates
   `feeder_motion_snapshot`, with a 120 s cooldown and tap target
   `/tower-feeder/tower`. Reload automations in HA if needed.
3. Test: publish a fake motion payload
   `{"cmd":"DETECTION_EVENT","type":"MOTION","ts":<ms>}` to
   `dl/PLAF203/<serial>/device/event/post` and watch the notification arrive.

> Gotchas baked into the builder: the condition must string-match **compact JSON**
> (spaced test payloads fail silently), `camera.snapshot` needs an explicit
> `target.entity_id`, and the `payload_json` form doesn't evaluate on some HA
> versions — the builder uses the proven form.

## Part 4 — 30-day local video recording (optional, second host)

On an always-on Linux box with your backups pool mounted:

```bash
sudo env PETLIBRO_SERIAL=EXAMPLE_SERIAL PETLIBRO_STREAM=rtsp://YOUR_CAMERA_HOST:8554/YOUR_STREAM \
  bash scripts/install-tower-recorder.sh <path-to-clips-dir> YOUR_BROKER_HOST
```

Build the Rust workspace first (see README). Deploys `petlibro-cam-recorder` (systemd `petlibro-cam-recorder`) — it listens
on your broker for motion/feeding events and records 3-minute H.264 clips with
ffmpeg from the camera's RTSP stream, into `<dir>/YYYY-MM-DD/`. Retention:
`petlibro-cam-retention.sh` (+ daily timer) deletes clips older than 30 days (GNU find `-mtime +30`: at least 31 complete days).
Use a systemd Debian/Ubuntu host, Python 3.9+ and pip. The installer installs
ffmpeg and paho-mqtt 2.x if needed. It prompts for MQTT credentials accepted by
your broker (not necessarily the feeder's credentials), stored in `/root/petlibro-cam-creds.json` (0600 — the install script prompts).

> The installer writes `/etc/default/petlibro-cam` (0600): `PETLIBRO_CLIPS_DIR`,
> `PETLIBRO_BROKER`, `PETLIBRO_SERIAL`, `PETLIBRO_STREAM`. Optional runtime
> `PETLIBRO_CREDS` overrides the credential file. MQTT port is 1883. Clips paths
> must contain only letters, digits, slash, underscore or hyphen. RTSP URLs cannot
> contain whitespace, quotes, backslashes or percent escapes. Replace all examples.
> Mount external storage before installation; configure its persistent mount.
> Hardened behaviors: subscription renews on reconnect (a clean-session reconnect
> silently kills a plain subscribe), clips are written `.part` then atomically
> renamed (in-flight files are never mistaken for complete), less than 100 MiB free prevents starting a clip, systemd requires the clips
> filesystem at service startup, and only this tool's own files are ever pruned by retention.

## Part 5 — The dashboard

`tower/dashboard/build_dashboard.py` builds the full Lovelace dashboard via the HA
WebSocket API: live camera, feed controls, schedules, device recording controls,
and detection settings (not external recorder status). Install `websockets` in
your Python environment. Export `HASS_TOKEN`, `HA_URL`, and `PETLIBRO_SERIAL` as
above, then run `python3 tower/dashboard/build_dashboard.py`. It creates or
replaces the `tower-feeder` dashboard config. Its live view is
`/tower-feeder/tower`. Check generated entity IDs against your addon and adjust
`camera.tower_feeder` if needed. Snapshot files are not automatically pruned.

## Day-2 operations

- **After addon updates/restarts:** re-run the host command above (or arrange an
  external SSH scheduler).
- **Camera slow to start:** the first pull cold-starts the feeder's P2P link —
  ~10–20 s to first frame is normal.
- **Feeder seems offline in the app:** expected — it lives locally now.
- **All entities unavailable:** check the feeder's MQTT actually connects to the
  addon's broker first (see `docs/04-troubleshooting.md`), not the addon.
