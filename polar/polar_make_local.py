#!/usr/bin/env python3
"""Push DEVICE_CONFIG_SYNC to the Polar pointing its MQTT at our local broker.
This is the permanent 'flash': the device persists mqttAddr in its own storage."""
import json
import os
import subprocess

from polar_local_cloud import config_sync_payload, validate_serial, validate_local_address

SERIAL = os.environ.get("POLAR_SERIAL", "EXAMPLE_SERIAL")
TOPIC = f"dl/PLAF109/{SERIAL}/device/service/sub"
BROKER = os.environ.get("POLAR_LOCAL_HOST", "")
PORT = int(os.environ.get("POLAR_LOCAL_PORT", "1884"))
PUB_HOST = os.environ.get("POLAR_BROKER", "127.0.0.1")
PUB_PORT = str(os.environ.get("POLAR_PORT", PORT))


def main():
    try:
        validate_serial(SERIAL)
        validate_local_address(BROKER, PORT)
    except ValueError as ex:
        print(ex)
        return 2
    payload = config_sync_payload()
    payload["mqttAddr"] = [{"host": BROKER, "port": PORT}]
    p = json.dumps(payload, separators=(",", ":"))
    try:
        result = subprocess.run(
            ["mosquitto_pub", "-h", PUB_HOST, "-p", PUB_PORT, "-t", TOPIC,
             "-q", "1", "-m", p], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as ex:
        print("publish failed:", ex)
        return 1
    print("publish rc:", result.returncode, result.stderr.strip())
    print("msgId:", payload["msgId"])
    print("payload:", p)
    return 0 if result.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
