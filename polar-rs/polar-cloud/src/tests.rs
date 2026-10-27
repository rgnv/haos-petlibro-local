use super::*;
fn utc(s: &str) -> DateTime<Utc> {
    format!("{s}Z").parse().unwrap()
}
fn cloud() -> Cloud {
    let mut c = Cloud::new(
        "TESTSERIAL",
        chrono_tz::America::Los_Angeles,
        "192.0.2.20".into(),
        1884,
    );
    c.connected = true;
    c.schedule_file =
        std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("target/nonexistent-schedule.json");
    c
}
fn now() -> DateTime<Utc> {
    utc("2026-10-09T13:00:00")
}

#[test]
fn ntp_exact_transitions() {
    for (date, off, first, next, second, last) in [
        (
            "2026-01-01T12:34:56",
            -28800,
            "2026-03-08T10:00:00",
            -25200,
            "2026-11-01T09:00:00",
            -28800,
        ),
        (
            "2026-07-01T00:00:00",
            -25200,
            "2026-11-01T09:00:00",
            -28800,
            "2027-03-14T10:00:00",
            -25200,
        ),
        (
            "2026-11-01T09:00:00",
            -28800,
            "2027-03-14T10:00:00",
            -25200,
            "2027-11-07T09:00:00",
            -28800,
        ),
    ] {
        let p = ntp_payload(cloud().tz, utc(date));
        assert_eq!(p["ts"], utc(date).timestamp_millis());
        assert_eq!(p["timezoneOffsetSeconds"], off);
        assert_eq!(p["nextDSTTransitionTs"], utc(first).timestamp_millis());
        assert_eq!(p["nextDSTOffsetSeconds"], next);
        assert_eq!(
            p["secondNextDSTTransitionTs"],
            utc(second).timestamp_millis()
        );
        assert_eq!(p["secondNextDSTOffsetSeconds"], last);
        assert_eq!(p["calibrationTag"], false);
    }
}
#[test]
fn fractional_timezone_without_dst() {
    let p = ntp_payload(chrono_tz::Asia::Kathmandu, now());
    assert_eq!(p["timezone"], 5.75);
    assert_eq!(p["timezoneOffsetSeconds"], 20700);
    assert_eq!(p["nextDSTTransitionTs"], 0);
    assert_eq!(p["secondNextDSTTransitionTs"], 0);
}
#[test]
fn config_has_independent_address_and_ids() {
    let c = cloud();
    let mut p = config_payload(&c.host, c.port, now());
    assert_eq!(p["mqttAddr"], json!([{"host":c.host, "port":1884}]));
    assert_ne!(p["msgId"], p["linkId"]);
    p["mqttAddr"][0]["host"] = json!("changed");
    assert_eq!(c.host, "192.0.2.20");
}
#[test]
fn feed_uses_device_timezone_and_unique_id() {
    let date = utc("2026-01-02T07:59:59");
    let p = feed_payload(&json!(2), &json!(240), cloud().tz, date).unwrap();
    assert_eq!(
        p["plans"],
        json!([{"planId":666666666,"executionTime":"23:59","executionDay":"2026-01-01","plate":2,"feedingDuration":240,"feedNowState":true}])
    );
    assert_eq!(p["ts"], date.timestamp_millis());
    assert_ne!(
        p["msgId"],
        feed_payload(&json!(2), &json!(240), cloud().tz, date).unwrap()["msgId"]
    );
}
#[test]
fn bad_feed_values() {
    for bad in [
        json!(true),
        json!(false),
        json!(0),
        json!(-1),
        json!(1.5),
        json!("1"),
        Value::Null,
        json!([]),
        json!({}),
    ] {
        assert!(feed_payload(&bad, &json!(60), cloud().tz, now()).is_err());
        assert!(feed_payload(&json!(1), &bad, cloud().tz, now()).is_err());
    }
}
#[test]
fn echo_channel_command_matrix() {
    let c = cloud();
    for channel in ["heart", "ntp", "event", "service", "config", "system"] {
        for direction in ["post", "sub"] {
            for cmd in EVENTS.into_iter().chain([
                "WET_GRAIN_FEEDING_PLAN_SERVICE",
                "DEVICE_LOG_REPORT_EVENT",
                "NTP",
                "unknown",
            ]) {
                let p = echo_payload(
                    &c.base,
                    &format!("{}/{channel}/{direction}", c.base),
                    &json!({"cmd":cmd,"msgId":"id"}),
                    now(),
                );
                assert_eq!(
                    p.is_some(),
                    channel == "event" && direction == "post" && EVENTS.contains(&cmd)
                );
                if let Some(p) = p {
                    assert_eq!(p["msgId"], "id");
                    assert_eq!(p["code"], 0);
                    assert!(!p["linkId"].as_str().unwrap().is_empty());
                }
            }
        }
    }
}
#[test]
fn echo_self_guard_and_foreign_topics() {
    let c = cloud();
    let topic = format!("{}/event/post", c.base);
    for link in [
        Value::Null,
        json!(""),
        json!(false),
        json!("our-link"),
        json!([]),
    ] {
        assert!(echo_payload(
            &c.base,
            &topic,
            &json!({"cmd":"DEVICE_START_EVENT","linkId":link}),
            now()
        )
        .is_none());
    }
    for other in [
        "foreign/event/post".into(),
        format!("{topic}/extra"),
        format!("prefix/{topic}"),
    ] {
        assert!(
            echo_payload(&c.base, &other, &json!({"cmd":"DEVICE_START_EVENT"}), now()).is_none()
        );
    }
    for data in [
        Value::Null,
        json!([]),
        json!(1),
        json!("text"),
        json!({"cmd":[]}),
        json!({"cmd":{}}),
    ] {
        assert!(echo_payload(&c.base, &topic, &data, now()).is_none());
    }
}
#[test]
fn progress_echo_preserves_exec_step() {
    let c = cloud();
    assert_eq!(
        echo_payload(
            &c.base,
            &format!("{}/event/post", c.base),
            &json!({"cmd":"WET_GRAIN_OUTPUT_EVENT","execStep":"OPEN_DOOR"}),
            now()
        )
        .unwrap()["execStep"],
        "OPEN_DOOR"
    );
}
#[test]
fn reply_2099_cannot_publish_or_feed() {
    let mut c = cloud();
    for channel in ["service", "config", "system", "event"] {
        c.message(
            &format!("{}/{channel}/post", c.base),
            br#"{"cmd":"WET_GRAIN_FEEDING_PLAN_SERVICE","code":2099,"msgId":"loop"}"#,
            false,
            now(),
            Instant::now(),
            &mut |_, _, _| panic!("unexpected publish"),
        )
        .unwrap();
    }
}
#[test]
fn bad_json_and_feed_values_do_not_publish() {
    let mut c = cloud();
    for raw in [
        b"null".as_slice(),
        b"[]",
        b"42",
        br#""text""#,
        br#"{"cmd":[]}"#,
        br#"{"plate":"1"}"#,
        br#"{"duration":-1}"#,
        b"{",
        b"\xff",
        "[".repeat(2000).as_bytes(),
    ] {
        let _ = c.message(
            "polar/cmd/feed",
            raw,
            false,
            now(),
            Instant::now(),
            &mut |_, _, _| panic!("unexpected publish"),
        );
    }
}
#[test]
fn retained_and_linked_messages_have_no_side_effects() {
    let mut c = cloud();
    for (topic, data) in [
        ("polar/cmd/feed".into(), json!({})),
        ("polar/cmd/config".into(), json!({})),
        ("polar/cmd/set".into(), json!({"volume":50})),
        (
            format!("{}/event/post", c.base),
            json!({"cmd":"DEVICE_START_EVENT"}),
        ),
        (format!("{}/ntp/post", c.base), json!({"cmd":"NTP"})),
    ] {
        c.message(
            &topic,
            data.to_string().as_bytes(),
            true,
            now(),
            Instant::now(),
            &mut |_, _, _| panic!("unexpected publish"),
        )
        .unwrap();
        let mut linked = data;
        linked["linkId"] = Value::Null;
        c.message(
            &topic,
            linked.to_string().as_bytes(),
            false,
            now(),
            Instant::now(),
            &mut |_, _, _| panic!("unexpected publish"),
        )
        .unwrap();
    }
    assert!(c.config_due.is_none());
}
#[test]
fn boot_echo_nonblocking_coalesces_and_deferred_config_retries() {
    let mut c = cloud();
    let mono = Instant::now();
    let mut sent = Vec::new();
    for offset in [0, 100] {
        c.message(
            &format!("{}/event/post", c.base),
            br#"{"cmd":"DEVICE_START_EVENT"}"#,
            false,
            now(),
            mono + Duration::from_millis(offset),
            &mut |t, p, _| {
                sent.push((t.to_owned(), p));
                true
            },
        )
        .unwrap();
    }
    assert_eq!(c.config_due, Some(mono + Duration::from_millis(1500)));
    assert_eq!(sent.len(), 2);
    assert!(sent.iter().all(|(t, _)| t.ends_with("/event/sub")));
    c.timers(now(), mono + Duration::from_secs(1), &mut |_, _, _| {
        panic!("too early")
    });
    c.timers(now(), mono + Duration::from_secs(2), &mut |_, _, _| false);
    assert!(c.config_due.is_some());
    c.timers(now(), mono + Duration::from_secs(2), &mut |t, p, r| {
        assert!(t.ends_with("/service/sub"));
        assert_eq!(p["cmd"], "DEVICE_CONFIG_SYNC");
        assert!(!r);
        true
    });
    assert!(c.config_due.is_none());
}
#[test]
fn ntp_and_manual_feed_routing() {
    let mut c = cloud();
    c.message(
        &format!("{}/ntp/post", c.base),
        br#"{"cmd":"NTP"}"#,
        false,
        now(),
        Instant::now(),
        &mut |t, p, r| {
            assert!(t.ends_with("/ntp/sub"));
            assert_eq!(p["cmd"], "NTP");
            assert!(!r);
            true
        },
    )
    .unwrap();
    c.message(
        "polar/cmd/feed",
        br#"{"plate":2,"duration":240}"#,
        false,
        now(),
        Instant::now(),
        &mut |t, p, r| {
            assert!(t.ends_with("/service/sub"));
            assert_eq!(p["plans"][0]["plate"], 2);
            assert!(!r);
            true
        },
    )
    .unwrap();
}
#[test]
fn publish_failures() {
    let mut c = cloud();
    c.connected = false;
    assert!(!c.publish("topic", json!({}), false, &mut |_, _, _| panic!(
        "disconnected"
    )));
    c.connected = true;
    assert!(!c.publish("topic", json!({}), false, &mut |_, _, _| false));
}
#[test]
fn missed_minute_and_duplicate_rows() {
    let mut c = cloud();
    let mut calls = 0;
    for date in [
        "2026-10-09T12:59:59",
        "2026-10-09T13:02:00",
        "2026-10-09T13:02:20",
    ] {
        c.schedule(
            &json!([{"time":"06:00"},{"time":"06:00"}]),
            utc(date),
            &mut |_, _, _| {
                calls += 1;
                true
            },
        )
        .unwrap();
    }
    assert_eq!(calls, 1);
}
#[test]
fn fall_back_fires_once_and_spring_gap_skips() {
    let mut c = cloud();
    let mut calls = 0;
    for date in ["2026-11-01T08:30:00", "2026-11-01T09:30:00"] {
        c.schedule(&json!([{"time":"01:30"}]), utc(date), &mut |_, _, _| {
            calls += 1;
            true
        })
        .unwrap();
    }
    assert_eq!(calls, 1);
    for date in ["2027-03-14T09:59:00", "2027-03-14T10:01:00"] {
        c.schedule(&json!([{"time":"02:30"}]), utc(date), &mut |_, _, _| {
            panic!("spring gap")
        })
        .unwrap();
    }
}
#[test]
fn midnight_day_filter_and_stale_limit() {
    let before = Some(utc("2026-10-10T06:59:00").timestamp() / 60);
    let entry = json!({"time":"23:59","days":[4]});
    assert_eq!(
        schedule_due(&entry, cloud().tz, utc("2026-10-10T07:01:00"), before).unwrap(),
        Some(("2026-10-09".into(), "23:59".into(), "1".into()))
    );
    assert!(
        schedule_due(&entry, cloud().tz, utc("2026-10-10T07:06:00"), before)
            .unwrap()
            .is_none()
    );
    assert!(schedule_due(
        &json!({"time":"23:59","days":[5]}),
        cloud().tz,
        utc("2026-10-10T07:01:00"),
        before
    )
    .unwrap()
    .is_none());
}
#[test]
fn invalid_entries_do_not_starve_good_entry() {
    let mut c = cloud();
    let mut calls = 0;
    c.schedule(&json!([null,{}, {"time":"6:00"},{"time":"24:00"},{"time":"06:60"},{"time":"06:00","days":[true]},{"time":"06:00","plate":0},{"time":"06:00"}]),now(),&mut |_,_,_| {calls+=1;true}).unwrap();
    assert_eq!(calls, 1);
    assert!(c.schedule(&json!({}), now(), &mut |_, _, _| true).is_err());
}
#[test]
fn disconnect_and_queue_rejection_retry_within_catchup_window() {
    for disconnected in [true, false] {
        let mut c = cloud();
        let entries = json!([{"time":"06:00"}]);
        c.schedule(&entries, utc("2026-10-09T12:59:00"), &mut |_, _, _| true)
            .unwrap();
        c.connected = !disconnected;
        c.schedule(&entries, now(), &mut |_, _, _| false).unwrap();
        assert!(c.last_fired.is_empty());
        c.connected = true;
        let mut calls = 0;
        c.schedule(&entries, utc("2026-10-09T13:02:00"), &mut |_, _, _| {
            calls += 1;
            true
        })
        .unwrap();
        assert_eq!(calls, 1);
        assert_eq!(c.last_fired.len(), 1);
    }
}
#[test]
fn subscriptions_only_posts_and_local_commands() {
    assert_eq!(
        cloud().subscriptions(),
        ["dl/PLAF109/TESTSERIAL/device/+/post", "polar/cmd/#"]
    );
}
#[test]
fn heartbeat_status_online_offline_and_recovery() {
    let mut c = cloud();
    let mono = Instant::now();
    let mut sent = Vec::new();
    c.timers(now(), mono, &mut |_, _, _| panic!("no initial offline"));
    let topic = format!("{}/heart/post", c.base);
    c.message(
        &topic,
        br#"{"cmd":"HEARTBEAT","count":7,"rssi":-38,"temperature":22.95,"ts":123}"#,
        false,
        now(),
        mono,
        &mut |t, p, r| {
            sent.push((t.to_owned(), p, r));
            true
        },
    )
    .unwrap();
    assert_eq!(sent[0].0, "polar/status");
    assert!(sent[0].2);
    assert_eq!(sent[0].1["device_ts"], 123);
    assert_eq!(sent[0].1["online"], true);
    c.timers(
        now() + chrono::Duration::seconds(240),
        mono + Duration::from_secs(240),
        &mut |_, _, _| panic!("boundary"),
    );
    c.timers(
        now() + chrono::Duration::seconds(241),
        mono + Duration::from_secs(241),
        &mut |t, p, r| {
            sent.push((t.to_owned(), p, r));
            true
        },
    );
    assert_eq!(
        sent[1].1,
        json!({"online":false,"seen_at":now().timestamp_millis()})
    );
    assert!(sent[1].2);
    c.timers(
        now() + chrono::Duration::seconds(242),
        mono + Duration::from_secs(242),
        &mut |_, _, _| panic!("duplicate offline"),
    );
    c.message(
        &topic,
        br#"{"cmd":"HEARTBEAT"}"#,
        false,
        now(),
        mono + Duration::from_secs(243),
        &mut |_, p, r| {
            assert_eq!(p["online"], true);
            assert!(r);
            true
        },
    )
    .unwrap();
}
#[test]
fn attribute_helper_allowlist_and_state() {
    let mut c = cloud();
    for attr in SETTABLE_ATTRS {
        c.message(
            "polar/cmd/set",
            json!({"attr":attr,"value":false}).to_string().as_bytes(),
            false,
            now(),
            Instant::now(),
            &mut |t, p, r| {
                assert!(t.ends_with("/service/sub"));
                assert_eq!(p["cmd"], "ATTR_SET_SERVICE");
                assert_eq!(p[attr], false);
                assert!(p["msgId"].as_str().unwrap().starts_with("set-"));
                assert!(!r);
                true
            },
        )
        .unwrap();
        assert_eq!(c.attrs[attr], false);
    }
    for bad in [
        json!({}),
        json!({"attr":"cmd","value":"RESTORE"}),
        json!({"attr":"volume","value":null}),
        json!({"attr":[],"value":1}),
    ] {
        assert!(set_payload(&bad, now()).is_err());
    }
    c.message(
        &format!("{}/event/post", c.base),
        br#"{"cmd":"ATTR_PUSH_EVENT","volume":50,"wifiSsid":"private"}"#,
        false,
        now(),
        Instant::now(),
        &mut |_, _, _| true,
    )
    .unwrap();
    assert_eq!(c.attrs["volume"], 50);
    assert!(!c.attrs.contains_key("wifiSsid"));
    c.message(
        &format!("{}/heart/post", c.base),
        br#"{"cmd":"HEARTBEAT"}"#,
        false,
        now(),
        Instant::now(),
        &mut |_, p, r| {
            assert_eq!(p["attrs"]["volume"], 50);
            assert_eq!(p["schedule"], json!([]));
            assert!(r);
            true
        },
    )
    .unwrap();
}
#[test]
fn bell_command_and_retained_guard() {
    let mut c = cloud();
    let mut calls = 0;
    for retained in [true, false] {
        c.message(
            "polar/cmd/bell",
            b"{}",
            retained,
            now(),
            Instant::now(),
            &mut |_, p, r| {
                calls += 1;
                assert_eq!(p["ringerMode"], "RING");
                assert!(p["msgId"].as_str().unwrap().starts_with("bell-"));
                assert!(!r);
                true
            },
        )
        .unwrap();
    }
    assert_eq!(calls, 1);
    assert!(c.attrs.is_empty());
}
#[test]
fn summary_defaults_limit_and_malformed_file() {
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("target")
        .join(format!("summary-{}.json", id()));
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    assert_eq!(schedule_summary(&path).unwrap(), json!([]));
    let mut entries = vec![Value::Null];
    entries.extend((0..14).map(|_| json!({"time":"06:00"})));
    std::fs::write(&path, json!(entries).to_string()).unwrap();
    let result = schedule_summary(&path).unwrap();
    assert_eq!(result.as_array().unwrap().len(), 12);
    assert_eq!(
        result[0],
        json!({"time":"06:00","plate":1,"duration":240,"days":null})
    );
    std::fs::write(&path, b"{").unwrap();
    assert_eq!(schedule_summary(&path).unwrap(), json!([]));
    std::fs::remove_file(path).unwrap();
}
#[test]
fn invalid_configuration() {
    for serial in ["", "EXAMPLE_SERIAL", "YOUR123", "bad/serial", "x+#", "é"] {
        assert!(validate_serial(serial).is_err());
    }
    validate_serial("TESTSERIAL").unwrap();
    for host in [
        "",
        "192.168.x.x",
        "127.0.0.1",
        "0.0.0.0",
        "224.0.0.1",
        "::1",
    ] {
        assert!(validate_local_address(host, 1884).is_err());
    }
    assert!(validate_local_address("192.0.2.20", 0).is_err());
    validate_local_address("192.0.2.20", 1884).unwrap();
}

#[test]
fn feed_has_no_invented_hardware_upper_bound() {
    let huge: Value = serde_json::from_str("1000000000000000000000000000000").unwrap();
    let payload = feed_payload(&huge, &huge, cloud().tz, now()).unwrap();
    assert_eq!(payload["plans"][0]["plate"], huge);
    assert_eq!(payload["plans"][0]["feedingDuration"], huge);
    for number in ["1.0", "1e0", "-0"] {
        assert!(feed_payload(
            &serde_json::from_str::<Value>(number).unwrap(),
            &json!(1),
            cloud().tz,
            now()
        )
        .is_err());
    }
}

#[test]
fn attr_refresh_on_connect_populates_controls() {
    let mut c = cloud();
    c.connected = false;
    let mono = Instant::now();
    c.on_connect(mono);
    assert!(c.attr_due.is_some());
    c.message(
        &format!("{}/heart/post", c.base),
        br#"{"cmd":"HEARTBEAT","count":7,"rssi":-40,"temperature":20.0,"ts":11}"#,
        false,
        now(),
        mono,
        &mut |t, p, r| {
            assert_eq!(t, "polar/status");
            assert_eq!(p["attrs"], json!({}));
            assert_eq!(p["count"], 7);
            assert!(r);
            true
        },
    )
    .unwrap();
    let mut sent = 0;
    let base = c.base.clone();
    c.timers(
        now(),
        mono + Duration::from_secs(2),
        &mut |t, p, r| {
            sent += 1;
            assert_eq!(t, format!("{base}/service/sub"));
            assert_eq!(p["cmd"], "ATTR_GET_SERVICE");
            assert!(p["msgId"].as_str().unwrap().starts_with("attr-"));
            assert!(!r);
            true
        },
    );
    assert_eq!(sent, 1);
    assert!(c.attr_due.is_none());
    c.message(
        &format!("{}/event/post", c.base),
        br#"{"cmd":"ATTR_GET_SERVICE","code":0,"lightSwitch":true,"soundSwitch":false,"volume":40,"wifiSsid":"private","plateStuckCurrent":400}"#,
        false,
        now(),
        mono,
        &mut |t, p, r| {
            assert_eq!(t, "polar/status");
            assert_eq!(p["attrs"]["lightSwitch"], true);
            assert_eq!(p["attrs"]["soundSwitch"], false);
            assert_eq!(p["attrs"]["volume"], 40);
            assert!(p["attrs"].get("wifiSsid").is_none());
            assert!(p["attrs"].get("plateStuckCurrent").is_none());
            assert_eq!(p["count"], 7, "status keeps heartbeat fields");
            assert!(r);
            true
        },
    )
    .unwrap();
}

#[test]
fn attr_fetch_waits_for_broker_and_ignores_failed_device_reply() {
    let mut c = cloud();
    let mono = Instant::now();
    c.on_connect(mono);
    c.connected = false;
    c.timers(
        now(),
        mono + Duration::from_secs(5),
        &mut |_, _, _| panic!("must not publish while disconnected"),
    );
    assert!(c.attr_due.is_some(), "fetch stays queued until the broker returns");
    c.connected = true;
    let mut sent = 0;
    c.timers(now(), mono + Duration::from_secs(6), &mut |_, p, _| {
        sent += 1;
        assert_eq!(p["cmd"], "ATTR_GET_SERVICE");
        true
    });
    assert_eq!(sent, 1);
    c.message(
        &format!("{}/event/post", c.base),
        br#"{"cmd":"ATTR_GET_SERVICE","code":2099,"lightSwitch":true}"#,
        false,
        now(),
        mono,
        &mut |_, _, _| panic!("failed replies must not touch the status"),
    )
    .unwrap();
    assert!(c.attrs.is_empty());
}
