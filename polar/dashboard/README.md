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

## 4. Schedules and per-plate custom timings (recommended)

The service owns the schedule and exposes it over MQTT (`polar/cmd/schedule`,
retained state on `polar/schedule`). Ready-made HA helpers and scripts build a
full authoring UI on top of it:

```bash
# HA config dir (e.g. /mnt/data/supervisor/homeassistant on HAOS)
cp polar/ha/polar_local.yaml    "$HA_CONFIG/packages/polar_local.yaml"
cat polar/ha/polar_scripts.yaml >> "$HA_CONFIG/scripts.yaml"
```

Then reload without a restart — Developer tools → Actions:

```
input_boolean.reload, input_datetime.reload, input_number.reload,
input_select.reload, script.reload
```

You get:

* `input_number.polar_feed_duration` + `script.polar_feed_plate_1|2|3` — feed any
  plate for a custom door-open time;
* a form (`input_datetime.polar_schedule_time`,
  `input_select.polar_schedule_plate`, `input_number.polar_schedule_duration`,
  seven weekday toggles) and `script.polar_schedule_add`;
* `script.polar_schedule_enable|disable|remove|clear` with
  `input_number.polar_schedule_entry` to pick the entry number;
* `sensor.polar_wet_food_feeder_scheduled_feeds` — count, with the entries list in
  its `entries` attribute (rendered by a markdown card).

Card config for the editor and the live list:

```yaml
type: vertical-stack
cards:
  - type: entities
    title: Add a scheduled feed
    entities:
      - entity: input_datetime.polar_schedule_time
        name: Time
      - entity: input_select.polar_schedule_plate
        name: Plate
      - entity: input_number.polar_schedule_duration
        name: Duration
      - input_boolean.polar_schedule_mon
      - input_boolean.polar_schedule_tue
      - input_boolean.polar_schedule_wed
      - input_boolean.polar_schedule_thu
      - input_boolean.polar_schedule_fri
      - input_boolean.polar_schedule_sat
      - input_boolean.polar_schedule_sun
      - entity: script.polar_schedule_add
        name: Add schedule
  - type: markdown
    title: Scheduled feeds
    content: >-
      {% set entries = state_attr('sensor.polar_wet_food_feeder_scheduled_feeds', 'entries') or [] %}
      {% set names = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'] %}
      {% for e in entries %}**{{ e.index }}.** {{ e.time }} · plate {{ e.plate }} ·
      {{ e.duration }} s · {% for d in e.days %}{{ names[d] }}{% if not loop.last %}, {% endif %}{% endfor %}{% if not e.enabled %} · disabled{% endif %}
      {% else %}**No scheduled feeds.**{% endfor %}
  - type: entities
    title: Manage schedules
    entities:
      - entity: input_number.polar_schedule_entry
        name: Entry number
      - script.polar_schedule_enable
      - script.polar_schedule_disable
      - script.polar_schedule_remove
      - script.polar_schedule_clear
```

Prefer this over an HA `time` automation: the service scheduler survives HA
outages and there is no double-feed risk. Use an automation only for things the
schedule file cannot express (conditions, notifications).

## 5. Automation example — feed on a one-off event

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

(Or skip HA entirely and publish `polar/cmd/schedule` yourself — the schedule
lives on the Polar host and keeps running when HA is down.)
