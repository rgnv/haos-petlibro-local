# 02 — Topic catalog

Every topic observed, both device families. Topics follow
`dl/<PRODUCT>/<SERIAL>/device/<channel>/<post|sub>`.

- `/post` — device → cloud (the device publishes here; broker PUBACKs QoS 1 only).
- `/sub`  — cloud → device (the device subscribes; cloud PUBLISHes commands/answers).
- `+` wildcard = the device receives on ANY subchannel (PLAF109 subscribes once
  with `+/sub`; PLAF203 subscribes per-channel to the seven listed below).

## PLAF203 "Tower" (camera feeder)

Device subscribes (QoS 1): `ntp/sub`, `ota/sub`, `broadcast/sub`, `config/sub`,
`event/sub`, `service/sub`, `system/sub`.

| Topic | Dir | Channel | Carries (observed) |
|---|---|---|---|
| `…/device/heart/post` | D→C | heart | `HEARTBEAT` ~every 100 s |
| `…/device/ntp/post` | D→C | ntp | `NTP` request; `NTP_SYNC` response to a cloud push |
| `…/device/ntp/sub` | C→D | ntp | `NTP` answer; `NTP_SYNC` timezone push |
| `…/device/event/post` | D→C | event | `DEVICE_START_EVENT`, `ATTR_PUSH_EVENT`, `GET_FEEDING_PLAN_EVENT`, `GRAIN_OUTPUT_EVENT`, `DETECTION_EVENT`, `DEVICE_LOG_REPORT_EVENT` |
| `…/device/event/sub` | C→D | event | JSON echoes of allowlisted events (not detection/log events); feeder-side `GET_FEEDING_PLAN_EVENT` requests |
| `…/device/service/post` | D→C | service | JSON acks for service requests (`code`, `msgId`) |
| `…/device/service/sub` | C→D | service | `DEVICE_CONFIG_SYNC`, `FEEDING_PLAN_SERVICE`, `MANUAL_FEEDING_SERVICE`, `ATTR_SET_SERVICE`, `ATTR_GET_SERVICE`, `GET_CONFIG`, `WIFI_RECONNECT_SERVICE`, `INITIALIZE_SD_CARD_SERVICE`, `DEVICE_REBOOT`, `RESTORE` |
| `…/device/ota/sub` | C→D | ota | `OTA_UPGRADE` / OTA control (observed during flash) |
| `…/device/broadcast/sub` | C→D | broadcast | no payloads observed |
| `…/device/config/sub` | C→D | config | no payloads observed (device's own config report goes `config/post` after `GET_CONFIG`) |
| `…/device/system/post` | D→C | system | `BINDING` (registration announcement; echoes back on system/sub) |
| `…/device/system/sub` | C→D | system | BINDING echo, other system acks |

## PLAF109 "Polar" (wet feeder)

Device subscribes (QoS 1): **`device/+/sub`** (single wildcard subscription).

| Topic | Dir | Carries (observed) |
|---|---|---|
| `…/device/heart/post` | D→C | `HEARTBEAT` ~every 100 s (count, rssi, wifiType, temperature) |
| `…/device/ntp/post` | D→C | `NTP` request |
| `…/device/ntp/sub` | C→D | `NTP` answer (timezone payload) |
| `…/device/event/post` | D→C | `DEVICE_START_EVENT`, `ATTR_PUSH_EVENT`, `WET_GRAIN_OUTPUT_EVENT`, `GET_SOME_ATTR_SERVICE` (reply), `DEVICE_LOG_REPORT_EVENT` |
| `…/device/event/sub` | C→D | JSON echoes (code:0 + linkId) for the event set |
| `…/device/service/post` | D→C | replies to service requests: `WET_GRAIN_FEEDING_PLAN_SERVICE` ack (with plans:[] of accepted ids), `DEVICE_CONFIG_SYNC` ack, `ATTR_SET_SERVICE` ack, `GET_SOME_ATTR_SERVICE` ack — **no JSON echo comes back for these** |
| `…/device/service/sub` | C→D | `DEVICE_CONFIG_SYNC` (broker pin), `WET_GRAIN_FEEDING_PLAN_SERVICE` (plan writes AND feed commands), `ATTR_SET_SERVICE`, `GET_SOME_ATTR_SERVICE`, `GET_FEEDING_PLAN_EVENT` (observed sent to event/sub too — wildcard device accepts either) |
| `…/device/config/post` | D→C | `GET_CONFIG` reply (identity: pid/mac/hw/sw) |

## Cross-family notes

- **Channel semantics:**
  - `heart` — liveness only.
  - `ntp` — clock discipline; device asks, cloud answers; cloud can force-push
    `NTP_SYNC`.
  - `event` — asynchronous device news (boot, data pushes, progress, detections).
  - `service` — request/response operations (plans, feeds, config writes).
  - `config` — identity/config report.
  - `system` — registration (`BINDING`) on PLAF203; no traffic seen on PLAF109.
  - `ota` — firmware update control channel.
- **QoS:** device → cloud JSON usually QoS 1; log reports can use QoS 0; cloud → device JSON at QoS 0 (with
  occasional QoS 1). Retained: not used in normal traffic.
- **Wildcard acceptance:** because PLAF109 subscribes `+/sub`, publishing a
  PLAF109 command to *any* `…/device/<x>/sub` topic reaches the device (the
  vendor web app and cloud both use this looseness). Canonical channel names
  above are still the ones the cloud uses.
