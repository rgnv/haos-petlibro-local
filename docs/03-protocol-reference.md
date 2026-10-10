# Protocol reference — Petlibro MQTT (PLAF203 & PLAF109)

Everything below is shared infrastructure knowledge (same for every Petlibro user):
cloud endpoints, topic structures, message shapes, and behaviors — captured from
real device conversations. Credentials are **per device** and are not (and cannot
be) included; capture your own with the tooling in this repo.

## Shared cloud infrastructure

- **Cloud MQTT broker:** `mqtt.us.petlibro.com:1883` — **plain MQTT, no TLS**.
  Resolves to a rotating set of load-balanced IPs (multiple A records). Devices
  authenticate with username/password visible in the plaintext CONNECT packet.
- **Cloud REST API:** `api.us.petlibro.com` (used by the app and the HA cloud
  integration for schedules, device info, and command initiation).
- **Device families:** topic namespace is `dl/<PRODUCT>/<SERIAL>/device/...` —
  `PLAF203` for the Tower camera feeder, `PLAF109` for the Polar wet feeder.
  Each device family has a `pid` field (`PLAF203`, `PLAF109`) in its boot event.

## Common device behavior

- **Every device publish gets an MQTT PUBACK** (QoS 1 posts) — transport layer.
- **Application-level replies** (the vendor cloud's pattern):
  - `event/post` events get a JSON echo **on the matching `/sub` topic**:
    `{"cmd": <same>, "msgId": <same>, "code": 0, "linkId": <new uuid>}`
    (observed for `DEVICE_START_EVENT`, `ATTR_PUSH_EVENT`, `GET_FEEDING_PLAN_EVENT`,
    `GRAIN_OUTPUT_EVENT`; `DEVICE_LOG_REPORT_EVENT` and `HEARTBEAT` get none).
  - **Service replies (`service/post`) get NO JSON echo** — echoing them can
    re-trigger the device (see the loop warning below).
  - `ntp/post` requests get an NTP answer on `ntp/sub` (fields below).
- **`linkId`** is a cloud-side session identifier attached to every cloud→device
  JSON message; values rotate per message. Device→cloud posts never carry it.
- **Heartbeat cadence** ~100 s (`count`, `rssi`, `wifiType`, `temperature`).
- **Keepalive** 100 s; the device maintains long-lived TCP sessions and is slow to
  notice short network blips (TCP retransmission survives ~15 min of silence).

## PLAF203 (Tower) essentials

- Device publishes: `device/{ntp,heart,event,service}/post`; subscribes
  `device/{ntp,ota,broadcast,config,event,service,system}/sub`.
- Commands of interest: `FEEDING_PLAN_SERVICE` (plans array), `MANUAL_FEEDING_SERVICE`
  (`grainNum`), `NTP_SYNC` (timezone push), `DEVICE_CONFIG_SYNC` (broker address
  push: `mqttAddr:[{host,port}]` + `httpsAddr`), `BINDING` (device registration
  announcement — **must be echo-replied or newer firmware stalls**).
- Events: `DEVICE_START_EVENT`, `ATTR_PUSH_EVENT` (attrs like feeding plan,
  settings), `GRAIN_OUTPUT_EVENT` (dispense progress; `grainNum` + status),
  `DETECTION_EVENT` (`type:"MOTION"` — the motion signal used for notifications).
- Video: the camera streams over TUTK/P2P (not MQTT); the addon's go2rtc handler
  surfaces it as RTSP `rtsp://<addon-host>:8554/<stream>`.

## PLAF109 (Polar) essentials

- Device publishes `device/{heart,ntp,event,service,config}/post`; subscribes
  **`device/+/sub`** (a wildcard — it accepts commands on any subtopic).
- **Feed = a plan write with `feedNowState:true` to the magic manual slot:**
  ```json
  {"cmd":"WET_GRAIN_FEEDING_PLAN_SERVICE","ts":<ms>,"msgId":"<id>",
   "plans":[{"planId":666666666,"executionTime":"HH:MM","executionDay":"YYYY-MM-DD",
             "plate":N,"feedingDuration":<seconds>,"feedNowState":true}]}
  ```
  → device ACKs `code:0`, then streams `WET_GRAIN_OUTPUT_EVENT` progress on
  `event/post`: `execStep` transitions `GRAIN_THAW → GRAIN_START → OPEN_DOOR → …`
  with `finished` flag, `plate`, `feedingDuration` echoed. UUID `planId 666666666`
  is the device's manual-feed slot.
- Regular feeding plans use the same command with real plan objects (its own empty
  default reports as `{"planId":666666666,"syncTime":0}`).
- Other accepted commands: `ATTR_GET_SERVICE` (attr dump), `GET_CONFIG` (identity),
  `DEVICE_CONFIG_SYNC` (broker pin — how you make it local permanently),
  `GET_FEEDING_PLAN_EVENT`. Unknown commands are silently ignored (no error) —
  don't expect a rejection to debug with.
- **NTP answer fields** (device ← cloud on `ntp/sub`): `cmd:"NTP"`, `ts`, `code:0`,
  `calibrationTag:false`, `timezoneOffsetSeconds`, `nextDSTOffsetSeconds`,
  `nextDSTTransitionTs`, `secondNextDSTOffsetSeconds`, `secondNextDSTTransitionTs`,
  `timezone` (hours). Device clock drifts (~20 min observed) and it ignores
  `calibrationTag:false`; don't rely on device timestamps for scheduling decisions.

## ⚠️ The echo loop — the one that bites

If you build your own cloud-side responder: **never echo `service/post` replies**,
and echo `event/post` only for the known event set. Echoing a
`WET_GRAIN_FEEDING_PLAN_SERVICE` reply (or any service reply) causes the device to
re-execute it as a fresh command with the same `msgId` → an infinite
`code:2099 FeedPlanErro` retry storm (~1.2 s cadence). Guard: skip any inbound
message containing `linkId` (device posts never carry it; echoes do).

## Discovery methodology (how to find YOUR device's unknowns)

1. Put a **transparent relay** between the device and the real cloud (this repo's
   `polar/polar_relay.py` pattern: DNAT the device's 1883 to your host, relay to
   `mqtt.us.petlibro.com:1883`, log both directions).
2. Trigger the action you want to learn (e.g. a manual feed) **via the vendor app
   or cloud API**.
3. Read the relay log — the cloud's exact command is there in plaintext.
4. Replay it locally. Verified end-to-end for the Polar feed command.
