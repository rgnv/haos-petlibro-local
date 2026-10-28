#!/usr/bin/env python3
"""polar_local_cloud — the Polar feeder's local "cloud" brain.

Runs alongside the local broker (mosquitto on the local host:1884). Replaces every piece of
Petlibro cloud behavior the device depends on, with no internet dependency:

  * echoes only allowlisted event posts with `code:0` (+ linkId) on event/sub
  * answers `NTP` requests with the full timezone/DST payload on ntp/sub
  * re-asserts OUR broker address (DEVICE_CONFIG_SYNC) whenever the device boots
    (DEVICE_START_EVENT) so its stored endpoint can never drift back to the cloud
  * local feed trigger: publish to topic `polar/cmd/feed`
    {"plate": 1, "duration": 60}  -> sends the feedNowState plan-write
  * schedule management over MQTT: publish to `polar/cmd/schedule`
    {"action":"add","time":"06:00","plate":1,"duration":240,"days":[0,1,2]}
    (also remove/enable/disable/clear/list; state is the retained `polar/schedule`)
  * schedule file polar_schedule.json (managed for you by the commands above):
    [{"id":"h…","time":"06:00","plate":1,"duration":240,"days":[0,1,2],"enabled":true}]

All behavior modeled from a transparent-relay capture of the real cloud
(2026-10-09). Device: PLAF109 "Polar", serial EXAMPLE_SERIAL.
"""
import json
import logging
import os
import tempfile
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

SERIAL = os.environ.get("POLAR_SERIAL", "EXAMPLE_SERIAL")
BROKER = os.environ.get("POLAR_BROKER", "127.0.0.1")
PORT = int(os.environ.get("POLAR_PORT", "1884"))
BASE = f"dl/PLAF109/{SERIAL}/device"
LOCAL_MQTT = {"host": os.environ.get("POLAR_LOCAL_HOST", ""),
              "port": int(os.environ.get("POLAR_LOCAL_PORT", "1884"))}
TZ = ZoneInfo(os.environ.get("POLAR_TIMEZONE", "UTC"))
SCHEDULE_FILE = os.environ.get("POLAR_SCHEDULE", "/root/polar_schedule.json")
MANUAL_PLAN_ID = 666666666  # magic manual-feed slot the device uses

# Remote-settable attributes (allowlist). Deliberately excludes motor/stall
# calibration values; these are the user-facing comfort settings.
SETTABLE_ATTRS = {
    "lightSwitch", "enableLight", "enableSound", "soundSwitch", "volume",
    "ringerMode", "ringerInterval", "ringerDuration", "closeDoorTime", "platePosition",
    "doorCheckSignalTime", "doorNotcheckSignalTime",
}

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("polar-cloud")

# Observed REAL-CLOUD behavior (relay capture 2026-10-09):
#   - event/post: JSON echo {cmd,msgId,code:0,linkId} on event/sub, but ONLY for
#     the known event set below (DEVICE_LOG_REPORT_EVENT / heart get no reply).
#   - service/post: NO JSON echo (broker PUBACK only). Echoing a service reply
#     re-triggers it on the device (a WET_GRAIN_FEEDING_PLAN_SERVICE reply echo
#     was re-executed -> 2099 FeedPlanErro loop). Never echo service/config/system.
#   - ntp/post: answered with the NTP payload on ntp/sub.
#   - heart/post: nothing.
EVENT_ECHO_SET = {"DEVICE_START_EVENT", "ATTR_PUSH_EVENT", "GET_FEEDING_PLAN_EVENT",
                  "WET_GRAIN_OUTPUT_EVENT"}


def validate_serial(serial):
    if (not serial or not serial.isascii() or not serial.isalnum()
            or serial.startswith(("EXAMPLE", "YOUR"))):
        raise ValueError("Set POLAR_SERIAL to your device serial (letters and digits).")


def validate_local_address(host, port):
    import ipaddress
    address = ipaddress.ip_address(host)
    if address.version != 4 or address.is_loopback or address.is_unspecified or address.is_multicast:
        raise ValueError("POLAR_LOCAL_HOST must be a device-reachable IPv4 address")
    if not 1 <= port <= 65535:
        raise ValueError("POLAR_LOCAL_PORT must be 1..65535")


def ntp_payload(now=None) -> dict:
    """Full NTP answer payload (device <- cloud on ntp/sub)."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)

    def offset_at(dt):
        return int(dt.astimezone(TZ).utcoffset().total_seconds())

    transitions = []
    prev = offset_at(now)
    cur = now
    for _ in range(400 * 4):  # 400 days of 6h steps
        cur = cur + timedelta(hours=6)
        off = offset_at(cur)
        if off != prev:
            lo, hi = int((cur - timedelta(hours=6)).timestamp()), int(cur.timestamp())
            while hi - lo > 1:
                mid = (lo + hi) // 2
                if offset_at(datetime.fromtimestamp(mid, timezone.utc)) == prev:
                    lo = mid
                else:
                    hi = mid
            transitions.append((hi * 1000, off))
            prev = off
            if len(transitions) == 2:
                break
    cur_off = offset_at(now)
    tr0 = transitions[0] if transitions else (0, cur_off)
    tr1 = transitions[1] if len(transitions) > 1 else (0, cur_off)
    return {
        "cmd": "NTP",
        "ts": int(now.timestamp() * 1000),
        "code": 0,
        "calibrationTag": False,
        "timezoneOffsetSeconds": cur_off,
        "nextDSTOffsetSeconds": tr0[1],
        "nextDSTTransitionTs": tr0[0],
        "secondNextDSTOffsetSeconds": tr1[1],
        "secondNextDSTTransitionTs": tr1[0],
        "timezone": cur_off / 3600,
    }


def config_sync_payload() -> dict:
    """Re-assert the local broker as the device's cloud (on service/sub)."""
    return {
        "cmd": "DEVICE_CONFIG_SYNC",
        "ts": int(time.time() * 1000),
        "msgId": uuid.uuid4().hex,
        "mqttAddr": [LOCAL_MQTT.copy()],
        "httpsAddr": "api.us.petlibro.com",
        "linkId": uuid.uuid4().hex,
    }


def validate_feed(plate, duration):
    # Hardware upper bounds are undocumented; do not invent them.
    if type(plate) is not int or plate < 1:
        raise ValueError("plate must be a positive integer")
    if type(duration) is not int or duration < 1:
        raise ValueError("duration must be a positive integer")


def feed_payload(plate: int = 1, duration: int = 60, now=None) -> dict:
    """Local feed: plan-write with feedNowState to the magic manual slot."""
    validate_feed(plate, duration)
    now = (now or datetime.now(TZ)).astimezone(TZ)
    return {
        "cmd": "WET_GRAIN_FEEDING_PLAN_SERVICE",
        "ts": int(now.timestamp() * 1000),
        "msgId": f"local-{uuid.uuid4().hex[:12]}",
        "plans": [{
            "planId": MANUAL_PLAN_ID,
            "executionTime": now.strftime("%H:%M"),
            "executionDay": now.strftime("%Y-%m-%d"),
            "plate": plate,
            "feedingDuration": duration,
            "feedNowState": True,
        }],
    }


MAX_SCHEDULES = 24


def content_id(value) -> str:
    """Stable id for an entry that arrives without one (FNV-1a 64 of its content).

    Hand-edited schedule files keep working, and the HA list keeps a
    remove-by-id target across reloads.
    """
    text = json.dumps(value, sort_keys=True, separators=(",", ":"))
    digest = 0xCBF29CE484222325
    for byte in text.encode("utf-8"):
        digest = ((digest ^ byte) * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return f"h{digest:016x}"


def normalize_entry(entry) -> dict:
    """Validate one schedule entry; return the canonical stored shape."""
    if not isinstance(entry, dict):
        raise ValueError("schedule entry must be an object")
    clock = entry.get("time")
    if (not isinstance(clock, str) or len(clock) != 5 or clock[2] != ":"
            or not clock[:2].isascii() or not clock[:2].isdigit()
            or not clock[3:].isascii() or not clock[3:].isdigit()):
        raise ValueError("schedule time must be HH:MM")
    hour, minute = int(clock[:2]), int(clock[3:])
    if hour > 23 or minute > 59:
        raise ValueError("schedule time out of range")
    plate = entry.get("plate", 1)
    if type(plate) is not int or not 1 <= plate <= 3:
        raise ValueError("plate must be 1..3")
    duration = entry.get("duration", 240)
    if type(duration) is not int or not 1 <= duration <= 3600:
        raise ValueError("duration must be 1..3600 seconds")
    days = entry.get("days", list(range(7)))
    if not isinstance(days, list) or any(type(d) is not int or not 0 <= d <= 6 for d in days):
        raise ValueError("schedule days must be integers 0..6")
    days = sorted(set(days))
    if not days:
        raise ValueError("schedule needs at least one day")
    enabled = entry.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValueError("schedule enabled must be a boolean")
    entry_id = entry.get("id")
    if entry_id is not None:
        if (not isinstance(entry_id, str) or not 1 <= len(entry_id) <= 40
                or not all(ch.isascii() and (ch.isalnum() or ch in "-_") for ch in entry_id)):
            raise ValueError("schedule id must be 1-40 chars of [A-Za-z0-9_-]")
    seed = {"time": clock, "plate": plate, "duration": duration, "days": days}
    return {"id": entry_id or content_id(seed), "time": clock, "plate": plate,
            "duration": duration, "days": days, "enabled": enabled}


def read_entries(path=None) -> list:
    """Load the schedule file, dropping entries that no longer validate."""
    path = SCHEDULE_FILE if path is None else path
    try:
        with open(path) as handle:
            raw = json.load(handle)
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as ex:
        log.warning("cannot read schedule %s: %s", path, ex)
        return []
    if not isinstance(raw, list):
        return []
    entries = []
    for entry in raw:
        try:
            entries.append(normalize_entry(entry))
        except ValueError:
            continue
    return entries


def write_entries(entries, path=None) -> None:
    """Write the schedule atomically (same-directory temp file + rename)."""
    path = SCHEDULE_FILE if path is None else path
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=directory, prefix=".polar-schedule-", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w") as handle:
            json.dump(entries, handle, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise


def schedule_state(entries) -> dict:
    """Retained `polar/schedule` payload: count + entries with 1-based index."""
    return {"count": len(entries),
            "entries": [dict(entry, index=position) for position, entry in enumerate(entries, 1)]}


def schedule_index(data, entries) -> int:
    """Resolve a command's `index` (1-based) or `id` to a list position."""
    index = data.get("index")
    if type(index) is int:
        if not 1 <= index <= len(entries):
            raise ValueError("schedule index out of range")
        return index - 1
    entry_id = data.get("id")
    if isinstance(entry_id, str):
        for position, entry in enumerate(entries):
            if entry["id"] == entry_id:
                return position
        raise ValueError(f"no schedule entry with id {entry_id}")
    raise ValueError("schedule command needs index or id")


def echo_payload(topic, data):
    """Fail closed: no service command, foreign topic, or linked reply can echo."""
    if (topic != f"{BASE}/event/post" or not isinstance(data, dict)
            or "linkId" in data or not isinstance(data.get("cmd"), str)
            or data["cmd"] not in EVENT_ECHO_SET):
        return None
    echo = {"cmd": data["cmd"], "msgId": data.get("msgId", ""),
            "code": 0, "linkId": uuid.uuid4().hex, "ts": int(time.time() * 1000)}
    if data["cmd"] == "WET_GRAIN_OUTPUT_EVENT":
        echo["execStep"] = data.get("execStep", "")
    return echo


def schedule_due(entry, now, previous):
    """Return daily identity if due; catch up at most five real minutes.

    Disabled entries never fire. The identity includes the entry id so two
    entries at the same minute and plate both fire instead of shadowing.
    """
    entry = normalize_entry(entry)
    if not entry["enabled"]:
        return None
    clock, plate = entry["time"], entry["plate"]
    hour, minute = int(clock[:2]), int(clock[3:])
    days = entry["days"]
    current = int(now.timestamp()) // 60
    start = current if previous is None else max(current - 5, min(previous, current))
    for minute_number in range(start, current + 1):
        local = datetime.fromtimestamp(minute_number * 60, TZ)
        if local.weekday() in days and (local.hour, local.minute) == (hour, minute):
            return (local.date().isoformat(), clock, plate, entry["id"])
    return None


class PolarCloud:
    def __init__(self, client):
        self.client = client
        # shortcut: deduplication is process-local; add a durable ledger for restart safety.
        self.last_fired = {}  # (date, time, plate) -> ts, prevents double-fire
        self.previous_minute = None
        self.config_due = None
        self.config_lock = threading.Lock()
        self.last_heart = None      # monotonic ts of last HEARTBEAT
        self.status_online = None   # last published online state
        # attributes we permit setting remotely (safety allowlist) + tracked state
        self.attrs = {}

    # ---- inbound dispatch -------------------------------------------------
    def on_message(self, msg):
        topic = msg.topic
        try:
            data = json.loads(msg.payload)
        except (ValueError, UnicodeError, RecursionError):
            log.warning("non-JSON on %s", topic)
            return
        # Retained commands/events must not replay physical actions after reconnect.
        if msg.retain or not isinstance(data, dict) or "linkId" in data:
            return
        cmd = data.get("cmd", "")
        if not isinstance(cmd, str):
            return
        # ---- device posts ------------------------------------------------
        if topic == f"{BASE}/heart/post" and cmd == "HEARTBEAT":
            # Dashboard status feed: retained JSON on polar/status (bridged to HA).
            self.last_heart = time.monotonic()
            if self.status_online is not True:
                self.status_online = True
            self.pub("polar/status", {
                "online": True,
                "count": data.get("count"),
                "rssi": data.get("rssi"),
                "temperature": data.get("temperature"),
                "device_ts": data.get("ts"),
                "seen_at": int(time.time() * 1000),
                "attrs": self.attrs,
                "schedule": self.schedule_summary(),
            }, retain=True)
            return
        if topic == f"{BASE}/ntp/post" and cmd == "NTP":
            self.pub(f"{BASE}/ntp/sub", ntp_payload())
            return
        if topic == f"{BASE}/event/post":
            if cmd == "ATTR_PUSH_EVENT":
                for k, v in data.items():
                    if k in SETTABLE_ATTRS:
                        self.attrs[k] = v
            echo = echo_payload(topic, data)
            if echo is not None:
                self.pub(f"{BASE}/event/sub", echo)
            if cmd == "DEVICE_START_EVENT":
                log.info("device booted -> re-asserting local broker")
                with self.config_lock:
                    if self.config_due is None:
                        self.config_due = time.monotonic() + 1.5
            return
        # service/config/system posts: no JSON echo (matches real cloud behavior)
        # ---- local command surface ----------------------------------------
        if topic == "polar/cmd/feed":
            plate = data.get("plate", 1)
            duration = data.get("duration", 60)
            try:
                payload = feed_payload(plate, duration)
            except ValueError as ex:
                log.warning("invalid feed command: %s", ex)
                return
            log.info("local feed trigger: plate=%s duration=%ss", plate, duration)
            self.pub(f"{BASE}/service/sub", payload)
        elif topic == "polar/cmd/config":
            self.pub(f"{BASE}/service/sub", config_sync_payload())
        elif topic == "polar/cmd/set":
            attr, value = data.get("attr"), data.get("value")
            if not isinstance(attr, str) or attr not in SETTABLE_ATTRS or value is None:
                log.warning("invalid or disallowed set: %r", data)
                return
            self.attrs[attr] = value
            self.pub(f"{BASE}/service/sub", {
                "cmd": "ATTR_SET_SERVICE", "ts": int(time.time() * 1000),
                "msgId": "set-" + uuid.uuid4().hex[:8], attr: value,
            })
            log.info("local attr set: %s=%r", attr, value)
        elif topic == "polar/cmd/schedule":
            try:
                self.schedule_command(data)
            except (ValueError, OSError) as ex:
                log.warning("invalid schedule command: %s", ex)
        elif topic == "polar/cmd/bell":
            # Captured from the vendor cloud: the chime is a function test, not an
            # attribute write. ATTR_SET_SERVICE {ringerMode:"RING"} is accepted
            # (code 0, echoed in ATTR_PUSH_EVENT) but never makes a sound.
            self.pub(f"{BASE}/service/sub", {
                "cmd": "DEVICE_FUNCTION_TEST_SERVICE", "ts": int(time.time() * 1000),
                "action": "AUDIO", "linkId": uuid.uuid4().hex,
            })
            log.info("local bell ring")

    # ---- helpers ----------------------------------------------------------
    def pub(self, topic, payload, retain=False):
        if not self.client.is_connected():
            log.warning("publish skipped while disconnected: %s", topic)
            return False
        result = self.client.publish(topic, json.dumps(payload, separators=(",", ":")), qos=1, retain=retain)
        if result.rc != 0:
            log.error("publish failed: topic=%s rc=%s", topic, result.rc)
        return result.rc == 0

    # ---- scheduler ---------------------------------------------------------
    def sched_tick(self, entries, now):
        if not isinstance(entries, list):
            raise ValueError("schedule must be an array")
        if self.previous_minute is None:
            self.previous_minute = int(now.timestamp()) // 60
        publish_failed = False
        for entry in entries:
            try:
                key = schedule_due(entry, now, self.previous_minute)
                if key is not None and key not in self.last_fired:
                    if self.pub(f"{BASE}/service/sub", feed_payload(
                            entry.get("plate", 1), entry.get("duration", 240))):
                        self.last_fired[key] = now.timestamp()
                        log.info("schedule fire: %s", entry)
                    else:
                        publish_failed = True
            except (ValueError, TypeError) as ex:
                log.warning("invalid schedule entry: %s", ex)
        if self.client.is_connected() and not publish_failed:
            self.previous_minute = int(now.timestamp()) // 60
        cutoff = now.timestamp() - 3 * 86400
        self.last_fired = {k: v for k, v in self.last_fired.items() if v > cutoff}

    def schedule_summary(self):
        """Compact view of the local schedule for the status payload."""
        return schedule_state(read_entries())["entries"]

    def publish_schedule(self):
        """Republish the retained `polar/schedule` state."""
        return self.pub("polar/schedule", schedule_state(read_entries()), retain=True)

    def schedule_command(self, data):
        """Apply a `polar/cmd/schedule` request and persist the result.

        Actions: list, add, remove, enable, disable, clear. remove/enable/disable
        take a 1-based `index` or an entry `id`.
        """
        action = data.get("action", "list")
        entries = read_entries()
        if action == "add":
            if len(entries) >= MAX_SCHEDULES:
                raise ValueError(f"schedule is full ({MAX_SCHEDULES} entries max)")
            entry = normalize_entry(data)
            if any(existing["id"] == entry["id"] for existing in entries):
                raise ValueError("that schedule entry already exists")
            entries.append(entry)
            write_entries(entries)
        elif action == "remove":
            del entries[schedule_index(data, entries)]
            write_entries(entries)
        elif action in ("enable", "disable"):
            entries[schedule_index(data, entries)]["enabled"] = action == "enable"
            write_entries(entries)
        elif action == "clear":
            entries = []
            write_entries(entries)
        elif action != "list":
            raise ValueError(f"unknown schedule action: {action}")
        log.info("local schedule %s -> %d entries", action, len(entries))
        self.publish_schedule()

    def sched_loop(self):
        next_schedule = 0
        while True:
            try:
                with self.config_lock:
                    if self.config_due is not None and time.monotonic() >= self.config_due:
                        if self.pub(f"{BASE}/service/sub", config_sync_payload()):
                            self.config_due = None
                # Offline detection: no heartbeat for 4 min -> retained offline status
                if (self.last_heart is not None and self.status_online
                        and time.monotonic() - self.last_heart > 240):
                    self.status_online = False
                    self.pub("polar/status", {
                        "online": False,
                        "seen_at": int((time.time() - (time.monotonic() - self.last_heart)) * 1000),
                    }, retain=True)
                    log.info("device silent >4min -> published offline status")
                if time.monotonic() >= next_schedule:
                    next_schedule = time.monotonic() + 20
                    self.sched_tick(read_entries(), datetime.now(TZ))
            except Exception as ex:
                log.error("scheduler error: %s", ex)
            time.sleep(0.5)


def main():
    validate_serial(SERIAL)
    validate_local_address(LOCAL_MQTT["host"], LOCAL_MQTT["port"])
    if not 1 <= PORT <= 65535:
        raise ValueError("POLAR_PORT must be 1..65535")
    import paho.mqtt.client as mqtt

    client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                         client_id="polar-local-cloud", protocol=mqtt.MQTTv311)
    cloud = PolarCloud(client)

    def on_connect(c, u, flags, rc, props=None):
        log.info("broker connected (rc=%s)", rc)
        if rc != 0:
            return
        c.subscribe([
            (f"{BASE}/+/post", 1),
            ("polar/cmd/#", 1),
        ])
        # Retained schedule state, so a broker that lost its retained store (or a
        # restart) still shows the current schedule in Home Assistant.
        cloud.publish_schedule()
        log.info("subscribed to device topics + polar/cmd/#")

    client.on_connect = on_connect
    client.on_message = lambda c, u, m: cloud.on_message(m)
    client.reconnect_delay_set(min_delay=2, max_delay=30)
    client.max_queued_messages_set(32)
    client.connect_async(BROKER, PORT, 60)

    threading.Thread(target=cloud.sched_loop, daemon=True).start()
    log.info("polar-local-cloud v2-no-service-echo up (broker=%s:%s)", BROKER, PORT)
    client.loop_forever(retry_first_connection=True)


if __name__ == "__main__":
    main()
