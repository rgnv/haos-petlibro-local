"""Release regression checks: mocks only, temporary files stay inside this repo."""
import importlib.util
import json
import os
import shlex
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'polar'))
import polar_local_cloud as cloud
import polar_local_feed as feed


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ReleaseTests(unittest.TestCase):
    def test_invalid_serial_never_publishes(self):
        for serial in ('', 'EXAMPLE_SERIAL', 'YOUR_SERIAL_HERE', 'bad/serial', 'x+#'):
            with self.subTest(serial=serial), patch.object(feed, 'SERIAL', serial), patch.object(feed.subprocess, 'run') as run:
                with self.assertRaises(ValueError):
                    feed.send_feed()
                run.assert_not_called()

    def test_fractional_timezone_is_not_rounded_down(self):
        with patch.object(cloud, 'TZ', ZoneInfo('Asia/Kathmandu')):
            payload = cloud.ntp_payload(datetime(2026, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(payload['timezone'], 5.75)
        self.assertEqual(payload['timezoneOffsetSeconds'], 20700)
        self.assertEqual(payload['nextDSTTransitionTs'], 0)

    def test_invalid_pin_addresses(self):
        for host, port in (('', 1884), ('192.168.x.x', 1884), ('127.0.0.1', 1884), ('0.0.0.0', 1884), ('192.0.2.20', 65536)):
            with self.assertRaises(ValueError):
                cloud.validate_local_address(host, port)
        cloud.validate_local_address('192.0.2.20', 1884)

    def test_installers_reject_missing_config_before_external_work(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith(('POLAR_', 'PETLIBRO_'))}
        for script, args in (('install-polar.sh', []), ('install-tower.sh', ['host.invalid']),
                             ('install-tower-recorder.sh', ['/clips'])):
            result = subprocess.run(['bash', str(ROOT / 'scripts' / script), *args], env=env,
                                    capture_output=True, text=True, timeout=5)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn('==> Checking dependencies', result.stdout)

    def test_polar_custom_port_install_uses_one_config(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            work = Path(directory)
            for path in ('etc/systemd/system', 'root', 'bin'):
                (work / path).mkdir(parents=True)
            script = (ROOT / 'scripts/install-polar.sh').read_text()
            script = script.replace('REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"',
                                    'REPO_ROOT=' + shlex.quote(str(ROOT)))
            script = script.replace('/etc/', str(work / 'etc') + '/').replace('/root/', str(work / 'root') + '/')
            installer = work / 'install.sh'
            installer.write_text(script)
            for name in ('mosquitto', 'mosquitto_pub', 'mosquitto_sub', 'socat', 'systemctl', 'sleep', 'polar-local-cloud', 'polar-relay'):
                executable = work / 'bin' / name
                executable.write_text('#!/bin/sh\nexit 0\n')
                executable.chmod(0o755)
            executable = work / 'bin/python3'
            executable.write_text('#!/bin/sh\n[ "$1" != -c ] || exit 0\nexec ' + shlex.quote(sys.executable) + ' "$@"\n')
            executable.chmod(0o755)
            # Root check and package manager are stubbed; no privilege or network needed.
            executable = work / 'bin/id'
            executable.write_text('#!/bin/sh\necho 0\n')
            executable.chmod(0o755)
            for name in ('apt-get', 'pip3'):
                executable = work / 'bin' / name
                executable.write_text('#!/bin/sh\nexit 99\n')
                executable.chmod(0o755)
            subprocess.run(['bash', str(installer), '--serial', 'TESTSERIAL', '--broker-ip',
                            '192.0.2.20', '--port', '2884', '--timezone', 'UTC'],
                           env=dict(os.environ, PETLIBRO_RUST_BIN_DIR=str(work / 'bin'), PATH=str(work / 'bin') + ':' + os.environ['PATH']),
                           check=True, capture_output=True, timeout=10)
            config = (work / 'etc/default/polar').read_text()
            self.assertIn('POLAR_PORT=2884\n', config)
            self.assertIn('POLAR_LOCAL_PORT=2884\n', config)
            self.assertIn('POLAR_SERIAL=TESTSERIAL\n', config)
            self.assertEqual((work / 'etc/default/polar').stat().st_mode & 0o777, 0o600)
            self.assertIn('listener 2884 ', (work / 'etc/mosquitto/conf.d/polar-capture.conf').read_text())
            self.assertEqual((work / 'etc/systemd/system/polar-local-cloud.service').read_text(),
                             (ROOT / 'polar/systemd/polar-local-cloud.service').read_text())

    def test_takeover_helpers_share_config_and_relay_exists(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            work = Path(directory)
            config = work / 'polar.env'
            config.write_text('POLAR_SERIAL=TESTSERIAL\nPOLAR_PORT=2884\nPOLAR_LOCAL_HOST=192.0.2.20\n')
            wizard = work / 'wizard.sh'
            wizard.write_text((ROOT / 'scripts/polar-takeover.sh').read_text().replace('/etc/default/polar', str(config)).replace('/root/polar-relay', str(work / 'polar-relay')))
            for name, body in {'id': 'echo 0', 'sleep': 'exit 0', 'systemctl': 'exit 0',
                               'timeout': 'shift; exec "$@"', 'mosquitto_sub': 'echo "$@"',
                               'polar-relay': 'echo "$POLAR_SERIAL $POLAR_LOCAL_HOST $POLAR_PORT $*"',
                               'python3': 'echo "$POLAR_SERIAL $POLAR_LOCAL_HOST $POLAR_PORT $*"'}.items():
                tool = work / name
                tool.write_text('#!/bin/sh\n' + body + '\n')
                tool.chmod(0o755)
            env = dict(os.environ, PATH=str(work) + ':' + os.environ['PATH'])
            for command in ('serve', 'pin', 'relay'):
                result = subprocess.run(['bash', str(wizard), command], env=env,
                                        capture_output=True, text=True, check=True, timeout=5)
                self.assertIn('2884', result.stdout)
                if command != 'serve':
                    self.assertIn('TESTSERIAL 192.0.2.20', result.stdout)

    def test_sanitizer_removes_short_connect_secrets_and_topic_serial(self):
        sanitizer = load('sanitizer', 'scripts/sanitize-capture.py')
        self.assertEqual(sanitizer.sanitize_mqtt_lines(b"prefix MQTT\\x00a\\x00xy\nnext"),
                         b'<MQTT_CONNECT_REDACTED>\nnext')
        text = 'dl/PLAF109/TESTSERIAL/device/event/post {"password":"a\\\"b"}'
        for _, rx, replacement in sanitizer.TEXT_RULES:
            if replacement is not None:
                text = rx.sub(replacement, text)
        self.assertNotIn('TESTSERIAL', text)
        self.assertNotIn('a\\\"b', text)

    def test_recorder_atomic_success_failure_reconnect_and_malformed_event(self):
        mqtt = Mock()
        with patch.dict(sys.modules, {'paho': Mock(), 'paho.mqtt': Mock(), 'paho.mqtt.client': mqtt}):
            sys.modules['paho'].mqtt = sys.modules['paho.mqtt']
            sys.modules['paho.mqtt'].client = mqtt
            recorder = load('recorder', 'tower/recorder/petlibro-cam-recorder.py')
        with tempfile.TemporaryDirectory(dir=ROOT) as directory, patch.object(recorder, 'OUT', directory), patch.object(recorder.threading, 'Thread') as thread:
            thread.side_effect = lambda **kw: SimpleNamespace(start=kw['target'])
            def ffmpeg(argv, **kwargs):
                self.assertTrue(kwargs['check'])
                Path(argv[-1]).write_bytes(b'clip')
            with patch.object(recorder.subprocess, 'run', side_effect=ffmpeg):
                recorder.record('motion')
            self.assertEqual(len(list(Path(directory).rglob('*.mp4'))), 1)
            self.assertFalse(list(Path(directory).rglob('*.part')))
            recorder.state['retry_at'] = 0
            with patch.object(recorder.subprocess, 'run', side_effect=OSError('failed')):
                recorder.record('feed')
            self.assertFalse(recorder.state['active'])
            self.assertFalse(list(Path(directory).rglob('tower_feed*.mp4')))
            recorder.state['retry_at'] = 0
            with patch.object(recorder.subprocess, 'run', side_effect=subprocess.CalledProcessError(
                    1, ['ffmpeg', 'rtsp://user:EXAMPLE_PASSWORD@camera/stream'])):
                with self.assertLogs('recorder', level='WARNING') as logs:
                    recorder.record('feed')
            self.assertNotIn('EXAMPLE_PASSWORD', ' '.join(logs.output))
        client = Mock()
        recorder.on_connect(client, None, None, 0)
        recorder.on_connect(client, None, None, 0)
        self.assertEqual(client.subscribe.call_count, 2)
        recorder.state['retry_at'] = 0
        with patch.object(recorder, 'record') as record:
            for data in ([], None, 1):
                recorder.on_message(None, None, SimpleNamespace(topic=recorder.TOPIC, payload=json.dumps(data), retain=False))
            recorder.on_message(None, None, SimpleNamespace(topic=recorder.TOPIC, payload='{"cmd":"GRAIN_OUTPUT_EVENT"}', retain=True))
            record.assert_not_called()

    def test_retention_escapes_root_and_preserves_other_files(self):
        with tempfile.TemporaryDirectory(prefix='clips.[test]', dir=ROOT) as directory:
            day = Path(directory) / '2000-01-01'
            day.mkdir()
            clip = day / 'tower_motion_20000101_000000.mp4'
            other = day / 'other.mp4'
            for file in (clip, other):
                file.write_bytes(b'old')
                os.utime(file, (1, 1))
            subprocess.run(['bash', str(ROOT / 'tower/recorder/petlibro-cam-retention.sh')],
                           env=dict(os.environ, PETLIBRO_CLIPS_DIR=directory), check=True)
            self.assertFalse(clip.exists())
            self.assertTrue(other.exists())


if __name__ == '__main__':
    unittest.main()
