# Installer patches — making the bootstrap OTA work with firmware 3.1.49+

The upstream installer's bootstrap MQTT server was validated against firmware
3.1.48. Newer firmware (3.1.54 observed) **stalls mid-handshake** unless the
bootstrap server reproduces additional cloud behaviors. `our-changes.diff` is the
complete, cumulative diff we run in production (against the upstream tree at the
commit we flashed with) — every hunk is commented `LOCAL PATCH#` inline.

## What the patches do (in handshake order)

| Patch | Where | What it does | Why |
|---|---|---|---|
| (keepalive) | mqtt_server.py | Drops the 30 s handshake timeout for the boot session | The '54 handshake takes longer than '48; the timeout killed sessions mid-dance |
| PATCH4 | `_Handler` | After CONNACK: push `FEEDING_PLAN_SERVICE` (empty `plans`) immediately; answer the feeder's `NTP` clock-check on `ntp/sub`; handle MQTT PINGREQ/PINGRESP + graceful DISCONNECT | The '54's boot gate: it waits for the plan push before proceeding; PINGREQ handling keeps the session alive through the dance |
| PATCH7 | handler | Send `NTP_SYNC` **after** the plan-service ack (not before) | Cloud order observed in captures: plan → plan-ack → NTP_SYNC. Sending early confused the '54 |
| PATCH8 | handler | On the `NTP_SYNC` ack, proactively send a full **NTP answer** on `ntp/sub` | The '54 waits for the server-side answer to its earlier NTP request; without it the next step never fires |
| **PATCH9** | handler | **Reply to the feeder's `BINDING` publish** (on `system/post`) with the standard echo `{"cmd":"BINDING","msgId":<same>,"code":0}` on `system/sub` | **The gate that unlocked everything.** Newer firmware announces BINDING (account registration) and refuses to send `DEVICE_START_EVENT` until the server acknowledges. Without this, the OTA never starts — the single most important patch |
| (timeout) | `run()` | `wait_for(device_start_ready, 600)` | Extended wait for the slow handshake |

Sequence with all patches (observed success): CONNECT → CONNACK → SUBSCRIBEs →
plan push → plan-ack → NTP_SYNC → NTP_SYNC-ack → proactive NTP answer →
**BINDING → BINDING echo → DEVICE_START_EVENT** → OTA family → `OTA_UPGRADE` →
device downloads payload (HTTP 18080) → `OTA_PROGRESS` ×N → `OTA_INFORM` → flash.

## Applying

```bash
# inside a checkout of github.com/tannerln7/Petlibro-Home-Assistant
git apply /path/to/our-changes.diff
# adjust the timezone in build_ntp_sync_payload() if you're not US/Pacific
```

Notes:
- The diff's timezone payload is hardcoded to `America/Los_Angeles` (our
  deployment). For other timezones, change the `ZoneInfo("America/Los_Angeles")`
  line — the DST-transition search logic is timezone-generic.
- These patches target the **bootstrap phase** (the installer's temporary MQTT
  server). The permanent addon-side fixes live in `tower/patch-kit/`
  (`binding_patch.py`, `server_aid_daemon.py`).
- Verify after flashing: the installer log ends with the endpoint restore
  (`mqttAddr` pointing at YOUR broker) and the feeder connects to it.

## Provenance

Developed against firmware 3.1.54 on a real PLAF203 (2026-10). Upstream project:
[tannerln7/Petlibro-Home-Assistant](https://github.com/tannerln7/Petlibro-Home-Assistant)
— send the core fixes upstream if they adopt '54 support; this diff exists to
unblock '54 users today.
