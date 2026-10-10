"""Create/update the motion automation from the reviewed JSON example.

Edit camera/notify targets in the example before running this script.
"""
import json
import os
from pathlib import Path
import urllib.request

TOKEN = os.environ["HASS_TOKEN"]
BASE = os.environ["HA_URL"].rstrip("/")
SERIAL = os.environ["PETLIBRO_SERIAL"]
if not SERIAL.isalnum() or SERIAL.startswith(("EXAMPLE", "YOUR")):
    raise ValueError("Set PETLIBRO_SERIAL to your feeder serial")
automation = json.loads(Path(__file__).with_name("motion_snapshot_automation.example.json").read_text())
if any(a.get("service", "").endswith(("yourphone", "yourwatch")) for a in automation["action"]):
    raise ValueError("Edit notify targets in motion_snapshot_automation.example.json first")
automation["trigger"][0]["topic"] = f"dl/PLAF203/{SERIAL}/device/event/post"
req = urllib.request.Request(
    f"{BASE}/api/config/automation/config/feeder_motion_snapshot",
    data=json.dumps(automation).encode(),
    headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(req, timeout=20) as response:
    print("create automation:", response.status)
