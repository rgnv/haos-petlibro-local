# 03 — Message catalog (every JSON message)

Field-by-field reference for every message type observed on both families.
Timestamps (`ts`) are epoch **milliseconds**. Device clocks drift (observed
~20 min behind) — never trust device `ts` for scheduling; match against
`msgId`/ordering instead.

Legend: **[req]** always present · **[opt]** sometimes · **[echo]** = JSON echo
reply expected on the matching `/sub` · **no reply** = MQTT PUBACK only for QoS 1.

---

## Common / cross-family

### HEARTBEAT — device → `heart/post` — no reply
| Field | Type | Notes |
|---|---|---|
| `cmd` | string | `"HEARTBEAT"` |
| `count` | int | monotonic since boot; **resets to 1 after a reboot** (useful to spot restarts) |
| `rssi` | int | WiFi signal dBm (e.g. −38) |
| `wifiType` | int | 1 = WiFi (observed) |
| `temperature` | float | device-internal °C (PLAF109: fridge area) |
| `ts` | int ms | |

```json
{"cmd":"HEARTBEAT","count":94045,"rssi":-38,"wifiType":1,"temperature":22.95,"ts":1791600572077}
```

### NTP — device → `ntp/post`; answer on `ntp/sub`
Device asks; the cloud answers (see `04-flows.md` §NTP). Request:

```json
{"cmd":"NTP","ts":1791596645122}
```

Answer (all fields required; `timezone` in hours, offsets in seconds):

```json
{"cmd":"NTP","ts":1791596882694,"code":0,"calibrationTag":false,
 "timezoneOffsetSeconds":-25200,"nextDSTOffsetSeconds":-28800,
 "nextDSTTransitionTs":1793523600000,"secondNextDSTOffsetSeconds":-25200,
 "secondNextDSTTransitionTs":0,"timezone":-7}
```

Note: `calibrationTag:false` observed on devices that ignore calibration anyway
(their clocks drifted regardless). Build the DST transitions for the user's
timezone (see `polar/polar_local_cloud.py` `ntp_payload()` — scans 400 days and
locates boundaries to the second).

### DEVICE_CONFIG_SYNC — cloud → `service/sub` — ack `service/post` (no echo)
The cloud's broker-address push; **this is the "pin" command for local operation**.

| Field | Type | Notes |
|---|---|---|
| `cmd` | string | `"DEVICE_CONFIG_SYNC"` |
| `ts` | int | |
| `msgId` | string | uuid-ish |
| `mqttAddr` | array | `[{"host":"…","port":1883}]` — where the device should connect next |
| `httpsAddr` | string | e.g. `api.us.petlibro.com` |
| `linkId` | string | cloud session tag |

Device acks with `{"cmd":"DEVICE_CONFIG_SYNC","msgId":"<same>","code":0,"ts":…}`.
The device can persist `mqttAddr` across ordinary reboots. Verify reconnection;
factory resets, reprovisioning and firmware changes may alter it.

### DEVICE_LOG_REPORT_EVENT — device → `event/post` — no reply
Internal debug log dump. Fields: `cmd`, `msgId`, `logs[]` (each `{type, content, time}`),
`ts`. Observed log lines: `"io:10 state:1"` / `"io:19 state:0"` (GPIO/sensor
events), `"Microtime=NNN"` (motor timing), `type:"net"` → `"discon=NNN"`,
`type:"mqtt"` → `"MQTT_CONNECT_ERR"`. Qos 0. Useful for debugging connectivity.

### GET_CONFIG — cloud → `service/sub` — device reports on `config/post`
```json
{"cmd":"GET_CONFIG","ts":0,"msgId":"…"}
```
Device → `config/post`:
```json
{"cmd":"GET_CONFIG","msgId":"<device-generated-msgId>","pid":"PLAF109",
 "mac":"<hex>","hardwareVersion":"5.2.0","softwareVersion":"2.0.32","ts":…}
```

### ATTR_GET_SERVICE — cloud → `service/sub` — device reports attrs (no echo)
`{"cmd":"ATTR_GET_SERVICE","ts":…,"msgId":"…"}` → device replies on
`event/post` with a full attribute dump (`cmd: ATTR_GET_SERVICE`, `code:0`,
attrs, `ts`). See `05-fields.md` for the attribute list.

### ATTR_SET_SERVICE — cloud → `service/sub` — ack (no echo)
Set one or more attributes: `{"cmd":"ATTR_SET_SERVICE","ts":…,"msgId":"…","<attrKey>":<value>,…}`
Device acks on `service/post` (`code:0`) and pushes the changed attr on
`event/post` as an `ATTR_PUSH_EVENT`.

---

## PLAF203 "Tower"

### DEVICE_START_EVENT — device → `event/post` — **[echo]**
Boot/reconnect announcement.

| Field | Type | Example |
|---|---|---|
| `cmd` | string | `"DEVICE_START_EVENT"` |
| `msgId` | string | `<SERIAL><ms>` device-generated |
| `success` | bool | `true` |
| `pid` | string | `"PLAF203"` |
| `uuid` | string | (fw-dependent presence) |
| `mac` | string | hex, no colons |
| `wpa3` | bool | |
| `hardwareVersion` / `softwareVersion` | string | `"3.1.54"` |
| `tutkP2pRegion` | string | camera P2P region |
| `restartReason` | string | |
| `ts` | int | |

### ATTR_PUSH_EVENT — device → `event/post` — **[echo]**
Attribute pushes (also used for attr *responses*). Fields = the attribute itself
plus `cmd`/`msgId`/`ts` (no `code` on pushes; responses carry `code:0`).
Larger pushes carry e.g. `platePosition`, feeding settings; the full set for a
device arrives after a fresh connect (watch the log — the first minutes show
every stored setting).

### GET_FEEDING_PLAN_EVENT — both directions
- Device → `event/post`:**[echo]** (asks for plans) — `{"cmd":"GET_FEEDING_PLAN_EVENT","msgId":…}`
- Cloud → `event/sub`: plan data; the device's response to a cloud request comes
  as `event/post` with the plan payload.

### FEEDING_PLAN_SERVICE — cloud → `service/sub` — ack `service/post` (no echo)
Replaces/syncs the complete feeding plan collection. Dry-food plans:

| Field (per plan) | Type | Notes |
|---|---|---|
| `planId` | int | unique; **666666666 = the manual/one-shot slot** |
| `executionTime` | "HH:MM" | local |
| `repeatDay` | int[] | 1..7, Monday=1 |
| `enableAudio` | bool | call-to-meal sound |
| `audioTimes` | int | sound repeats |
| `grainNum` | int | portions per execution |
| `syncTime` | int | ms |

Device response: `{"cmd":"FEEDING_PLAN_SERVICE","code":0,"msgId":…,"plans":[{"planId":…,"syncTime":…}],"ts":…}`.
`plans: []` = delete all plans.

### MANUAL_FEEDING_SERVICE — cloud → `service/sub` — ack (no echo)
Feed now: `{"cmd":"MANUAL_FEEDING_SERVICE","ts":…,"msgId":…,"grainNum":<portions>}`
Device acks; dispense progress arrives separately as `GRAIN_OUTPUT_EVENT`
(same `msgId`).

### GRAIN_OUTPUT_EVENT — device → `event/post` — **[echo]**
Dispense progress. Fields: `cmd`, `msgId`, `grainNum`, `status`/progress values,
`ts`. (Two events per manual feed observed: start/complete.)

### DETECTION_EVENT — device → `event/post` — no JSON echo
```json
{"cmd":"DETECTION_EVENT","type":"MOTION","ts":1791593430164}
```
The motion signal used for local notifications. **Compact JSON** — textual
matchers must not expect spaces.

### BINDING — device → `system/post` — **[echo on system/sub]**
Registration announcement (contains member/account id + device identity).
**Critical:** newer firmware (≥3.1.49) waits for the echo here before sending
DEVICE_START_EVENT — the #1 cause of "handshake stalls". Echo:
`{"cmd":"BINDING","msgId":"<same>","code":0}` on `system/sub`.

### Others (service channel)
`WIFI_RECONNECT_SERVICE`, `INITIALIZE_SD_CARD_SERVICE`, `DEVICE_REBOOT`,
`RESTORE` — control ops; same request/ack shape (`cmd`/`ts`/`msgId` → `code:0`).
OTA family on `ota/sub`: `OTA_UPGRADE` (cloud→device, starts a firmware pull),
then device reports `OTA_PROGRESS`/`OTA_INFORM` on event topics.

---

## PLAF109 "Polar"

### DEVICE_START_EVENT — device → `event/post` — **[echo]**
```json
{"cmd":"DEVICE_START_EVENT","msgId":"<SERIAL><ms>","success":true,"pid":"PLAF109",
 "mac":"<hex>","hardwareVersion":"5.2.0","softwareVersion":"2.0.32","ts":…}
```

### ATTR_PUSH_EVENT — device → `event/post` — **[echo]**
Same concept as PLAF203. Attribute keys observed (wet feeder — see `05-fields.md`):
`powerMode`, `powerType`, `electricQuantity`, `wifiSsid`, `closeDoorTime`,
`volume`, `enableLight`, `lightSwitch`, `lightAgingType`, `enableSound`,
`soundSwitch`, `soundAgingType`, `doorCheckSignalTime`, `doorNotcheckSignalTime`,
`doorStuckCurrent`, `plateStuckCurrent`, `irSensorIdenTimeout`,
`idenFirstPlateTime`, `ringerMode`, `ringerInterval`, `ringerDuration`.

### WET_GRAIN_FEEDING_PLAN_SERVICE — cloud → `service/sub` — ack (no echo)
Both the **plan sync** and the **feed command**. Feed = plan-write with
`feedNowState: true` to the manual slot:

```json
{"cmd":"WET_GRAIN_FEEDING_PLAN_SERVICE","ts":1791602205778,"msgId":"…",
 "plans":[{"planId":666666666,"executionTime":"20:16","executionDay":"2026-10-09",
           "plate":1,"feedingDuration":240,"feedNowState":true}],
 "linkId":"…"}
```

| Field (per plan) | Type | Notes |
|---|---|---|
| `planId` | int | `666666666` = manual slot (one-shot feeds) |
| `executionTime` | "HH:MM" | time of day |
| `executionDay` | "YYYY-MM-DD" | date |
| `plate` | int | 1..3 (rotating carousel; 3 seen in cloud data) |
| `feedingDuration` | int s | door-open window |
| `feedNowState` | bool | **true = execute immediately** (omit for normal scheduled plans) |

Device ack: `{"cmd":"WET_GRAIN_FEEDING_PLAN_SERVICE","code":0,"msgId":…,
"plans":[{"planId":666666666,"syncTime":0}],"ts":…}` — `code:0` = accepted.
(`code:2099 / "FeedPlanErro"` = rejected — wrong shape, or a feed is already
running. Bare requests without a `plans` array also return 2099.)

### WET_GRAIN_OUTPUT_EVENT — device → `event/post` — **[echo]**
Feed progress stream (device-generated `msgId` per feed):

```json
{"cmd":"WET_GRAIN_OUTPUT_EVENT","msgId":"<device-generated-msgId>","finished":false,
 "feedingDuration":240,"planId":666666666,"plate":1,
 "execTime":1791602206064,"execStep":"GRAIN_THAW","ts":…}
```

`execStep` sequence observed: `GRAIN_THAW` → `GRAIN_START` → `OPEN_DOOR` (→ door
close after the duration/when the animal finishes; `finished:true` marks the
terminus). The cloud echoes each step with `{"cmd":"WET_GRAIN_OUTPUT_EVENT",
"msgId":"<same>","code":0,"execStep":"<same>","linkId":…}` on `event/sub`.

### GET_SOME_ATTR_SERVICE — cloud → `service/sub` — reply via ATTR_PUSH (no echo)
Server-side targeted attr read: `{"cmd":"GET_SOME_ATTR_SERVICE","attrKeys":["platePosition"],
"ts":…,"msgId":…}` → device pushes the requested values as `ATTR_PUSH_EVENT`
(`event/post`) and acks with `code:0`.

---

## Echo rules (the loop hazard)

| Device post | JSON reply | Topic |
|---|---|---|
| `ntp/post` (NTP) | NTP answer (full tz payload) | `ntp/sub` |
| `event/post`: `DEVICE_START_EVENT`, `ATTR_PUSH_EVENT`, `GET_FEEDING_PLAN_EVENT`, `GRAIN_OUTPUT_EVENT`/`WET_GRAIN_OUTPUT_EVENT` | `{cmd,msgId,code:0,linkId}` (+`execStep` for grain output) | `event/sub` |
| `event/post`: `DETECTION_EVENT`, `DEVICE_LOG_REPORT_EVENT` | none | — |
| `service/post` (all acks/replies) | **none** — echoing re-triggers the command on the device (infinite `2099` loop observed) | — |
| `config/post` | none | — |
| `heart/post` | none (PUBACK only) | — |

Self-guard for responders: **drop every inbound message that contains `linkId`**
(device posts never have it; some cloud-side messages do — this also stops
echo loops structurally).
