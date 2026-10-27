#!/usr/bin/env python3
"""Publish (retained) the Home Assistant MQTT-discovery configs for a Polar feeder.

The local-cloud service deliberately does not talk to Home Assistant, so the
dashboard needs discovery configs. Publish them here, on the broker the service
uses, and let a bridge carry them to Home Assistant's broker:

    connection polar-to-ha
    address <HA broker>:1883
    remote_username <user>
    remote_password <password>
    remote_clientid polar-bridge
    cleansession true
    topic <prefix>/# both 1
    topic dl/PLAF109/# both 1
    topic homeassistant/+/polar_feeder/# out 1

Every publish is retained and idempotent, so this is safe to run repeatedly:
the supplied systemd timer runs it every five minutes and after boot, which
restores the entities whenever a broker loses its retained store.

Entities (state from the retained ``polar/status`` message, commands on
``polar/cmd/*``):

    binary_sensor  online
    sensor         temperature, rssi, heartbeat, schedule
    switch         sound, indicator light
    number         volume
    button         ring bell
    select         plate position

Environment:
    POLAR_HA_BROKER            broker host (default 127.0.0.1)
    POLAR_HA_PORT             broker port (default 1884)
    POLAR_HA_TOPIC_PREFIX     command/status prefix (default polar)
    POLAR_HA_DISCOVERY_PREFIX discovery prefix (default homeassistant)
    POLAR_HA_NODE             discovery node id (default polar_feeder)
    POLAR_DEVICE_ID           HA device identifier (default polar_plaf109_local)
    POLAR_DEVICE_NAME         HA device name (default Polar Wet Food Feeder)
"""
import json
import os
import subprocess
import sys

BROKER = os.environ.get('POLAR_HA_BROKER', '127.0.0.1')
PORT = os.environ.get('POLAR_HA_PORT', '1884')
PREFIX = os.environ.get('POLAR_HA_TOPIC_PREFIX', 'polar')
DISCOVERY = os.environ.get('POLAR_HA_DISCOVERY_PREFIX', 'homeassistant')
NODE = os.environ.get('POLAR_HA_NODE', 'polar_feeder')
DEVICE = {
    'identifiers': [os.environ.get('POLAR_DEVICE_ID', 'polar_plaf109_local')],
    'name': os.environ.get('POLAR_DEVICE_NAME', 'Polar Wet Food Feeder'),
    'manufacturer': 'Petlibro (local)',
    'model': 'PLAF109',
}
STATUS = '%s/status' % PREFIX
CMD_SET = '%s/cmd/set' % PREFIX
CMD_BELL = '%s/cmd/bell' % PREFIX


def cfg(**kw):
    out = {'device': DEVICE}
    out.update(kw)
    return out


CONFIGS = {
    'binary_sensor/%s/online' % NODE: cfg(
        name='Online',
        unique_id='polar_feeder_online',
        device_class='connectivity',
        state_topic=STATUS,
        value_template="{{ 'ON' if value_json.online else 'OFF' }}",
        payload_on='ON',
        payload_off='OFF',
    ),
    'sensor/%s/temperature' % NODE: cfg(
        name='Temperature',
        unique_id='polar_feeder_temp',
        device_class='temperature',
        state_class='measurement',
        unit_of_measurement='°C',
        suggested_display_precision=1,
        state_topic=STATUS,
        value_template='{{ value_json.temperature }}',
    ),
    'sensor/%s/rssi' % NODE: cfg(
        name='WiFi signal',
        unique_id='polar_feeder_rssi',
        device_class='signal_strength',
        state_class='measurement',
        unit_of_measurement='dBm',
        entity_category='diagnostic',
        state_topic=STATUS,
        value_template='{{ value_json.rssi }}',
    ),
    'sensor/%s/heartbeat' % NODE: cfg(
        name='Heartbeats',
        unique_id='polar_feeder_count',
        icon='mdi:heart-pulse',
        entity_category='diagnostic',
        state_topic=STATUS,
        value_template='{{ value_json.count }}',
    ),
    'sensor/%s/schedule' % NODE: cfg(
        name='Scheduled feeds',
        unique_id='polar_feeder_sched',
        icon='mdi:calendar-clock',
        state_topic=STATUS,
        value_template='{{ value_json.schedule | count }}',
        json_attributes_topic=STATUS,
        json_attributes_template='{{ {"entries": value_json.schedule} | tojson }}',
    ),
    'switch/%s/sound' % NODE: cfg(
        name='Sound',
        unique_id='polar_feeder_sound',
        icon='mdi:volume-high',
        state_topic=STATUS,
        value_template="{{ 'ON' if value_json.attrs.get('soundSwitch') else 'OFF' }}",
        state_on='ON',
        state_off='OFF',
        command_topic=CMD_SET,
        payload_on='{"attr":"soundSwitch","value":true}',
        payload_off='{"attr":"soundSwitch","value":false}',
    ),
    'switch/%s/light' % NODE: cfg(
        name='Indicator light',
        unique_id='polar_feeder_light2',
        icon='mdi:lightbulb',
        state_topic=STATUS,
        value_template="{{ 'ON' if value_json.attrs.get('lightSwitch') else 'OFF' }}",
        state_on='ON',
        state_off='OFF',
        command_topic=CMD_SET,
        payload_on='{"attr":"lightSwitch","value":true}',
        payload_off='{"attr":"lightSwitch","value":false}',
    ),
    'number/%s/volume' % NODE: cfg(
        name='Volume',
        unique_id='polar_feeder_volume',
        icon='mdi:volume-high',
        state_topic=STATUS,
        value_template='{{ value_json.attrs.volume }}',
        command_topic=CMD_SET,
        command_template='{"attr":"volume","value":{{ value | int }}}',
        min=0,
        max=100,
        step=10,
        mode='slider',
    ),
    'button/%s/bell' % NODE: cfg(
        name='Ring bell',
        unique_id='polar_feeder_bell',
        icon='mdi:bell-ring',
        command_topic=CMD_BELL,
        payload_press='{}',
    ),
    'select/%s/plate' % NODE: cfg(
        name='Plate position',
        unique_id='polar_feeder_plate',
        icon='mdi:rotate-3d-variant',
        options=['1', '2', '3'],
        state_topic=STATUS,
        value_template='{{ value_json.attrs.platePosition | default(1, true) | string }}',
        command_topic=CMD_SET,
        command_template='{"attr":"platePosition","value":{{ value | int }}}',
    ),
}


def publish(topic, payload):
    body = json.dumps(payload, separators=(',', ':'))
    proc = subprocess.run(
        ['mosquitto_pub', '-h', BROKER, '-p', PORT, '-q', '1', '-r', '-t', topic, '-m', body],
        capture_output=True, text=True, timeout=15,
    )
    if proc.returncode != 0:
        raise RuntimeError('%s: %s' % (topic, proc.stderr.strip()))
    return len(body)


def main():
    failures = 0
    for obj, payload in CONFIGS.items():
        topic = '%s/%s/config' % (DISCOVERY, obj)
        try:
            size = publish(topic, payload)
            print('ok   %-46s %4d bytes' % (topic, size))
        except Exception as exc:  # noqa: BLE001 - report and keep going
            failures += 1
            print('FAIL %-46s %s' % (topic, exc), file=sys.stderr)
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
