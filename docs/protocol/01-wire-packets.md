# 01 — MQTT wire packets (byte level)

Illustrative packet layouts based on the reported protocol; raw capture evidence
is not included here. Device-specific fields are placeholders. MQTT 3.1.1
packet format: **1 fixed header byte** (type << 4 | flags), then **remaining
length** (1–4 bytes, varint), then type-specific payload.

| Type | Value | Direction (this ecosystem) |
|---|---|---|
| CONNECT | 0x10 | device → broker |
| CONNACK | 0x20 | broker → device |
| PUBLISH | 0x30 \| flags | both ways (flags carry QoS/retain/dup) |
| PUBACK | 0x40 | both (for QoS 1 PUBLISHes) |
| SUBSCRIBE | 0x82 | device → broker (lower nibble must be 0x2) |
| SUBACK | 0x90 | broker → device |
| PINGREQ | 0xC0 | device → broker |
| PINGRESP | 0xD0 | broker → device |
| DISCONNECT | 0xE0 | client → broker |

---

## CONNECT (device → broker) — `0x10`

This is where the credentials ride. Illustrative packet: **88 bytes total**
(header 2 + payload 86; the remaining-length varint is 1 byte: `0x56`).

```
10 56                             fixed header: CONNECT, remaining length 0x56
00 04 4D 51 54 54                 protocol name: "MQTT"
04                                protocol level 4 (MQTT 3.1.1)
C2                                connect flags = 1100 0010:
                                    username (0x80) ✓
                                    password (0x40) ✓
                                    will retain (0x20) ✗
                                    will QoS (0x18 >> 3) = 0
                                    will flag (0x04) ✗   ← no will message
                                    clean session (0x02) ✓ ← TRUE: fresh session
                                    reserved (0x01) = 0
00 64                             keepalive: 100 seconds
00 16 <22 bytes>                  client id   = device serial (22-byte serial placeholder)
00 10 <16 bytes>                  username    = 16-char token (per device)
00 20 <32 bytes>                  password    = 32-char token (per device)
```

**Facts:**
- **Clean session = true in this C2 example.** The device must re-subscribe.
  Retained messages can still be delivered when it subscribes again. A CONNACK
  session-present value of zero alone does not prove the CONNECT clean-session
  flag: it also occurs when no prior persistent session exists.
- **No will message** (`No will message specified.` appears in broker logs).
- Client id = the device serial — one MQTT session per serial. Two clients with
  the same id kick each other off in a loop.
- Keepalive 100 s. The device also sends application-level HEARTBEAT JSON every
  ~100 s (unrelated to MQTT PINGREQ, which is sent to keep the TCP session fresh).

## CONNACK (broker → device) — `0x20`

Example matching the clean-session CONNECT above: `20 02 00 00`

- session-present = `00` (no session restored)
- return code = `00` = accepted.

## SUBSCRIBE (device → broker) — `0x82`

PLAF203 (fw 3.1.54) sends **one SUBSCRIBE per channel**, each with a packet id:

```
82 <len> <packet-id> 00 <topic-len> <topic> 01
```

Topics (in observed order): `ntp/sub`, `ota/sub`, `broadcast/sub`, `config/sub`,
`event/sub`, `service/sub`, `system/sub` — all requested at **QoS 1**.

PLAF109 (fw 2.0.32) sends a **single wildcard subscribe**:

```
dl/PLAF109/<SERIAL>/device/+/sub   (QoS 1)
```

The wildcard means the device listens on **every** `…/device/<anything>/sub` —
any subtopic you publish to is received. (Do not interpret this as a security
boundary; it's a convenience for the cloud.)

## SUBACK (broker → device) — `0x90`

`90 <len> <packet-id> <granted-qos>` — observed granting QoS 1 (`01`) to all.

## PUBLISH — `0x30 | flags`

Flags byte: `(dup << 3) | (qos << 1) | retain`.

Observed behavior:
- Device → cloud: **QoS 1** for everything except `DEVICE_LOG_REPORT_EVENT`
  (QoS 0). No DUP, no RETAIN seen in normal operation.
- Cloud → device: **QoS 0** mostly (all JSON commands), QoS 1 occasionally for
  delivery-critical pushes. No retention in normal traffic — but the broker
  *can* deliver queued QoS-1 messages on reconnect (session-present above).

Layout (QoS 1, no retain, 2-byte varint):

```
32 <len-lo> <len-hi> <topic-len> <topic> <packet-id> <payload>
```

Illustrative PLAF109 heartbeat framing with a 115-byte payload:

```
32 AA 01                         PUBLISH, QoS 1, remaining length = 170
00 33                            topic length 51
64 6C 2F 50 4C 41 46 31 30 39 …  "dl/PLAF109/<SERIAL>/device/heart/post"
B7 92                            packet id (varies)
7B 22 63 6D 64 22 …              payload begins {"cmd" (compact JSON)
```

JSON whitespace can vary. The current notification example string-matches
compact MOTION payloads; spaced test payloads do not match (see docs/04).

## PUBACK — `0x40`

`40 02 <packet-id>` — sent by the broker for every QoS-1 device publish, and by
the device for every QoS-1 cloud publish. Pure transport; contains no JSON.

## PINGREQ / PINGRESP — `0xC0` / `0xD0`

`C0 00` / `D0 00`. With keepalive 100 s, a client must not let more than 100 s pass between
control packets; PINGREQ fills otherwise idle intervals. The broker may close
a connection after 1.5 times that interval without a control packet. This is
independent of the JSON HEARTBEAT.

## DISCONNECT — `0xE0`

`E0 00` on graceful shutdown (e.g. before a device reboot). A device that just
drops TCP (reboot, power loss) sends nothing.

---

## Observing raw packets

The relay (`polar/polar_relay.py`) logs every chunk with a timestamp, tag
(`DEV->CLOUD` / `CLOUD->DEV`), length, and a Python bytes repr — everything above
may be truncated: only 600 bytes per chunk are logged. For full packet captures, `tcpdump -i any -s 0 -w capture.pcap tcp port 1884`
on the broker host, or the wizard's raw capture listener.

**Reminder:** raw captures contain credentials (CONNECT) and personal data
(serials, member ids). Binary packet captures require manual packet-aware redaction. The sanitizer
accepts textual logs only; manually review them before sharing.
