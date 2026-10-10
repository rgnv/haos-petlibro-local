#!/usr/bin/env python3
"""Send a local feed command to the Polar feeder (no cloud).

Command format discovered 2026-10-09 from a transparent relay capture:
a plan-write with feedNowState:true to the magic manual slot (planId 666666666)."""
import argparse
import json
import os
import subprocess

from polar_local_cloud import feed_payload, validate_serial

SERIAL = os.environ.get("POLAR_SERIAL", "EXAMPLE_SERIAL")
BROKER = os.environ.get("POLAR_BROKER", "127.0.0.1")
PORT = os.environ.get("POLAR_PORT", "1884")
TOPIC = f"dl/PLAF109/{SERIAL}/device/service/sub"


def send_feed(plate: int = 1, duration: int = 60) -> str:
    validate_serial(SERIAL)
    payload = feed_payload(plate, duration)
    p = json.dumps(payload, separators=(",", ":"))
    r = subprocess.run(["mosquitto_pub", "-h", BROKER, "-p", PORT, "-t", TOPIC, "-q", "1", "-m", p],
                       capture_output=True, text=True, timeout=15)
    if r.returncode != 0:
        raise RuntimeError(f"publish failed: {r.stderr}")
    return payload["msgId"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plate", type=int, nargs="?", default=1)
    parser.add_argument("duration", type=int, nargs="?", default=60)
    args = parser.parse_args()
    try:
        mid = send_feed(args.plate, args.duration)
    except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired) as ex:
        parser.exit(1, f"{ex}\n")
    print(f"feed command sent (plate={args.plate}, duration={args.duration}s, msgId={mid})")
