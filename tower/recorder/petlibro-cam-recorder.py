#!/usr/bin/env python3
"""Petlibro Tower camera recorder.

Records 3-minute clips when the feeder reports MOTION or GRAIN_OUTPUT (feeding)
events. Events arrive via the local MQTT broker direct from the
feeder. Clips land in /mnt/backups/petlibro-cam/YYYY-MM-DD/ and are pruned to
30 days by the companion retention timer.
"""
import json
import logging
import os
import subprocess
import shutil
import threading
import time

import paho.mqtt.client as mqtt

STREAM = os.environ.get("PETLIBRO_STREAM", "")
OUT = os.environ.get("PETLIBRO_CLIPS_DIR", "")
CLIP_SECONDS = 180
COOLDOWN_SECONDS = 30
BROKER = os.environ.get("PETLIBRO_BROKER", "127.0.0.1")
SERIAL = os.environ.get("PETLIBRO_SERIAL", "EXAMPLE_SERIAL")
TOPIC = f"dl/PLAF203/{SERIAL}/device/event/post"
CREDS = os.environ.get("PETLIBRO_CREDS", "/root/petlibro-cam-creds.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",

)
log = logging.getLogger("recorder")

state = {"active": False, "retry_at": 0.0}


def record(kind: str) -> None:
    if state["active"] or time.time() < state["retry_at"]:
        return
    state["active"] = True
    now = time.localtime()
    day = time.strftime("%Y-%m-%d", now)
    ts = time.strftime("%Y%m%d_%H%M%S", now)
    d = os.path.join(OUT, day)
    path = os.path.join(d, f"tower_{kind}_{ts}.mp4")
    partial = path + ".part"

    def run() -> None:
        try:
            if not os.path.isdir(OUT) or os.path.islink(OUT):
                raise OSError("clips directory missing or symlinked")
            if shutil.disk_usage(OUT).free < 100 * 1024 * 1024:
                raise OSError("less than 100 MiB free in clips directory")
            if os.path.islink(d):
                raise OSError("date directory is a symlink")
            os.makedirs(d, exist_ok=True)
            log.info("recording %s -> %s", kind, path)
            subprocess.run(
                [
                    "ffmpeg", "-y", "-rtsp_transport", "tcp",
                    "-timeout", "20000000",
                    "-i", STREAM, "-t", str(CLIP_SECONDS),
                    "-c", "copy", "-movflags", "+faststart", "-f", "mp4", partial,
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=CLIP_SECONDS + 90,
            )
            size = os.path.getsize(partial)
            if size == 0:
                raise OSError("empty clip")
            os.replace(partial, path)
            log.info("clip done: %s (%d bytes)", path, size)
        except subprocess.CalledProcessError as exc:
            log.warning("ffmpeg failed with exit status %s", exc.returncode)
        except subprocess.TimeoutExpired:
            log.warning("ffmpeg exceeded clip timeout")
        except Exception as exc:  # noqa: BLE001
            log.warning("clip failed: %s", exc)
        finally:
            state["active"] = False
            state["retry_at"] = time.time() + COOLDOWN_SECONDS

    try:
        threading.Thread(target=run, daemon=True).start()
    except Exception:
        state["active"] = False
        raise


def on_message(_c, _u, msg) -> None:
    if msg.retain or msg.topic != TOPIC or state["active"] or time.time() < state["retry_at"]:
        return
    try:
        payload = json.loads(msg.payload)
    except Exception:  # noqa: BLE001
        return
    if not isinstance(payload, dict):
        return
    cmd = payload.get("cmd")
    if cmd == "DETECTION_EVENT" and payload.get("type") == "MOTION":
        record("motion")
    elif cmd == "GRAIN_OUTPUT_EVENT":
        record("feed")


def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        client.subscribe(TOPIC, qos=1)
    else:
        log.error("broker rejected connection: %s", reason_code)


def main() -> None:
    if (not SERIAL.isalnum() or SERIAL.startswith(("EXAMPLE", "YOUR"))
            or not STREAM.startswith(("rtsp://", "rtsps://")) or not os.path.isabs(OUT)):
        raise ValueError("Set PETLIBRO_SERIAL, PETLIBRO_STREAM (RTSP), and absolute PETLIBRO_CLIPS_DIR")
    creds = json.load(open(CREDS))
    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id="petlibro-cam-recorder",
        protocol=mqtt.MQTTv311,
    )
    client.username_pw_set(creds["username"], creds["password"])
    client.on_message = on_message
    client.on_connect = on_connect
    client.reconnect_delay_set(min_delay=2, max_delay=30)
    client.connect_async(BROKER, 1883, 30)
    log.info("recorder up; listening on %s", TOPIC)
    client.loop_forever(retry_first_connection=True)


if __name__ == "__main__":
    main()
