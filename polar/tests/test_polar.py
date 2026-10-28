"""Offline checks: no broker, subprocess, or socket connections are made."""
import importlib
import json
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from types import SimpleNamespace
from unittest.mock import Mock, patch

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import polar_local_cloud as cloud
import polar_local_feed as feed
import polar_make_local as make_local
import polar_relay as relay

# Deterministic timezone fixture, independent of deployment configuration.
cloud.TZ = ZoneInfo("America/Los_Angeles")


def utc(value):
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def message(topic, data, retain=False):
    return SimpleNamespace(topic=topic, payload=json.dumps(data).encode(), retain=retain)


class PayloadTests(unittest.TestCase):
    def test_ntp_exact_transitions(self):
        cases = [
            ("2026-01-01T12:34:56", -28800, "2026-03-08T10:00:00", -25200,
             "2026-11-01T09:00:00", -28800),
            ("2026-07-01T00:00:00", -25200, "2026-11-01T09:00:00", -28800,
             "2027-03-14T10:00:00", -25200),
            ("2026-11-01T09:00:00", -28800, "2027-03-14T10:00:00", -25200,
             "2027-11-07T09:00:00", -28800),
        ]
        for now, offset, first, next_offset, second, second_offset in cases:
            with self.subTest(now=now):
                payload = cloud.ntp_payload(utc(now))
                self.assertEqual(payload["ts"], int(utc(now).timestamp() * 1000))
                self.assertEqual(payload["timezoneOffsetSeconds"], offset)
                self.assertEqual(payload["nextDSTTransitionTs"], int(utc(first).timestamp() * 1000))
                self.assertEqual(payload["secondNextDSTTransitionTs"], int(utc(second).timestamp() * 1000))
                self.assertEqual(payload["nextDSTOffsetSeconds"], next_offset)
                self.assertEqual(payload["secondNextDSTOffsetSeconds"], second_offset)
                self.assertFalse(payload["calibrationTag"])

    def test_config_has_independent_address_and_ids(self):
        payload = cloud.config_sync_payload()
        self.assertEqual(payload["mqttAddr"], [cloud.LOCAL_MQTT])
        self.assertNotEqual(payload["msgId"], payload["linkId"])
        payload["mqttAddr"][0]["host"] = "changed"
        self.assertNotEqual(cloud.LOCAL_MQTT["host"], "changed")

    def test_feed_uses_device_timezone_and_unique_id(self):
        now = utc("2026-01-02T07:59:59")
        payload = cloud.feed_payload(2, 240, now)
        self.assertEqual(payload["plans"], [{
            "planId": 666666666, "executionTime": "23:59", "executionDay": "2026-01-01",
            "plate": 2, "feedingDuration": 240, "feedNowState": True}])
        self.assertEqual(payload["ts"], int(now.timestamp() * 1000))
        self.assertNotEqual(payload["msgId"], cloud.feed_payload(2, 240, now)["msgId"])

    def test_bad_feed_values(self):
        for bad in (True, False, 0, -1, 1.5, "1", None, [], {}, float("nan")):
            for args in ((bad, 60), (1, bad)):
                with self.subTest(args=args), self.assertRaises(ValueError):
                    cloud.feed_payload(*args)


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.client.is_connected.return_value = True
        self.client.publish.return_value.rc = 0
        self.service = cloud.PolarCloud(self.client)

    def test_echo_channel_command_matrix(self):
        for channel in ("heart", "ntp", "event", "service", "config", "system"):
            for direction in ("post", "sub"):
                for cmd in cloud.EVENT_ECHO_SET | {"WET_GRAIN_FEEDING_PLAN_SERVICE", "DEVICE_LOG_REPORT_EVENT", "NTP", "unknown"}:
                    topic = f"{cloud.BASE}/{channel}/{direction}"
                    result = cloud.echo_payload(topic, {"cmd": cmd, "msgId": "id"})
                    expected = channel == "event" and direction == "post" and cmd in cloud.EVENT_ECHO_SET
                    with self.subTest(topic=topic, cmd=cmd):
                        self.assertEqual(result is not None, expected)
                        if expected:
                            self.assertEqual(result["msgId"], "id")
                            self.assertEqual(result["code"], 0)
                            self.assertTrue(result["linkId"])

    def test_echo_self_guard_and_foreign_topics(self):
        topic = f"{cloud.BASE}/event/post"
        for link in (None, "", False, "our-link", []):
            self.assertIsNone(cloud.echo_payload(topic, {"cmd": "DEVICE_START_EVENT", "linkId": link}))
        for other in ("foreign/event/post", topic + "/extra", "prefix/" + topic):
            self.assertIsNone(cloud.echo_payload(other, {"cmd": "DEVICE_START_EVENT"}))
        for data in (None, [], 1, "text", {"cmd": []}, {"cmd": {}}):
            self.assertIsNone(cloud.echo_payload(topic, data))

    def test_progress_echo_preserves_exec_step(self):
        result = cloud.echo_payload(f"{cloud.BASE}/event/post", {
            "cmd": "WET_GRAIN_OUTPUT_EVENT", "execStep": "OPEN_DOOR"})
        self.assertEqual(result["execStep"], "OPEN_DOOR")

    def test_2099_reply_cannot_publish_or_feed(self):
        for channel in ("service", "config", "system", "event"):
            self.service.on_message(message(f"{cloud.BASE}/{channel}/post", {
                "cmd": "WET_GRAIN_FEEDING_PLAN_SERVICE", "code": 2099, "msgId": "loop"}))
        self.client.publish.assert_not_called()

    def test_bad_json_and_feed_values_do_not_escape_callback(self):
        for data in (None, [], 42, "text", {"cmd": []}, {"plate": "1"}, {"duration": -1}):
            self.service.on_message(message("polar/cmd/feed", data))
        for payload in (b"{", b"\xff", b"[" * 2000 + b"]" * 2000):
            self.service.on_message(SimpleNamespace(topic="polar/cmd/feed", payload=payload, retain=False))
        self.client.publish.assert_not_called()

    def test_retained_and_linked_messages_have_no_side_effects(self):
        for topic, data in (("polar/cmd/feed", {}), ("polar/cmd/config", {}),
                            (f"{cloud.BASE}/event/post", {"cmd": "DEVICE_START_EVENT"}),
                            (f"{cloud.BASE}/ntp/post", {"cmd": "NTP"})):
            self.service.on_message(message(topic, data, retain=True))
            self.service.on_message(message(topic, dict(data, linkId=None)))
        self.client.publish.assert_not_called()
        self.assertIsNone(self.service.config_due)

    def test_boot_echo_is_nonblocking_and_coalesces_config(self):
        msg = message(f"{cloud.BASE}/event/post", {"cmd": "DEVICE_START_EVENT"})
        with patch.object(cloud.time, "sleep", side_effect=AssertionError("blocking callback")):
            self.service.on_message(msg)
            due = self.service.config_due
            self.service.on_message(msg)
        self.assertEqual(self.service.config_due, due)
        self.assertEqual(self.client.publish.call_count, 2)
        for call in self.client.publish.call_args_list:
            self.assertEqual(call.args[0], f"{cloud.BASE}/event/sub")

    def test_ntp_and_manual_feed_routing(self):
        self.service.on_message(message(f"{cloud.BASE}/ntp/post", {"cmd": "NTP"}))
        self.assertEqual(self.client.publish.call_args.args[0], f"{cloud.BASE}/ntp/sub")
        self.service.on_message(message("polar/cmd/feed", {"plate": 2, "duration": 240}))
        topic, raw = self.client.publish.call_args.args
        self.assertEqual(topic, f"{cloud.BASE}/service/sub")
        self.assertEqual(json.loads(raw)["plans"][0]["plate"], 2)

    def test_publish_failures(self):
        self.client.is_connected.return_value = False
        self.assertFalse(self.service.pub("topic", {}))
        self.client.publish.assert_not_called()
        self.client.is_connected.return_value = True
        self.client.publish.return_value.rc = 15
        self.assertFalse(self.service.pub("topic", {}))

    def test_deferred_boot_config_runs_in_worker(self):
        self.service.config_due = 0
        with patch.object(cloud, "open", side_effect=FileNotFoundError), patch.object(
                cloud.time, "sleep", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.service.sched_loop()
        self.assertIsNone(self.service.config_due)
        topic, raw = self.client.publish.call_args.args
        self.assertEqual(topic, f"{cloud.BASE}/service/sub")
        self.assertEqual(json.loads(raw)["cmd"], "DEVICE_CONFIG_SYNC")


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.client.is_connected.return_value = True
        self.client.publish.return_value.rc = 0
        self.service = cloud.PolarCloud(self.client)

    def test_missed_minute_and_duplicate_rows(self):
        entries = [{"time": "06:00"}] * 2
        self.service.sched_tick(entries, utc("2026-10-09T12:59:59"))
        self.service.sched_tick(entries, utc("2026-10-09T13:02:00"))
        self.service.sched_tick(entries, utc("2026-10-09T13:02:20"))
        self.assertEqual(self.client.publish.call_count, 1)

    def test_fall_back_fires_once_and_spring_gap_skips(self):
        entries = [{"time": "01:30"}]
        for now in ("2026-11-01T08:30:00", "2026-11-01T09:30:00"):
            self.service.sched_tick(entries, utc(now))
        self.assertEqual(self.client.publish.call_count, 1)
        self.client.publish.reset_mock()
        self.service.sched_tick([{"time": "02:30"}], utc("2027-03-14T09:59:00"))
        self.service.sched_tick([{"time": "02:30"}], utc("2027-03-14T10:01:00"))
        self.client.publish.assert_not_called()

    def test_midnight_day_filter_and_stale_limit(self):
        before = int(utc("2026-10-10T06:59:00").timestamp()) // 60
        entry = {"time": "23:59", "days": [4]}
        entry_id = cloud.content_id({"time": "23:59", "plate": 1, "duration": 240, "days": [4]})
        self.assertEqual(cloud.schedule_due(entry, utc("2026-10-10T07:01:00"), before),
                         ("2026-10-09", "23:59", 1, entry_id))
        self.assertIsNone(cloud.schedule_due(entry, utc("2026-10-10T07:06:00"), before))
        self.assertIsNone(cloud.schedule_due(dict(entry, days=[5]), utc("2026-10-10T07:01:00"), before))

    def test_invalid_entries_do_not_starve_good_entry(self):
        bad = [None, {}, {"time": "6:00"}, {"time": "24:00"}, {"time": "06:60"},
               {"time": "06:00", "days": [True]}, {"time": "06:00", "plate": 0}]
        self.service.sched_tick(bad + [{"time": "06:00"}], utc("2026-10-09T13:00:00"))
        self.assertEqual(self.client.publish.call_count, 1)
        with self.assertRaises(ValueError):
            self.service.sched_tick({}, utc("2026-10-09T13:00:00"))

    def test_disconnect_does_not_mark_feed_sent(self):
        entries = [{"time": "06:00"}]
        self.service.sched_tick(entries, utc("2026-10-09T12:59:00"))
        self.client.is_connected.return_value = False
        self.service.sched_tick(entries, utc("2026-10-09T13:00:00"))
        self.assertFalse(self.service.last_fired)
        self.client.is_connected.return_value = True
        self.service.sched_tick(entries, utc("2026-10-09T13:02:00"))
        self.assertEqual(self.client.publish.call_count, 1)

    def test_queue_rejection_retries_within_catchup_window(self):
        entries = [{"time": "06:00"}]
        self.client.publish.return_value.rc = 15
        self.service.sched_tick(entries, utc("2026-10-09T13:00:00"))
        self.assertFalse(self.service.last_fired)
        self.client.publish.return_value.rc = 0
        self.service.sched_tick(entries, utc("2026-10-09T13:02:00"))
        self.assertEqual(len(self.service.last_fired), 1)


class ScheduleCommandTests(unittest.TestCase):
    """polar/cmd/schedule: validation, persistence and the retained state."""

    def setUp(self):
        self.client = Mock()
        self.client.is_connected.return_value = True
        self.client.publish.return_value.rc = 0
        self.service = cloud.PolarCloud(self.client)
        self.directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.directory, True)
        patcher = patch.object(cloud, "SCHEDULE_FILE", os.path.join(self.directory, "schedule.json"))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.path = cloud.SCHEDULE_FILE

    def send(self, **payload):
        self.service.on_message(message("polar/cmd/schedule", payload))

    def entries(self):
        with open(self.path) as handle:
            return json.load(handle)

    def state_publishes(self):
        return [call for call in self.client.publish.call_args_list
                if call.args[0] == "polar/schedule"]

    def test_add_persists_and_publishes_retained_state(self):
        self.send(action="add", time="06:30", plate=2, duration=180, days=[1, 3])
        saved = self.entries()
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["time"], "06:30")
        self.assertEqual(saved[0]["plate"], 2)
        self.assertEqual(saved[0]["duration"], 180)
        self.assertEqual(saved[0]["days"], [1, 3])
        self.assertTrue(saved[0]["enabled"])
        self.assertRegex(saved[0]["id"], r"^h[0-9a-f]{16}$")
        publishes = self.state_publishes()
        self.assertEqual(len(publishes), 1)
        state = json.loads(publishes[0].args[1])
        self.assertEqual(state["count"], 1)
        self.assertEqual(state["entries"][0]["index"], 1)
        self.assertTrue(publishes[0].kwargs["retain"])

    def test_duplicate_and_invalid_commands_are_ignored(self):
        self.send(action="add", time="06:30", plate=2, duration=180, days=[1])
        publishes = len(self.state_publishes())
        for bad in ({"action": "add", "time": "06:30", "plate": 2, "duration": 180, "days": [1]},
                    {"action": "add", "time": "6:30"},
                    {"action": "add", "time": "06:30", "plate": 4},
                    {"action": "add", "time": "06:30", "duration": 0},
                    {"action": "add", "time": "06:30", "days": []},
                    {"action": "add", "time": "06:30", "days": [7]},
                    {"action": "nonsense"},
                    {"action": "remove", "index": 5}):
            self.send(**bad)
        self.assertEqual(len(self.entries()), 1)
        self.assertEqual(len(self.state_publishes()), publishes)

    def test_remove_enable_disable_and_clear(self):
        self.send(action="add", time="06:30", plate=1, duration=120, days=[0])
        self.send(action="add", time="07:30", plate=3, duration=90, days=[2])
        self.send(action="disable", index=2)
        self.assertFalse(self.entries()[1]["enabled"])
        self.send(action="disable", index=2)
        self.send(action="enable", index=2)
        self.assertTrue(self.entries()[1]["enabled"])
        self.send(action="remove", index=1)
        saved = self.entries()
        self.assertEqual([entry["time"] for entry in saved], ["07:30"])
        self.send(action="clear")
        self.assertEqual(self.entries(), [])
        self.assertEqual(json.loads(self.state_publishes()[-1].args[1])["count"], 0)

    def test_disabled_entry_never_fires(self):
        cloud.write_entries([{"time": "06:00", "enabled": False}])
        self.service.sched_tick(cloud.read_entries(), utc("2026-10-09T13:00:00"))
        self.client.publish.assert_not_called()

    def test_summary_exposes_id_index_and_enabled(self):
        cloud.write_entries([{"time": "06:00"}, {"time": "07:00", "enabled": False}])
        summary = self.service.schedule_summary()
        self.assertEqual([entry["index"] for entry in summary], [1, 2])
        self.assertEqual(summary[1]["enabled"], False)
        self.assertTrue(summary[0]["id"].startswith("h"))


class ScriptTests(unittest.TestCase):
    def test_service_startup_retries_and_subscribes_only_posts(self):
        mqtt = Mock()
        with patch.dict("sys.modules", {"paho": Mock(), "paho.mqtt": Mock(),
                                       "paho.mqtt.client": mqtt}), patch.object(cloud.threading, "Thread"):
            # Supply a parent package whose client attribute is our fake module.
            import sys
            sys.modules["paho.mqtt"].client = mqtt
            sys.modules["paho"].mqtt = sys.modules["paho.mqtt"]
            with patch.object(cloud, "SERIAL", "TESTSERIAL"), patch.object(
                    cloud, "LOCAL_MQTT", {"host": "192.0.2.20", "port": 1884}):
                cloud.main()
        client = mqtt.Client.return_value
        client.connect.assert_not_called()
        client.connect_async.assert_called_once_with(cloud.BROKER, cloud.PORT, 60)
        client.loop_forever.assert_called_once_with(retry_first_connection=True)
        client.on_connect(client, None, None, 1)
        client.subscribe.assert_not_called()
        client.on_connect(client, None, None, 0)
        self.assertEqual(client.subscribe.call_args.args[0],
                         [(f"{cloud.BASE}/+/post", 1), ("polar/cmd/#", 1)])

    def test_imports_have_no_io(self):
        with patch("builtins.open", side_effect=AssertionError("file opened")), patch(
                "subprocess.run", side_effect=AssertionError("process started")), patch(
                "socket.create_connection", side_effect=AssertionError("network")):
            importlib.reload(make_local)
            importlib.reload(relay)

    @patch.object(feed, "SERIAL", "TESTSERIAL")
    def test_feed_publish_timeout_and_failures(self):
        with patch.object(feed.subprocess, "run", return_value=SimpleNamespace(returncode=0, stderr="")) as run:
            mid = feed.send_feed()
            self.assertEqual(run.call_args.kwargs["timeout"], 15)
            payload = json.loads(run.call_args.args[0][-1])
            self.assertEqual(mid, payload["msgId"])
        with patch.object(feed.subprocess, "run", return_value=SimpleNamespace(returncode=1, stderr="failed")):
            with self.assertRaises(RuntimeError):
                feed.send_feed()

    def test_config_exit_status(self):
        with patch("builtins.print"), patch.object(make_local.subprocess, "run") as run, \
                patch.object(make_local, "SERIAL", "TESTSERIAL"), \
                patch.object(make_local, "BROKER", "192.0.2.20"):
            for rc in (0, 1):
                run.return_value = SimpleNamespace(returncode=rc, stderr="")
                self.assertEqual(make_local.main(), rc)
                self.assertEqual(run.call_args.kwargs["timeout"], 15)
            run.side_effect = subprocess.TimeoutExpired("mosquitto_pub", 15)
            self.assertEqual(make_local.main(), 1)

    def test_config_requires_env(self):
        # Without POLAR_SERIAL / POLAR_LOCAL_HOST the tool must refuse (rc 2), not publish.
        with patch("builtins.print"), patch.object(make_local, "SERIAL", "EXAMPLE_SERIAL"), \
                patch.object(make_local, "BROKER", ""):
            self.assertEqual(make_local.main(), 2)

    def test_relay_resets_timeout_and_closes_sockets(self):
        client, upstream = Mock(), Mock()
        with patch.object(relay.socket, "create_connection", return_value=upstream), patch.object(
                relay.threading, "Thread") as thread, patch.object(relay.threading, "Event"):
            relay.handle(client, ("test", 1883))
        upstream.settimeout.assert_called_once_with(None)
        upstream.close.assert_called_once()
        client.close.assert_called_once()
        self.assertEqual(thread.return_value.join.call_count, 2)

    def test_relay_failure_closes_client(self):
        client = Mock()
        with patch.object(relay.socket, "create_connection", side_effect=OSError("offline")):
            relay.handle(client, ("test", 1883))
        client.close.assert_called_once()

    def test_relay_log_failure_does_not_break_forwarding(self):
        logfile = Mock()
        logfile.write.side_effect = OSError("disk full")
        with patch.object(relay, "LOG", logfile), patch("builtins.print") as report:
            relay.log("test", b"data")
        report.assert_called_once()

    def test_pipe_eof_and_send_error_shutdown_both_sides(self):
        for error in (None, OSError("closed")):
            src, dst, event = Mock(), Mock(), Mock()
            src.recv.side_effect = [b"data", b""]
            dst.sendall.side_effect = error
            relay.pipe(src, dst, "test", event)
            event.set.assert_called_once()
            src.shutdown.assert_called_once()
            dst.shutdown.assert_called_once()


if __name__ == "__main__":
    unittest.main()
