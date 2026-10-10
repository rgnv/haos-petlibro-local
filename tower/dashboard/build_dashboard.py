import asyncio, json, os
import websockets

TOKEN = os.environ["HASS_TOKEN"]
HA_BASE = os.environ.get("HA_URL", "http://homeassistant.local:8123").rstrip("/")
WS = HA_BASE.replace("http", "ws", 1) + "/api/websocket"

S = os.environ.get("PETLIBRO_SERIAL", "EXAMPLE_SERIAL")
if not S.isalnum() or S.startswith(("EXAMPLE", "YOUR")):
    raise ValueError("Set PETLIBRO_SERIAL to your feeder serial")
E = lambda suffix: f"petlibro_cat_feeder_{S.lower()}_{suffix}"

def ent(eid, name=None):
    c = {"entity": eid}
    if name:
        c["name"] = name
    return c

views = [{
    "title": "Tower",
    "path": "tower",
    "icon": "mdi:cat",
    "cards": [
        {"type": "picture-entity", "entity": "camera.tower_feeder",
         "camera_view": "live", "aspect_ratio": "16:9", "show_name": False,
         "show_state": False},
        {"type": "entities", "title": "🍽 Feed Controls", "state_color": True, "entities": [
            ent(f"button.{E('dispense_food_now')}", "Dispense Food Now"),
            ent(f"number.{E('manual_feed_portions')}", "Feed Portions"),
            ent(f"sensor.{E('dispensing_status')}", "Dispenser Status"),
            ent(f"sensor.{E('last_dispense_completed')}", "Last Feed Completed"),
            ent(f"sensor.{E('last_dispense_portions')}", "Last Feed Portions"),
            ent(f"sensor.{E('feeder_motor_state')}", "Motor State"),
            ent(f"binary_sensor.{E('low_food')}", "Low Food"),
            ent(f"binary_sensor.{E('food_outlet_blocked')}", "Outlet Blocked"),
        ]},
        {"type": "entities", "title": "📅 Feeding Schedules (edit as JSON)", "entities": [
            ent(f"text.{E(f'feeding_schedule_{i}')}", f"Schedule {i}") for i in range(1, 10)
        ]},
        {"type": "entities", "title": "📹 Camera & Recording", "state_color": True, "entities": [
            ent(f"switch.{E('camera')}", "Camera"),
            ent(f"select.{E('feeder_camera_resolution')}", "Resolution"),
            ent(f"select.{E('ir_night_vision')}", "Night Vision"),
            ent(f"switch.{E('local_video_recording')}", "Local Recording"),
            ent(f"select.{E('local_recording_mode')}", "Recording Mode"),
            ent(f"switch.{E('record_scheduled_meals')}", "Record Scheduled Meals"),
            ent(f"switch.{E('record_manual_feeds')}", "Record Manual Feeds"),
            ent(f"switch.{E('meal_video_recording')}", "Meal Video Recording"),
            ent(f"switch.{E('video_watermark')}", "Video Watermark"),
            ent(f"switch.{E('cloud_video_recording')}", "Cloud Video Recording"),
        ]},
        {"type": "entities", "title": "👁 Motion & Sound Detection", "state_color": True, "entities": [
            ent(f"switch.{E('motion_detection')}", "Motion Detection"),
            ent(f"select.{E('motion_detection_range')}", "Motion Range"),
            ent(f"select.{E('motion_detection_sensitivity')}", "Motion Sensitivity"),
            ent(f"switch.{E('sound_detection')}", "Sound Detection"),
            ent(f"select.{E('sound_detection_sensitivity')}", "Sound Sensitivity"),
            ent(f"switch.{E('device_sound')}", "Device Sound"),
            ent(f"number.{E('device_sound_volume')}", "Sound Volume"),
            ent(f"switch.{E('button_lights')}", "Button Lights"),
        ]},
        {"type": "entities", "title": "⚙️ Feeding Options", "state_color": True, "entities": [
            ent(f"switch.{E('automatic_change_mode')}", "Auto Change Mode"),
            ent(f"number.{E('automatic_change_threshold')}", "Auto Change Threshold"),
            ent(f"select.{E('bowl_setup')}", "Bowl Setup"),
            ent(f"text.{E('meal_call_audio_url')}", "Meal Call Audio URL"),
            ent(f"switch.{E('meal_call')}", "Meal Call"),
        ]},
        {"type": "entities", "title": "📊 Status", "state_color": True, "entities": [
            ent(f"binary_sensor.{E('connection')}", "Connection"),
            ent(f"sensor.{E('battery_level')}", "Battery"),
            ent(f"sensor.{E('active_power_source')}", "Power Source"),
            ent(f"sensor.{E('wi_fi_ssid')}", "WiFi"),
            ent(f"sensor.{E('wi_fi_rssi')}", "WiFi Signal"),
            ent(f"sensor.{E('sd_card_status')}", "SD Card"),
            ent(f"binary_sensor.{E('error_state')}", "Error State"),
            ent(f"sensor.{E('error_message')}", "Error Message"),
            ent(f"sensor.{E('last_clock_sync')}", "Last Clock Sync"),
        ]},
        {"type": "entities", "title": "🔧 System", "entities": [
            ent(f"button.{E('reconnect_wi_fi')}", "Reconnect WiFi"),
            ent(f"button.{E('reboot')}", "Reboot Feeder"),
            ent(f"button.{E('format_sd_card')}", "Format SD Card"),
            ent(f"button.{E('check_for_state_agent_updates')}", "Check State Agent Updates"),
            ent(f"update.{E('state_agent_firmware')}", "State Agent Firmware"),
            ent(f"sensor.{E('software_version')}", "Software Version"),
            ent(f"sensor.{E('hardware_version')}", "Hardware Version"),
            ent(f"sensor.{E('serial_number')}", "Serial"),
            ent(f"sensor.{E('uuid')}", "UUID"),
            ent(f"button.{E('factory_reset')}", "Factory Reset (careful)"),
        ]},
    ],
}]

config = {"title": "Tower Feeder", "views": views}


async def call(ws, mid, msg):
    msg["id"] = mid
    await ws.send(json.dumps(msg))
    while True:
        resp = json.loads(await ws.recv())
        if resp.get("id") == mid:
            if not resp.get("success"):
                raise RuntimeError(resp.get("error", resp))
            return resp


async def main():
    async with websockets.connect(WS, max_size=2**24) as ws:
        await ws.recv()
        await ws.send(json.dumps({"type": "auth", "access_token": TOKEN}))
        assert json.loads(await ws.recv())["type"] == "auth_ok"
        print("authed")

        dashboards = await call(ws, 1, {"type": "lovelace/dashboards/list"})
        if not any(d["url_path"] == "tower-feeder" for d in dashboards["result"]):
            await call(ws, 2, {"type": "lovelace/dashboards/create",
                               "url_path": "tower-feeder", "title": "Tower Feeder",
                               "icon": "mdi:cat", "show_in_sidebar": True,
                               "require_admin": False})

        r = await call(ws, 3, {"type": "lovelace/config/save",
                               "url_path": "tower-feeder", "config": config})
        print("config save:", r.get("success"), r.get("error", {}).get("message", "")[:200])

asyncio.run(main())
