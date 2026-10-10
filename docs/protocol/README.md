# Protocol documentation — Petlibro MQTT

Working reference for the protocol spoken by Petlibro's MQTT devices (PLAF203
camera feeder, PLAF109 wet feeder — and likely siblings). Everything here is
**observed from real captures** on live devices (2026-10, firmware 3.1.54 /
2.0.32). Values that vary by device are shown as placeholders (`<SERIAL>` etc.);
other models and firmware remain unverified.

- New here? Read `01-wire-packets.md` top-to-bottom, then `04-flows.md`.
- Building a local responder? `03-messages.md` + `05-fields.md` are your reference.
- Found something new? [Submit a dossier](../../CONTRIBUTING.md).

## The stack at a glance

```
          PLAF203 "Tower"                        PLAF109 "Polar"
       (camera feeder, fw 3.1.54)             (wet feeder, fw 2.0.32)
                 │                                      │
                 └────────────── MQTT 3.1.1 over TCP ───┘
                        port 1883, PLAINTEXT (no TLS)
                        cloud broker: mqtt.us.petlibro.com
                        (resolve the hostname; do not hardcode observed IPs)
                 │
     topic root: dl/<PRODUCT>/<SERIAL>/device/<channel>/{post|sub}
       PRODUCT: PLAF203 | PLAF109        channel: heart ntp event service config (system)
```

**Layers:**

| Layer | Description |
|---|---|
| MQTT 3.1.1 | Standard MQTT. QoS 0/1 used. Session persistence depends on the CONNECT clean-session flag; inspect a valid packet. Keepalive 100 s. No TLS, no will message, plaintext credentials. |
| Topic tree | `dl/<PRODUCT>/<SERIAL>/device/<channel>/post` (device → cloud) and `.../sub` (cloud → device). The device subscribes; the cloud publishes to `/sub` and receives on `/post`. |
| JSON application layer | Every PUBLISH payload is a JSON object with a `cmd` field. Cross-cutting fields: `cmd`, `ts` (epoch ms, device clock — drifts), `msgId` (correlation), `code` (0 = success), `linkId` (cloud-assigned session tag, cloud→device only). |

**Responder rules for these implementations:**

1. **Every QoS 1 PUBLISH gets an MQTT PUBACK** from the broker —
   that's transport-level; nothing in the JSON depends on it.
2. **Application replies are separate.** The cloud replies to *event-class* posts
   from the allowlist with a JSON echo on the matching `/sub`:

   ```json
   {"cmd":"<same as request>","msgId":"<same as request>","code":0,"linkId":"<uuid>"}
   ```

   — with two exceptions: `ntp/post` gets a full **NTP answer**, and
   `service/post`, `config/post`, heartbeats, and `DEVICE_LOG_REPORT_EVENT` get
   **no JSON reply**. Echoing service replies re-triggers them on the device
   (infinite loop — see `03-messages.md` §Echo rules).
3. **`linkId` only ever goes cloud → device.** Its presence in a message you
   received means the message came from the cloud side (useful as a self-echo
   guard in responders).

## Files

| File | Contents |
|---|---|
| `01-wire-packets.md` | MQTT packets: CONNECT, CONNACK, SUBSCRIBE, SUBACK, PUBLISH, PUBACK, PINGREQ/RESP, DISCONNECT — byte layouts + observed values |
| `02-topics.md` | Full topic catalog: every channel, direction, subscription sets per family |
| `03-messages.md` | Every JSON message type, field-by-field, with examples and response rules |
| `04-flows.md` | End-to-end sequences: boot handshake, NTP exchange, feed cycle, reconnect/keepalive |
| `05-fields.md` | Master field dictionary — every key seen anywhere, type, values, meaning |
