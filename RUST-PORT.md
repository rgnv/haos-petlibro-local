# Standalone Rust service port

`polar-rs/` contains three member crates. Installers remain shell and consume
prebuilt binaries; the deployment host does not need a Rust toolchain. The port
itself contacted no live service, broker, feeder, or camera; the deployment to a
live host happened afterwards and was smoke-tested — see Verification.

| Python reference | Rust crate / binary | Runtime |
|---|---|---|
| `polar/polar_local_cloud.py` | `polar-cloud` / `polar-local-cloud` | Tokio + rumqttc, MQTT 3.1.1 |
| `tower/recorder/petlibro-cam-recorder.py` | `tower-recorder` / `petlibro-cam-recorder` | Tokio + rumqttc + external ffmpeg |
| `polar/polar_relay.py` | `polar-relay` / `polar-relay` | Tokio TCP |

## Polar parity

- Same environment variables/defaults, topic base, client ID, 60-second keepalive,
  QoS 1 publications/subscriptions, clean-session reconnect subscriptions to
  `device/+/post` and `polar/cmd/#`. Reconnect backoff runs from 2 to 30 seconds,
  including first-connection failures. Client request queue holds 32 requests.
- Echo only the four allowlisted event posts. Never echo service/config/system
  replies, heartbeat, logs, or foreign topics. Preserve `msgId` and progress
  `execStep`. Drop retained, non-object, invalid-command, and any linked input.
- NTP uses the same 400-day, six-hour scan and binary search to exact DST seconds;
  includes two transitions, fractional-hour offsets, and zero transitions for
  fixed-offset zones. `chrono-tz` embeds IANA data rather than using host zoneinfo.
- Boot echo is immediate; config reassertion waits 1.5 seconds, coalesces repeated
  boots and retries rejected/disconnected publishes. Broker pin payload preserves
  `httpsAddr`, local broker address, and independent random UUIDs.
- Manual feeds preserve validation, manual plan slot, local date/time, defaults,
  and `feedNowState`. No undocumented hardware upper bound is added.
- Every heartbeat publishes retained `polar/status`; silence strictly greater
  than 240 seconds publishes offline once. No initial offline/LWT is added.
  As in Python, offline state changes before attempting publication, so a broker
  outage at that instant can leave retained status stale until another heartbeat.
  Status also carries cached allowlisted attributes and the first 12 schedule
  objects, reloaded from disk on each heartbeat with Python defaults.
- Schedule file defaults to `/root/polar_schedule.json`, reloads every 20 seconds,
  and tolerates absence. Monday=0. Five-minute catch-up uses elapsed UTC minutes,
  then local timezone matching. Deduplication uses date/time/plate, including DST
  folds and duplicate rows; invalid rows do not starve valid rows. Rejected sends
  retry within the catch-up window. Deduplication expires after three days and is
  process-local, so restarting within a scheduled minute can repeat a feed.
- `polar/cmd/set` uses `{"attr":"volume","value":50}` and the exact Python
  allowlist, including `platePosition`. Null values and unknown attributes are
  rejected; values otherwise pass through unchanged. The local attribute cache
  updates before publish, even if disconnected, matching Python. Incoming
  `ATTR_PUSH_EVENT` updates only allowlisted keys. `polar/cmd/bell` emits an
  `ATTR_SET_SERVICE` with `ringerMode:"RING"` and a `bell-` message ID.

## Recorder parity

Same environment configuration and external credential JSON; no embedded
credentials. MQTT uses port 1883, the existing client ID, 30-second keepalive,
QoS 1 event subscription on every connection, and 2–30-second reconnect backoff.
Only nonretained motion/feed events from the exact device event topic trigger
recording. One recording at a time; 30-second cooldown follows success or failure.

Same ffmpeg argument order, TCP RTSP, 20-second input timeout, 180-second clip,
stream copy, faststart, explicit MP4 container, and 270-second process timeout.
Timeout kills/reaps ffmpeg; errors do not include the stream URL. Clips use local
host time and `YYYY-MM-DD/tower_{motion|feed}_YYYYMMDD_HHMMSS.mp4.part` followed by
atomic rename only after success and nonzero size. Missing/symlinked roots,
symlinked date directories, and less than 100 MiB free are rejected.

Retention stays in the existing shell timer: matching completed clips expire
after 30 days, partial files after one day. No Rust deletion loop was added.
The port keeps the Python directory-check model, not a new adversarial filesystem
sandbox; administrators must control write access to clip directories.

## Relay parity

Same listen/upstream environment variables, default ports/hostname, IPv4 wildcard
listener with address reuse and backlog 16, eight-second connection timeout,
65,536-byte reads, and no established-stream idle timeout. EOF/error closes both
sides. The Tokio timeout includes DNS resolution and the complete connect attempt.

Logs append to `/root/polar_relay.log`, enforcing mode 0600 even for an existing
file. Format is `[YYYY-MM-DD HH:MM:SS] TAG (NB) b'Python bytes repr'`, with local
host timestamps, full chunk length, and first 600 bytes only. Python quote choice,
byte escapes, direction/lifecycle tags, and tuple address rendering are preserved.
OS error wording and TCP chunk boundaries can differ between runtimes. Log write
failures report to stderr without breaking forwarding. Logs contain raw MQTT
credentials when deployed; existing capture-redaction guidance still applies.

## Deliberately retained Python / shell

- `tower/patch-kit/*`, `tower/dashboard`, and `tower/automations` stay untouched:
  they run in HAOS addons/HA Python environments without a Rust toolchain.
- `polar_local_feed.py` and `polar_make_local.py` stay one-shot Python commands;
  their imports require keeping `polar_local_cloud.py`. Existing Python service
  files also remain as parity references and rollback sources. Default installers
  and systemd units select Rust, not these service references.
- Capture sanitizer, setup/capture helpers, installers, and retention scripts stay
  in their existing languages. They do not benefit from this standalone daemon
  port and preserve HAOS compatibility.

## Verification

Rust tests port the service-related payload, routing, validation, schedule,
publication-failure, and deferred-config cases from `polar/tests/test_polar.py`.
Additional checks cover heartbeat/offline recovery, attribute allowlisting/state,
bell commands, schedule status summaries, unbounded positive feed integers,
fractional timezone offsets, recorder arguments/atomic files/failures/timeouts,
symlink rejection, relay bytes formatting, bidirectional forwarding and closure.
Relay tests use Tokio in-memory duplex streams; recorder tests use local stub
executables and temporary files, never a camera or broker.

Full workspace validation passes on cargo 1.98.1: `cargo build --release
--workspace`, `cargo test --workspace` (34/34 — 26 cloud, 4 relay, 4 recorder),
and zero clippy warnings; `Cargo.lock` is committed for reproducible builds. The
Rust control surface matches the Python reference through commit `d4760de`,
including its concurrent allowlist update. An earlier review environment could
only run partial offline checks (29 Python protocol tests, 9 release tests, and
the dependency-free Rust subsets under `.review/rust-offline/`); the full build
and suite were run afterwards on a machine with dependency access.

Live deployment smoke test (Debian 13 x86_64, glibc 2.41 reference host):
binaries installed through `scripts/install-polar.sh` and
`scripts/install-tower-recorder.sh`. Verified on live hardware: broker connect
with subscriptions to device topics and `polar/cmd/#`; retained `polar/status`
publishing with tracked attributes; device heartbeat handling; a full
`polar/cmd/set` round trip (ATTR_SET_SERVICE → device `code:0` acknowledgement
and `ATTR_PUSH_EVENT` carrying the command's `msgId` → echo); and the recorder
finalizing a ~180 s 1080p h264 clip from a live MQTT motion event (`.part`
growth → atomic rename). The relay remains a capture-time tool, not a service.
Remaining limits: smoke coverage is for that reference host; other hosts and
firmware revisions need their own verification, and the broker-security and
physical-execution caveats in REVIEW.md are unchanged.
