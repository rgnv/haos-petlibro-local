# Polar dashboard (Home Assistant)

Feed your Polar from Home Assistant with a button, and see live status.

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

## 2. Dashboard card (raw config, paste into any dashboard)

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
      - entity: sensor.polar_plate_position        # from the cloud integration, if installed
      - entity: sensor.polar_temperature
      - entity: binary_sensor.polar_wi_fi
  - type: logbook
    entities: []
    hours_to_show: 24
```

Replace the `sensor.polar_*` entities with whatever your setup exposes (the vendor
cloud integration stops updating once the feeder is local — wire the local MQTT
topics into HA MQTT sensors if you want live values, or ignore that section).

## 3. Automation example — feed every morning at 06:00

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
