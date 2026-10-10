use anyhow::{ensure, Result};
use chrono::Utc;
use polar_cloud::{validate_local_address, validate_serial, Cloud};
use rumqttc::{AsyncClient, Event, MqttOptions, Packet, QoS};
use serde_json::{json, Value};
use std::{
    env, fs,
    io::ErrorKind,
    time::{Duration, Instant},
};

fn env_or(key: &str, default: &str) -> String {
    env::var(key).unwrap_or_else(|_| default.into())
}
#[tokio::main]
async fn main() -> Result<()> {
    let serial = env_or("POLAR_SERIAL", "EXAMPLE_SERIAL");
    let host = env_or("POLAR_LOCAL_HOST", "");
    let port: u16 = env_or("POLAR_PORT", "1884").parse()?;
    let local_port: u16 = env_or("POLAR_LOCAL_PORT", "1884").parse()?;
    validate_serial(&serial)?;
    validate_local_address(&host, local_port)?;
    ensure!(port > 0, "POLAR_PORT must be 1..65535");
    let mut cloud = Cloud::new(
        &serial,
        env_or("POLAR_TIMEZONE", "UTC").parse()?,
        host,
        local_port,
    );
    let schedule = env_or("POLAR_SCHEDULE", "/root/polar_schedule.json");
    cloud.schedule_file = schedule.clone().into();
    let mut options = MqttOptions::new(
        "polar-local-cloud",
        env_or("POLAR_BROKER", "127.0.0.1"),
        port,
    );
    options.set_keep_alive(Duration::from_secs(60));
    let (client, mut events) = AsyncClient::new(options, 32);
    let mut send = |topic: &str, payload: Value, retain: bool| {
        client
            .try_publish(topic, QoS::AtLeastOnce, retain, payload.to_string())
            .is_ok()
    };
    let mut tick = tokio::time::interval(Duration::from_millis(500));
    tick.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
    let mut next_schedule = Instant::now();
    let mut reconnect_at = tokio::time::Instant::now();
    let mut delay = 2;
    loop {
        tokio::select! {
            event = async { tokio::time::sleep_until(reconnect_at).await; events.poll().await } => {
                match event {
                    Ok(Event::Incoming(Packet::ConnAck(_))) => {
                        cloud.connected = true;
                        delay = 2;
                        for topic in cloud.subscriptions() { client.try_subscribe(topic, QoS::AtLeastOnce)?; }
                        eprintln!("broker connected; subscribed to device topics + polar/cmd/#");
                    }
                    Ok(Event::Incoming(Packet::Publish(msg))) => {
                        if let Err(err) = cloud.message(&msg.topic, &msg.payload, msg.retain, Utc::now(), Instant::now(), &mut send) { eprintln!("invalid message on {}: {err}", msg.topic); }
                    }
                    Err(err) => {
                        cloud.connected = false;
                        eprintln!("broker disconnected: {err}");
                        reconnect_at = tokio::time::Instant::now() + Duration::from_secs(delay);
                        delay = (delay * 2).min(30);
                    }
                    _ => {}
                }
            }
            _ = tick.tick() => {
                let now = Utc::now();
                let mono = Instant::now();
                cloud.timers(now, mono, &mut send);
                if mono >= next_schedule {
                    next_schedule = mono + Duration::from_secs(20);
                    let entries: Result<Value> = match fs::read(&schedule) {
                        Ok(raw) => serde_json::from_slice(&raw).map_err(Into::into),
                        Err(err) if err.kind() == ErrorKind::NotFound => Ok(json!([])),
                        Err(err) => Err(err.into()),
                    };
                    if let Err(err) = entries.and_then(|entries| cloud.schedule(&entries, now, &mut send)) { eprintln!("scheduler error: {err}"); }
                }
            }
        }
    }
}
