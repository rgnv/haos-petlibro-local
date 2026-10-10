# 04 — Flows (end-to-end sequences)

Sequence diagrams of the important multi-message exchanges, as observed on real
devices. Arrows: `D` = device, `C` = cloud (or your local responder).

## 1. Boot / reconnect handshake (PLAF109 "Polar", the complete one)

Reported cloud exchange (not a verbatim trace emitted by the local service):

```
D → C   CONNECT            (inspect clean-session flag, keepalive 100 s)
C → D   CONNACK            session-present depends on stored session
D → C   SUBSCRIBE  device/+/sub (QoS 1)
C → D   SUBACK
C → D   DEVICE_CONFIG_SYNC request   (mqttAddr + httpsAddr + linkId)
D → C   DEVICE_START_EVENT           (pid, mac, hw, sw, success:true)
C → D   ★ event/sub echo of DEVICE_START_EVENT (code:0, linkId)
D → C   ATTR_PUSH_EVENT              (full attribute set)
C → D   ★ event/sub echo of ATTR_PUSH_EVENT
C → D   SERVICE/ATTR requests: GET_SOME_ATTR_SERVICE {attrKeys:[...]},
        ATTR_SET_SERVICE {attr: value}, DEVICE_CONFIG_SYNC …
D → C   service/post acks (code:0) + ATTR_PUSH responses for reads
C → D   ★ echoes on event/sub for each ATTR_PUSH
D → C   HEARTBEAT every ~100 s (no reply) + MQTT PINGREQ every 100 s
```

★ = the echo rules (see `03-messages.md`). The boot burst is dense (~20 messages
in 2 seconds) and repeats on every reconnect. A local responder replicating the
★ lines + NTP + config-sync is sufficient for normal operation (proven: an
unanswered service ack changes nothing; the device proceeds regardless).

## 2. Boot handshake (PLAF203 "Tower", fw ≥3.1.49 — the BINDING gate)

```
D → C   CONNECT / CONNACK (session-present)
D → C   SUBSCRIBE ×7 channels (ntp, ota, broadcast, config, event, service, system)
C → D   SUBACK ×7
D → C   BINDING on system/post          ← registration announcement
C → D   ★ system/sub echo (code:0)      ← MUST ANSWER or nothing proceeds
D → C   DEVICE_START_EVENT on event/post
C → D   ★ echo + (cloud) FEEDING_PLAN_SERVICE push on service/sub
D → C   service/post ack (code:0)
C → D   NTP_SYNC on ntp/sub (timezone push)
D → C   ntp/post ack
D → C   ATTR_PUSH_EVENT (settings) + GET_FEEDING_PLAN_EVENT
C → D   ★ echoes + plan data
(steady state: heartbeats + PINGREQs)
```

**The BINDING echo is the gate for fw 3.1.49+.** A cloud that doesn't answer it
sees the device sit silent forever after SUBSCRIBE — the exact failure mode the
Tower patches in this repo fix.

## 3. NTP exchange

```
D → C   {"cmd":"NTP","ts":…} on ntp/post               (periodic; ~hourly-ish)
C → D   full NTP answer on ntp/sub (offsets + 2 DST transitions + timezone-h)
        — no ack; device adopts silently
C → D   (optional, cloud-initiated) NTP_SYNC on ntp/sub with msgId
D → C   {"cmd":"NTP_SYNC","msgId":"<same>","code":0} on ntp/post  (ack)
```

Device clocks drift (observed ~20 min slow) and ignore `calibrationTag:false` —
treat device `ts` as informative only.

## 4. Feed cycle (PLAF109 — the full command→execution loop)

```
C → D   WET_GRAIN_FEEDING_PLAN_SERVICE on service/sub
          plans:[{planId:666666666, executionTime, executionDay,
                  plate, feedingDuration, feedNowState:true}]
D → C   service/post ack  {code:0, plans:[{planId:666666666, syncTime:0}]}
          (a running feed or malformed plan → code:2099 "FeedPlanErro" instead)
D → C   WET_GRAIN_OUTPUT_EVENT GRAIN_THAW   (event/post)
C → D   ★ echo (code:0, execStep:GRAIN_THAW)
D → C   WET_GRAIN_OUTPUT_EVENT GRAIN_START
C → D   ★ echo
D → C   WET_GRAIN_OUTPUT_EVENT OPEN_DOOR
C → D   ★ echo
…       (door open for feedingDuration; closes / finishes →
         further WET_GRAIN_OUTPUT_EVENT, finished:true)
```

`feedingDuration` = the door-open window (240 s typical; shorter values accepted).
Progress events are device-generated and flow regardless of the cloud — a pure
local responder just echoes them.

## 5. PLAF203 manual feed cycle

```
C → D   MANUAL_FEEDING_SERVICE on service/sub {grainNum:N}
D → C   service/post ack (code:0)
D → C   GRAIN_OUTPUT_EVENT ×2 (event/post, same msgId as the request)
C → D   ★ echoes
```

## 6. Keepalive & liveness

```
every 100 s:  D → C  MQTT PINGREQ   /  C → D  PINGRESP
every ~100 s: D → C  JSON HEARTBEAT (heart/post; no reply)
```

A quiet device (deep-sleep battery devices like the Polar) may stretch periods —
its TCP session survives short network gaps via retransmission. To force a fresh
connect for capture/debug: deauth its WiFi (AP-side), bounce the broker it's on,
or power-cycle it. TCP retransmission timeouts vary; MQTT keepalive can close the session sooner.

## 7. Local takeover flow (what this repo's tooling does)

```
D → (router redirect) → YOUR host:1884
D → C(you)  CONNECT  ← capture HERE once (credentials) — see docs/02
D → C(you)  subscribe / boot burst
C(you) → D  echoes + NTP + DEVICE_CONFIG_SYNC{mqttAddr: YOUR address}
            ← the pin: after this the device stores YOUR address (redirect
              becomes optional; verified across reboots)
C(you) → D  commands (feeds, plans, attrs) whenever you like
```

The reference implementation is `polar/polar_local_cloud.py` (29 unit tests in `polar/tests/`).
