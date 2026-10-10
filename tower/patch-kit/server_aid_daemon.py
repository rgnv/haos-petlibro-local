#!/usr/bin/env python3
"""Standing server-side responder for the fw 3.1.54 PLAF203 feeder.

The device's boot sequence waits for the server to push:
  FEEDING_PLAN_SERVICE x2 -> (ack) -> NTP_SYNC -> (ack) -> NTP answer
The addon (0.3.x) does NOT send these opening pushes, so without this the
device sits silent after every reconnect. This daemon runs inside the
petlibro_local addon container (launched by /share/petlibro-patches/fix-addon.sh)
and reacts to each fresh session.
"""
import datetime
import json
import os
import secrets
import time
import uuid
from zoneinfo import ZoneInfo

import paho.mqtt.client as mqtt

SERIAL = os.environ.get("PETLIBRO_SERIAL", "EXAMPLE_SERIAL")
BASE = f"dl/PLAF203/{SERIAL}/device"
LOG = "/data/petlibro_server_aid.log"
SESSION_GAP = 300  # seconds of silence that marks a new session (heartbeats are ~70s)


def log(msg: str) -> None:
    line = f"{time.strftime('%F %T')} {msg}"
    try:
        with open(LOG, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def tz_payload(ts_ms):
    la = ZoneInfo(os.environ.get("PETLIBRO_TIMEZONE", "UTC"))
    now = datetime.datetime.fromtimestamp(ts_ms / 1000, datetime.timezone.utc)
    current_off = int(now.astimezone(la).utcoffset().total_seconds())

    def next_transition(start_utc, cur_off):
        cursor = start_utc
        for _ in range(366 * 3):
            cand = cursor + datetime.timedelta(days=1)
            if int(cand.astimezone(la).utcoffset().total_seconds()) != cur_off:
                lo = int(cursor.timestamp()); hi = int(cand.timestamp())
                while lo + 1 < hi:
                    mid = (lo + hi) // 2
                    mt = datetime.datetime.fromtimestamp(mid, datetime.timezone.utc)
                    if int(mt.astimezone(la).utcoffset().total_seconds()) == cur_off:
                        lo = mid
                    else:
                        hi = mid
                new_off = int(datetime.datetime.fromtimestamp(hi, datetime.timezone.utc)
                              .astimezone(la).utcoffset().total_seconds())
                return hi * 1000, new_off
            cursor = cand
        return None

    t1 = next_transition(now, current_off)
    t2 = next_transition(
        datetime.datetime.fromtimestamp(t1[0] / 1000, datetime.timezone.utc)
        + datetime.timedelta(seconds=1), t1[1]) if t1 else None
    return {
        "timezoneOffsetSeconds": current_off,
        "nextDSTOffsetSeconds": (t1[1] if t1 else current_off),
        "nextDSTTransitionTs": (t1[0] if t1 else 0),
        "secondNextDSTOffsetSeconds": (t2[1] if t2 else (t1[1] if t1 else current_off)),
        "secondNextDSTTransitionTs": (t2[0] if t2 else 0),
        "timezone": current_off / 3600,
    }


def build_ntp_sync_payload() -> bytes:
    ts = int(time.time() * 1000)
    payload = {"cmd": "NTP_SYNC", "ts": ts, "msgId": secrets.token_hex(16)}
    payload.update(tz_payload(ts))
    return json.dumps(payload, separators=(",", ":")).encode("ascii")


# shortcut: silence identifies sessions; use a verified boot marker for fast device reconnects.
session = {"last": None, "ntp_sent": False, "answer_sent": False, "plan_sent": False}


def send_plan(c):
    plan = json.dumps({"cmd": "FEEDING_PLAN_SERVICE", "ts": int(time.time() * 1000),
                       "msgId": str(uuid.uuid4()), "plans": []})
    if c.publish(f"{BASE}/service/sub", plan, qos=0).rc != mqtt.MQTT_ERR_SUCCESS:
        return
    time.sleep(0.4)
    if c.publish(f"{BASE}/service/sub", plan, qos=0).rc != mqtt.MQTT_ERR_SUCCESS:
        return
    session["plan_sent"] = True
    log("new session -> FEEDING_PLAN_SERVICE x2 sent")


def send_ntp_sync(c):
    if c.publish(f"{BASE}/ntp/sub", build_ntp_sync_payload(), qos=0).rc != mqtt.MQTT_ERR_SUCCESS:
        return
    session["ntp_sent"] = True
    log("plan acked -> NTP_SYNC sent")


def send_answer(c):
    ts = int(time.time() * 1000)
    ans = {"cmd": "NTP", "ts": ts, "code": 0, "calibrationTag": False}
    ans.update(tz_payload(ts))
    if c.publish(f"{BASE}/ntp/sub", json.dumps(ans), qos=0).rc != mqtt.MQTT_ERR_SUCCESS:
        return
    log("-> NTP answer sent")
    return True


def on_m(c, uu, m):
    if m.retain or not (m.topic.startswith(BASE + "/") and m.topic.endswith("/post")):
        return
    try:
        d = json.loads(m.payload)
    except (ValueError, UnicodeError, RecursionError):
        return
    if not isinstance(d, dict) or not isinstance(d.get("cmd"), str):
        return
    now = time.monotonic()
    if session["last"] is None or now - session["last"] > SESSION_GAP:
        session.update(ntp_sent=False, answer_sent=False, plan_sent=False)
    session["last"] = now
    cmd = d["cmd"]

    try:
        if not session["plan_sent"]:
            send_plan(c)
            if cmd != "NTP":
                return
        if cmd == "NTP" and m.topic == f"{BASE}/ntp/post":
            send_answer(c)
        elif (cmd == "FEEDING_PLAN_SERVICE" and d.get("code") == 0
              and session["plan_sent"] and not session["ntp_sent"]):
            send_ntp_sync(c)
        elif (cmd == "NTP_SYNC" and d.get("code") == 0
              and session["ntp_sent"] and not session["answer_sent"]):
            session["answer_sent"] = bool(send_answer(c))
    except Exception as error:
        log(f"responder failed: {type(error).__name__}")


def on_connect(c, uu, flags, reason_code, properties=None):
    if reason_code == 0:
        c.subscribe(f"{BASE}/+/post")


def main():
    if not SERIAL.isalnum() or SERIAL.startswith(("EXAMPLE", "YOUR")):
        raise ValueError("Set PETLIBRO_SERIAL to your feeder serial")
    broker = os.environ["PETLIBRO_BROKER"]
    ZoneInfo(os.environ.get("PETLIBRO_TIMEZONE", "UTC"))
    with open("/data/options.json") as f:
        opts = json.load(f)
    log("daemon starting")
    c = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                    client_id="petlibro_server_aid", protocol=mqtt.MQTTv311)
    c.username_pw_set(opts["mqtt_username"], opts["mqtt_password"])
    c.on_message = on_m
    c.on_connect = on_connect
    c.reconnect_delay_set(min_delay=1, max_delay=60)
    c.connect_async(broker, 1883, 30)
    c.loop_forever(retry_first_connection=True)


if __name__ == "__main__":
    main()
