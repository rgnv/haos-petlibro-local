# Polar dashboard (Home Assistant)

Feed your Polar from Home Assistant with a button, and see live status.

Two pieces are optional and independent:

1. a **feed script** + dashboard card that publish `polar/cmd/feed`;
2. a **discovery publisher** that exposes live status and the device controls as
   proper HA entities (no YAML needed).

## 1. One-time HA setup

Create a **helper script** (Settings → Automations & Scenes → Scripts → new, edit as YAML):

```yaml
alias: Polar feed
sequence:
  - service: mqtt.publish
    data:
      qos: "1"
      topic: polar/cmd/feed
      payload: >
        {"plate": {{ plate | default(1) }}, "duration": {{ duration | default(240) }}}
fields:
  plate:
    description: Which plate (1-3)
    example: 1
  duration:
    description: Door-open feeding window in seconds
    example: 240
mode: single
```

> HA must be able to reach the broker running on your Polar host (port 1884 by
> default). If HA's MQTT integration points elsewhere, add a broker bridge for this topic. HA mqtt.publish uses its configured MQTT integration.

## 2. Live entities via MQTT discovery

`polar/polar_ha_discovery.py` publishes retained discovery configs for one
device: online, temperature, WiFi signal, heartbeats, schedule summary, sound
and indicator switches, volume, ring-bell button and plate position. State comes
from the retained `polar/status` message; commands go to `polar/cmd/set|bell`.

The service does not publish these itself, and Home Assistant's broker is
usually a different host, so bridge the discovery topics and run the publisher
on the broker host:

```ini
# /etc/mosquitto/conf.d/polar-bridge-ha.conf  (mode 0640, owner root:mosquitto —
# mosquitto re-reads config files as its unprivileged user on SIGHUP)
connection polar-to-ha
address 192.0.2.10:1883
remote_username ha-mqtt-user
remote_password ********
remote_clientid polar-bridge
cleansession true
topic polar/# both 1
topic dl/PLAF109/# both 1
topic homeassistant/+/polar_feeder/# out 1
```

```bash
sudo install -m 0755 polar/polar_ha_discovery.py /usr/local/bin/polar-ha-discovery
sudo install -m 0644 polar/systemd/polar-ha-discovery.service \
                        polar/systemd/polar-ha-discovery.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now polar-ha-discovery.timer
sudo /usr/local/bin/polar-ha-discovery        # publish now
```

The timer republishes every five minutes, so a broker that loses its retained
store (in-memory MQTT pods are common) self-heals within that window, and the
bridge re-sends retained configs every time it reconnects. The publisher honours
`POLAR_HA_BROKER`, `POLAR_HA_PORT`, `POLAR_HA_TOPIC_PREFIX`,
`POLAR_HA_DISCOVERY_PREFIX`, `POLAR_HA_NODE`, `POLAR_DEVICE_ID` and
`POLAR_DEVICE_NAME`.

Entities land on one device named `Polar Wet Food Feeder` (ids are prefixed with
the device name, e.g. `binary_sensor.polar_wet_food_feeder_polar_online`).
Attribute-backed controls reflect the device's own values: the service asks the
feeder for its attributes on every (re)connect, so the switches do not sit at
`off` after a restart.

## 3. Dashboard card (raw config, paste into any dashboard)

```yaml
type: vertical-stack
cards:
  - type: markdown
    content: |
      # 🐾 Polar wet feeder
      Local control — no cloud.
  - type: button
    name: Feed plate 1 (4 min)
    icon: mdi:food-turkey
    tap_action:
      action: call-service
      service: script.polar_feed
      service_data:
        plate: 1
        duration: 240
  - type: button
    name: Feed plate 2 (4 min)
    icon: mdi:food-turkey
    tap_action:
      action: call-service
      service: script.polar_feed
      service_data:
        plate: 2
        duration: 240
  - type: entities
    title: Device
    entities:
      - binary_sensor.polar_wet_food_feeder_polar_online
      - sensor.polar_wet_food_feeder_polar_temperature
      - sensor.polar_wet_food_feeder_polar_wifi_signal
      - sensor.polar_wet_food_feeder_scheduled_feeds
      - switch.polar_wet_food_feeder_indicator_light
      - switch.polar_wet_food_feeder_sound
      - number.polar_wet_food_feeder_volume
      - button.polar_wet_food_feeder_ring_bell_2
      - select.polar_wet_food_feeder_plate_position_2
  - type: logbook
    entities: []
    hours_to_show: 24
```

Without the discovery publisher, point the card at whatever MQTT sensors you
created yourself. A vendor cloud integration stops updating once the feeder is
local — its entities freeze at the last cloud value, so disable (or ignore) them
rather than mixing them with the local ones.

## 4. Automation example — feed every morning at 06:00

```yaml
alias: Polar morning feed
trigger:
  - platform: time
    at: "06:00:00"
action:
  - service: script.polar_feed
    data:
      plate: 1
      duration: 240
mode: single
```

(Or skip HA entirely and use the service's built-in schedule file —
`/root/polar_schedule.json` on the Polar host.)
