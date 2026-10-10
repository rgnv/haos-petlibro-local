use anyhow::{bail, ensure, Result};
use chrono::{DateTime, Datelike, Offset, TimeZone, Timelike, Utc};
use chrono_tz::Tz;
use serde_json::{json, Value};
use std::{
    collections::HashMap,
    net::Ipv4Addr,
    time::{Duration, Instant},
};
use uuid::Uuid;

pub const EVENTS: [&str; 4] = [
    "DEVICE_START_EVENT",
    "ATTR_PUSH_EVENT",
    "GET_FEEDING_PLAN_EVENT",
    "WET_GRAIN_OUTPUT_EVENT",
];
fn id() -> String {
    Uuid::new_v4().simple().to_string()
}
pub fn validate_serial(serial: &str) -> Result<()> {
    ensure!(
        !serial.is_empty()
            && serial.bytes().all(|b| b.is_ascii_alphanumeric())
            && !serial.starts_with("EXAMPLE")
            && !serial.starts_with("YOUR"),
        "Set POLAR_SERIAL to your device serial (letters and digits)."
    );
    Ok(())
}
pub fn validate_local_address(host: &str, port: u16) -> Result<()> {
    let ip: Ipv4Addr = host.parse()?;
    ensure!(
        !ip.is_loopback() && !ip.is_unspecified() && !ip.is_multicast(),
        "POLAR_LOCAL_HOST must be a device-reachable IPv4 address"
    );
    ensure!(port > 0, "POLAR_LOCAL_PORT must be 1..65535");
    Ok(())
}
pub fn ntp_payload(tz: Tz, now: DateTime<Utc>) -> Value {
    let offset = |ts| {
        tz.timestamp_opt(ts, 0)
            .unwrap()
            .offset()
            .fix()
            .local_minus_utc()
    };
    let current = offset(now.timestamp());
    let mut previous = current;
    let mut cursor = now.timestamp();
    let mut transitions = Vec::new();
    for _ in 0..400 * 4 {
        cursor += 6 * 3600;
        let next = offset(cursor);
        if next != previous {
            let (mut lo, mut hi) = (cursor - 6 * 3600, cursor);
            while hi - lo > 1 {
                let mid = (lo + hi) / 2;
                if offset(mid) == previous {
                    lo = mid;
                } else {
                    hi = mid;
                }
            }
            transitions.push((hi * 1000, next));
            previous = next;
            if transitions.len() == 2 {
                break;
            }
        }
    }
    let first = transitions.first().copied().unwrap_or((0, current));
    let second = transitions.get(1).copied().unwrap_or((0, current));
    json!({"cmd":"NTP", "ts":now.timestamp_millis(), "code":0, "calibrationTag":false,
        "timezoneOffsetSeconds":current, "nextDSTOffsetSeconds":first.1, "nextDSTTransitionTs":first.0,
        "secondNextDSTOffsetSeconds":second.1, "secondNextDSTTransitionTs":second.0, "timezone":current as f64 / 3600.0})
}
pub fn config_payload(host: &str, port: u16, now: DateTime<Utc>) -> Value {
    json!({"cmd":"DEVICE_CONFIG_SYNC", "ts":now.timestamp_millis(), "msgId":id(),
        "mqttAddr":[{"host":host,"port":port}], "httpsAddr":"api.us.petlibro.com", "linkId":id()})
}
fn positive(value: &Value, name: &str) -> Result<()> {
    ensure!(
        value.as_u64().is_some_and(|v| v > 0),
        "{name} must be a positive integer"
    );
    Ok(())
}
pub fn feed_payload(plate: &Value, duration: &Value, tz: Tz, now: DateTime<Utc>) -> Result<Value> {
    positive(plate, "plate")?;
    positive(duration, "duration")?;
    let local = now.with_timezone(&tz);
    Ok(
        json!({"cmd":"WET_GRAIN_FEEDING_PLAN_SERVICE", "ts":now.timestamp_millis(),
        "msgId":format!("local-{}", &id()[..12]), "plans":[{"planId":666666666,
        "executionTime":local.format("%H:%M").to_string(), "executionDay":local.format("%Y-%m-%d").to_string(),
        "plate":plate, "feedingDuration":duration, "feedNowState":true}]}),
    )
}
pub fn echo_payload(base: &str, topic: &str, data: &Value, now: DateTime<Utc>) -> Option<Value> {
    let obj = data.as_object()?;
    let cmd = obj.get("cmd")?.as_str()?;
    if topic != format!("{base}/event/post") || obj.contains_key("linkId") || !EVENTS.contains(&cmd)
    {
        return None;
    }
    let mut echo = json!({"cmd":cmd, "msgId":data.get("msgId").unwrap_or(&json!("")), "code":0, "linkId":id(), "ts":now.timestamp_millis()});
    if cmd == "WET_GRAIN_OUTPUT_EVENT" {
        echo["execStep"] = data.get("execStep").cloned().unwrap_or(json!(""));
    }
    Some(echo)
}
pub fn set_payload(data: &Value, now: DateTime<Utc>) -> Result<Value> {
    let attrs = data
        .as_object()
        .ok_or_else(|| anyhow::anyhow!("attributes must be an object"))?;
    ensure!(!attrs.is_empty(), "attributes must not be empty");
    for key in ["cmd", "ts", "msgId", "linkId", "code"] {
        ensure!(!attrs.contains_key(key), "reserved attribute: {key}");
    }
    let mut result = data.clone();
    result["cmd"] = json!("ATTR_SET_SERVICE");
    result["ts"] = json!(now.timestamp_millis());
    result["msgId"] = json!(id());
    Ok(result)
}

pub type ScheduleKey = (String, String, String);
pub fn schedule_due(
    entry: &Value,
    tz: Tz,
    now: DateTime<Utc>,
    previous: Option<i64>,
) -> Result<Option<ScheduleKey>> {
    ensure!(entry.is_object(), "schedule entry must be an object");
    let clock = entry["time"].as_str().unwrap_or("");
    let b = clock.as_bytes();
    ensure!(
        b.len() == 5 && b[2] == b':' && [b[0], b[1], b[3], b[4]].iter().all(u8::is_ascii_digit),
        "schedule time must be HH:MM"
    );
    let hour: u32 = clock[..2].parse()?;
    let minute: u32 = clock[3..].parse()?;
    ensure!(hour <= 23 && minute <= 59, "schedule time out of range");
    let plate = entry.get("plate").cloned().unwrap_or(json!(1));
    positive(&plate, "plate")?;
    positive(entry.get("duration").unwrap_or(&json!(240)), "duration")?;
    let days = entry
        .get("days")
        .cloned()
        .unwrap_or(json!([0, 1, 2, 3, 4, 5, 6]));
    let days = days
        .as_array()
        .ok_or_else(|| anyhow::anyhow!("schedule days must be integers 0..6"))?;
    ensure!(
        days.iter().all(|d| d.as_u64().is_some_and(|n| n <= 6)),
        "schedule days must be integers 0..6"
    );
    let current = now.timestamp().div_euclid(60);
    let start = previous.map_or(current, |p| (current - 5).max(p.min(current)));
    for m in start..=current {
        let local = tz.timestamp_opt(m * 60, 0).unwrap();
        if days.contains(&json!(local.weekday().num_days_from_monday()))
            && (local.hour(), local.minute()) == (hour, minute)
        {
            return Ok(Some((
                local.date_naive().to_string(),
                clock.to_string(),
                plate.to_string(),
            )));
        }
    }
    Ok(None)
}

pub struct Cloud {
    pub base: String,
    pub tz: Tz,
    pub host: String,
    pub port: u16,
    pub connected: bool,
    pub config_due: Option<Instant>,
    pub last_heart: Option<Instant>,
    pub status_online: bool,
    // shortcut: deduplication is process-local; add a durable ledger for restart safety.
    pub last_fired: HashMap<ScheduleKey, i64>,
    pub previous_minute: Option<i64>,
}
// Return true only when the MQTT client accepted the QoS 1 publication.
pub type Publisher<'a> = dyn FnMut(&str, Value, bool) -> bool + 'a;
impl Cloud {
    pub fn new(serial: &str, tz: Tz, host: String, port: u16) -> Self {
        Self {
            base: format!("dl/PLAF109/{serial}/device"),
            tz,
            host,
            port,
            connected: false,
            config_due: None,
            last_heart: None,
            status_online: false,
            last_fired: HashMap::new(),
            previous_minute: None,
        }
    }
    pub fn subscriptions(&self) -> [String; 2] {
        [format!("{}/+/post", self.base), "polar/cmd/#".into()]
    }
    fn publish(&self, topic: &str, data: Value, retained: bool, send: &mut Publisher<'_>) -> bool {
        if !self.connected {
            eprintln!("publish skipped while disconnected: {topic}");
            return false;
        }
        let ok = send(topic, data, retained);
        if !ok {
            eprintln!("publish failed: topic={topic}");
        }
        ok
    }
    pub fn message(
        &mut self,
        topic: &str,
        raw: &[u8],
        retained: bool,
        now: DateTime<Utc>,
        mono: Instant,
        send: &mut Publisher<'_>,
    ) -> Result<()> {
        let data: Value = serde_json::from_slice(raw)?;
        if retained || !data.is_object() || data.get("linkId").is_some() {
            return Ok(());
        }
        let Some(cmd) = data
            .get("cmd")
            .unwrap_or(&json!(""))
            .as_str()
            .map(str::to_owned)
        else {
            return Ok(());
        };
        let service = format!("{}/service/sub", self.base);
        if topic == format!("{}/heart/post", self.base) && cmd == "HEARTBEAT" {
            self.last_heart = Some(mono);
            self.status_online = true;
            self.publish("polar/status", json!({"online":true,"count":data["count"],"rssi":data["rssi"],"temperature":data["temperature"],"device_ts":data["ts"],"seen_at":now.timestamp_millis()}), true, send);
        } else if topic == format!("{}/ntp/post", self.base) && cmd == "NTP" {
            self.publish(
                &format!("{}/ntp/sub", self.base),
                ntp_payload(self.tz, now),
                false,
                send,
            );
        } else if topic == format!("{}/event/post", self.base) {
            if let Some(echo) = echo_payload(&self.base, topic, &data, now) {
                self.publish(&format!("{}/event/sub", self.base), echo, false, send);
            }
            if cmd == "DEVICE_START_EVENT" && self.config_due.is_none() {
                self.config_due = Some(mono + Duration::from_millis(1500));
            }
        } else if topic == "polar/cmd/feed" {
            let payload = feed_payload(
                data.get("plate").unwrap_or(&json!(1)),
                data.get("duration").unwrap_or(&json!(60)),
                self.tz,
                now,
            )?;
            self.publish(&service, payload, false, send);
        } else if topic == "polar/cmd/config" {
            self.publish(
                &service,
                config_payload(&self.host, self.port, now),
                false,
                send,
            );
        } else if topic == "polar/cmd/set" {
            self.publish(&service, set_payload(&data, now)?, false, send);
        }
        Ok(())
    }
    pub fn timers(&mut self, now: DateTime<Utc>, mono: Instant, send: &mut Publisher<'_>) {
        if self.config_due.is_some_and(|due| mono >= due)
            && self.publish(
                &format!("{}/service/sub", self.base),
                config_payload(&self.host, self.port, now),
                false,
                send,
            )
        {
            self.config_due = None;
        }
        if let Some(heart) = self.last_heart {
            let silence = mono.duration_since(heart);
            if self.status_online && silence > Duration::from_secs(240) {
                self.status_online = false;
                self.publish("polar/status", json!({"online":false,"seen_at":now.timestamp_millis() - silence.as_millis() as i64}), true, send);
            }
        }
    }
    pub fn schedule(
        &mut self,
        entries: &Value,
        now: DateTime<Utc>,
        send: &mut Publisher<'_>,
    ) -> Result<()> {
        let Some(entries) = entries.as_array() else {
            bail!("schedule must be an array");
        };
        let current = now.timestamp().div_euclid(60);
        self.previous_minute.get_or_insert(current);
        let mut failed = false;
        for entry in entries {
            match schedule_due(entry, self.tz, now, self.previous_minute) {
                Ok(Some(key)) if !self.last_fired.contains_key(&key) => {
                    let payload = feed_payload(
                        entry.get("plate").unwrap_or(&json!(1)),
                        entry.get("duration").unwrap_or(&json!(240)),
                        self.tz,
                        now,
                    )?;
                    if self.publish(&format!("{}/service/sub", self.base), payload, false, send) {
                        self.last_fired.insert(key, now.timestamp());
                    } else {
                        failed = true;
                    }
                }
                Err(err) => eprintln!("invalid schedule entry: {err}"),
                _ => {}
            }
        }
        if self.connected && !failed {
            self.previous_minute = Some(current);
        }
        self.last_fired
            .retain(|_, ts| *ts > now.timestamp() - 3 * 86400);
        Ok(())
    }
}

#[cfg(test)]
mod tests;
