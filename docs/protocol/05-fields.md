# 05 — Master field dictionary

Every key observed in any message, with type, values seen, and meaning. Grouped
by scope. "[both]" = appears on both families.

## Protocol envelope fields [both]

| Key | Type | Values / example | Meaning |
|---|---|---|---|
| `cmd` | string | `"HEARTBEAT"`, `"NTP"`, `"DEVICE_START_EVENT"`, … | message type — drives all dispatch |
| `ts` | int (ms) | 1791600572077 | sender's clock at send time. **Device clocks drift** (~20 min observed); do not schedule off it |
| `msgId` | string | `<uuid>` (cloud) / `<SERIAL><ms>` (device) | request↔response correlation; echo it verbatim |
| `code` | int | `0` = OK; `2099` = `"FeedPlanErro"` (PLAF109 plan/feed rejection) | application result |
| `msg` | string | `"FeedPlanErro"` | human-readable error companion to nonzero `code` |
| `linkId` | string | 32-hex | cloud session tag on cloud→device messages (**never** in device posts; use as a self-echo discriminator) |
| `success` | bool | `true` | boot/operation success flag |
| `count` | int | 94045 | heartbeat counter since boot; resets on reboot |
| `rssi` | int (dBm) | −38 | WiFi signal |
| `wifiType` | int | 1 | 1 = WiFi |
| `temperature` | float (°C) | 22.95 | device-internal sensor (PLAF109: food compartment) |
| `finished` | bool | false | progress-complete flag (output events) |
| `execStep` | string | `GRAIN_THAW`, `GRAIN_START`, `OPEN_DOOR` | feed stage (PLAF109 output events) |
| `execTime` | int ms | 1791602206064 | when a feed step executed |

## Identity / boot [both]

| Key | Type | Example | Meaning |
|---|---|---|---|
| `pid` | string | `"PLAF203"`, `"PLAF109"` | product id — **the family discriminator** |
| `uuid` | string | | device uuid (fw-dependent) |
| `mac` | string | `"<MAC>"` | WiFi MAC, hex without separators |
| `wpa3` | bool | | WPA3 capability (PLAF203) |
| `hardwareVersion` | string | `"5.2.0"` | |
| `softwareVersion` | string | `"2.0.32"`, `"3.1.54"` | firmware |
| `tutkP2pRegion` | string | | camera P2P relay region (PLAF203) |
| `restartReason` | string | | last reset reason (PLAF203) |
| `hardwareVersion`/`softwareVersion` | string | | also in `GET_CONFIG` replies |

## Config sync [cloud → device]

| Key | Type | Example | Meaning |
|---|---|---|---|
| `mqttAddr` | array | `[{"host":"mqtt.us.petlibro.com","port":1883}]` | **broker address the device should use** — point at your host to go local |
| `httpsAddr` | string | `"api.us.petlibro.com"` | cloud API host the device uses for HTTPS-side features |

## NTP answer [cloud → device on ntp/sub]

| Key | Type | Example | Meaning |
|---|---|---|---|
| `calibrationTag` | bool | false | recalibration request flag (observed ignored) |
| `timezoneOffsetSeconds` | int | −25200 | current UTC offset (PDT = −25200) |
| `nextDSTOffsetSeconds` | int | −28800 | offset after the next transition (PST) |
| `nextDSTTransitionTs` | int ms | 1793523600000 | next transition instant (UTC) |
| `secondNextDSTOffsetSeconds` | int | −25200 | offset after the one beyond that |
| `secondNextDSTTransitionTs` | int ms | … | that transition instant |
| `timezone` | number (hours) | −7 | current offset; implementation supports fractional hours |

## PLAF203 attributes & plan fields

| Key | Type | Values | Meaning |
|---|---|---|---|
| `grainNum` | int | 1..N | portions (manual feed / per-plan amount) |
| `planId` | int | unique; `666666666` = manual slot | plan identity |
| `executionTime` | "HH:MM" | `"11:00"` | local time of day |
| `repeatDay` | int[] | `[7,1,2,3,4,5,6]` | weekdays, Monday=1 |
| `enableAudio` | bool | | call-to-meal sound per plan |
| `audioTimes` | int | 2 | sound repeats |
| `syncTime` | int ms | | plan sync marker returned in acks |
| `platePosition` | int | 1 | carousel position (shared name with PLAF109!) |
| `type` | string | `"MOTION"` | DETECTION_EVENT subtype |

(PLAF203 attribute pushes carry the feeder's settings — feeding plan state,
audio, light, etc. — in `ATTR_PUSH_EVENT` payloads; the field names mirror the
HA integration's entity set.)

## PLAF109 attributes (wet feeder)

From `ATTR_PUSH_EVENT` / `ATTR_GET_SERVICE` dumps:

| Key | Type | Values seen | Meaning |
|---|---|---|---|
| `powerMode` | int | 1 | power mode |
| `powerType` | int | 1 | power source type |
| `electricQuantity` | int | 0 | battery percentage (0 with batteryState low) |
| `wifiSsid` | string | | joined WiFi SSID (**personal — redact in shares**) |
| `closeDoorTime` | int s | 30 | auto-close delay |
| `volume` | int | 50 | speaker volume |
| `enableLight` / `lightSwitch` | bool | false | light feature + state |
| `lightAgingType` | int | 1 | light aging curve |
| `enableSound` / `soundSwitch` | bool | true | sound feature + state |
| `soundAgingType` | int | 1 | |
| `doorCheckSignalTime` | int s | 5 | door-sensor check interval |
| `doorNotcheckSignalTime` | int s | 10 | |
| `doorStuckCurrent` / `plateStuckCurrent` | int | 400 | motor stall-current limits |
| `irSensorIdenTimeout` | int s | 2 | IR identification timeout |
| `idenFirstPlateTime` | int ms | 450 | first-plate identification time |
| `ringerMode` | string | `"NORMAL"` | bell mode |
| `ringerInterval` / `ringerDuration` | int s | 2 / 10 | bell pattern |
| `platePosition` | int | 1 | current carousel position |
| `vacuumState` / `vacuumMode` | bool/str | true / `"NORMAL"` | vacuum-seal subsystem (list API) |
| `pumpAirState` | bool | true | air pump (list API) |
| `weightPercent` / `weight` | float | 0 | food weight |
| `waterBoxStatus` | int | −1 | water box sensor |
| `surplusGrain` | bool | true | food present |
| `batteryState` | string | `"low"` | battery health (list API) |
| `cameraAuthInfo` | string | token | camera auth token (list API; device-family token, not MQTT) |

## Feeding plan fields (PLAF109)

| Key | Type | Values | Meaning |
|---|---|---|---|
| `planId` | int | `666666666` = manual | plan identity |
| `executionTime` / `executionDay` | "HH:MM" / "YYYY-MM-DD" | | when |
| `plate` | int | 1..3 | which carousel plate |
| `feedingDuration` | int s | 240 (60 short test) | door-open window |
| `feedNowState` | bool | true | **execute immediately** (feed command) |
| `state` | int | 6 (cloud-side plan records) | plan state enum |
| `feedAgainExecutionEndTime` | string | | re-offer window (cloud plan JSON) |
| `templateGroupId` | string | uuid | cloud plan grouping |

## Logs / diagnostics

| Key | Type | Values | Meaning |
|---|---|---|---|
| `logs` | array | `[{type, content, time}]` | DEVICE_LOG_REPORT_EVENT body |
| `type` | string | `"sensor"`, `"net"`, `"mqtt"` | log line class |
| `content` | string | `"io:10 state:1"`, `"Microtime=498"`, `"discon=8"`, `"MQTT_CONNECT_ERR"` | raw log line |
| `time` | int ms | | when logged |
| `discon` | (in content) | 8, 202, 205 | disconnect reason codes |

## Redaction quick-list (for capture sharing)

Always redact: CONNECT username/password, `client_id` (serial), `mac`, `uuid`,
`wifiSsid`, member/user ids in BINDING, plan `templateGroupId` (account-scoped),
and any tokens (`cameraAuthInfo` etc.). The sanitizer handles the common shapes —
review its output before sharing.
