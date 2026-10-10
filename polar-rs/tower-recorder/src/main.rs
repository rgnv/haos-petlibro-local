use anyhow::{ensure, Context, Result};
use chrono::Local;
use rumqttc::{AsyncClient, Event, MqttOptions, Packet, QoS};
use serde_json::Value;
use std::{
    env,
    ffi::CString,
    fs,
    os::unix::ffi::OsStrExt,
    path::{Path, PathBuf},
    process::Stdio,
    time::{Duration, SystemTime},
};
use tokio::{process::Command, task::JoinSet};

const CLIP_SECONDS: u64 = 180;
const COOLDOWN_SECONDS: u64 = 30;
#[derive(Clone)]
struct Config {
    stream: String,
    out: PathBuf,
}
fn event_kind(topic: &str, expected: &str, raw: &[u8], retained: bool) -> Option<&'static str> {
    if retained || topic != expected {
        return None;
    }
    let payload: Value = serde_json::from_slice(raw).ok()?;
    match payload.get("cmd")?.as_str()? {
        "DETECTION_EVENT" if payload.get("type")?.as_str()? == "MOTION" => Some("motion"),
        "GRAIN_OUTPUT_EVENT" => Some("feed"),
        _ => None,
    }
}
fn check_directory(out: &Path, day: &Path) -> Result<()> {
    let meta = fs::symlink_metadata(out).context("clips directory missing")?;
    ensure!(
        meta.is_dir() && !meta.file_type().is_symlink(),
        "clips directory missing or symlinked"
    );
    let path = CString::new(out.as_os_str().as_bytes())?;
    let mut stats = std::mem::MaybeUninit::<libc::statvfs>::uninit();
    // Both pointers are valid; statvfs initializes the output only on success.
    let result = unsafe { libc::statvfs(path.as_ptr(), stats.as_mut_ptr()) };
    if result != 0 {
        return Err(std::io::Error::last_os_error().into());
    }
    let stats = unsafe { stats.assume_init() };
    ensure!(
        (stats.f_bavail as u128) * (stats.f_frsize as u128) >= 100 * 1024 * 1024,
        "less than 100 MiB free in clips directory"
    );
    match fs::symlink_metadata(day) {
        Ok(meta) => ensure!(
            !meta.file_type().is_symlink(),
            "date directory is a symlink"
        ),
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => {}
        Err(err) => return Err(err.into()),
    }
    fs::create_dir_all(day)?;
    Ok(())
}
fn ffmpeg_args(stream: &str, partial: &Path) -> Vec<std::ffi::OsString> {
    let mut args: Vec<_> = [
        "-y",
        "-rtsp_transport",
        "tcp",
        "-timeout",
        "20000000",
        "-i",
        stream,
        "-t",
        "180",
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        "-f",
        "mp4",
    ]
    .into_iter()
    .map(Into::into)
    .collect();
    args.push(partial.as_os_str().to_owned());
    args
}
async fn record(config: &Config, kind: &str, ffmpeg: &Path, limit: Duration) -> Result<PathBuf> {
    let now = Local::now();
    let day = config.out.join(now.format("%Y-%m-%d").to_string());
    let path = day.join(format!("tower_{kind}_{}.mp4", now.format("%Y%m%d_%H%M%S")));
    let partial = path.with_extension("mp4.part");
    check_directory(&config.out, &day)?;
    eprintln!("recording {kind} -> {}", path.display());
    let mut child = Command::new(ffmpeg)
        .args(ffmpeg_args(&config.stream, &partial))
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .kill_on_drop(true)
        .spawn()
        .context("cannot start ffmpeg")?;
    match tokio::time::timeout(limit, child.wait()).await {
        Ok(status) => ensure!(status?.success(), "ffmpeg failed with nonzero exit status"),
        Err(_) => {
            child.kill().await.context("cannot stop timed-out ffmpeg")?;
            anyhow::bail!("ffmpeg exceeded clip timeout");
        }
    }
    let size = fs::metadata(&partial)?.len();
    ensure!(size > 0, "empty clip");
    fs::rename(&partial, &path)?;
    eprintln!("clip done: {} ({size} bytes)", path.display());
    Ok(path)
}
fn env_or(key: &str, default: &str) -> String {
    env::var(key).unwrap_or_else(|_| default.into())
}
#[tokio::main]
async fn main() -> Result<()> {
    let serial = env_or("PETLIBRO_SERIAL", "EXAMPLE_SERIAL");
    let config = Config {
        stream: env_or("PETLIBRO_STREAM", ""),
        out: env_or("PETLIBRO_CLIPS_DIR", "").into(),
    };
    ensure!(
        !serial.is_empty()
            && serial.chars().all(char::is_alphanumeric)
            && !serial.starts_with("EXAMPLE")
            && !serial.starts_with("YOUR")
            && (config.stream.starts_with("rtsp://") || config.stream.starts_with("rtsps://"))
            && config.out.is_absolute(),
        "Set PETLIBRO_SERIAL, PETLIBRO_STREAM (RTSP), and absolute PETLIBRO_CLIPS_DIR"
    );
    let creds: Value = serde_json::from_slice(&fs::read(env_or(
        "PETLIBRO_CREDS",
        "/root/petlibro-cam-creds.json",
    ))?)?;
    let username = creds["username"]
        .as_str()
        .context("MQTT username must be a string")?;
    let password = creds["password"]
        .as_str()
        .context("MQTT password must be a string")?;
    let mut options = MqttOptions::new(
        "petlibro-cam-recorder",
        env_or("PETLIBRO_BROKER", "127.0.0.1"),
        1883,
    );
    options.set_keep_alive(Duration::from_secs(30));
    options.set_credentials(username, password);
    let (client, mut events) = AsyncClient::new(options, 32);
    let topic = format!("dl/PLAF203/{serial}/device/event/post");
    let mut jobs = JoinSet::new();
    let mut retry_at = SystemTime::UNIX_EPOCH;
    let mut reconnect_at = tokio::time::Instant::now();
    let mut delay = 2;
    loop {
        tokio::select! {
            result = jobs.join_next(), if !jobs.is_empty() => {
                match result {
                    Some(Ok(Err(err))) => eprintln!("clip failed: {err}"),
                    Some(Err(err)) => eprintln!("recording worker failed: {err}"),
                    _ => {}
                }
                retry_at = SystemTime::now() + Duration::from_secs(COOLDOWN_SECONDS);
            }
            event = async { tokio::time::sleep_until(reconnect_at).await; events.poll().await } => {
                match event {
                    Ok(Event::Incoming(Packet::ConnAck(_))) => {delay = 2;client.try_subscribe(&topic,QoS::AtLeastOnce)?;eprintln!("recorder up; listening on {topic}");}
                    Ok(Event::Incoming(Packet::Publish(msg))) if jobs.is_empty() && SystemTime::now() >= retry_at => {
                        if let Some(kind) = event_kind(&msg.topic,&topic,&msg.payload,msg.retain) {
                            let config = config.clone();
                            jobs.spawn(async move { record(&config,kind,Path::new("ffmpeg"),Duration::from_secs(CLIP_SECONDS+90)).await });
                        }
                    }
                    Err(err) => {
                        eprintln!("broker disconnected: {err}");
                        reconnect_at = tokio::time::Instant::now() + Duration::from_secs(delay);
                        delay = (delay*2).min(30);
                    }
                    _ => {}
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::{symlink, PermissionsExt};
    struct Temp(PathBuf);
    impl Temp {
        fn new() -> Self {
            let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .join("target")
                .join(format!(
                    "test-{}-{}",
                    std::process::id(),
                    SystemTime::now()
                        .duration_since(SystemTime::UNIX_EPOCH)
                        .unwrap()
                        .as_nanos()
                ));
            fs::create_dir_all(&path).unwrap();
            Self(path)
        }
        fn script(&self, name: &str, body: &str) -> PathBuf {
            let path = self.0.join(name);
            fs::write(&path, format!("#!/bin/sh\n{body}\n")).unwrap();
            fs::set_permissions(&path, fs::Permissions::from_mode(0o700)).unwrap();
            path
        }
    }
    impl Drop for Temp {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }
    #[test]
    fn event_routing_retained_malformed_and_foreign() {
        for raw in [
            b"null".as_slice(),
            b"[]",
            b"1",
            b"{",
            b"\xff",
            br#"{"cmd":[]}"#,
            br#"{"cmd":"DETECTION_EVENT","type":"OTHER"}"#,
        ] {
            assert_eq!(event_kind("topic", "topic", raw, false), None);
        }
        for (raw, kind) in [
            (br#"{"cmd":"GRAIN_OUTPUT_EVENT"}"#.as_slice(), "feed"),
            (br#"{"cmd":"DETECTION_EVENT","type":"MOTION"}"#, "motion"),
        ] {
            assert_eq!(event_kind("topic", "topic", raw, false), Some(kind));
            assert_eq!(event_kind("topic", "topic", raw, true), None);
            assert_eq!(event_kind("foreign", "topic", raw, false), None);
        }
    }
    #[test]
    fn ffmpeg_flags_match_reference() {
        let args = ffmpeg_args("rtsp://example.invalid/stream", Path::new("clip.mp4.part"));
        assert_eq!(
            args.iter().map(|a| a.to_str().unwrap()).collect::<Vec<_>>(),
            vec![
                "-y",
                "-rtsp_transport",
                "tcp",
                "-timeout",
                "20000000",
                "-i",
                "rtsp://example.invalid/stream",
                "-t",
                "180",
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                "-f",
                "mp4",
                "clip.mp4.part"
            ]
        );
    }
    #[tokio::test]
    async fn atomic_success_failures_and_timeout() {
        let temp = Temp::new();
        let config = Config {
            stream: "rtsp://test:SECRET@example.invalid/stream".into(),
            out: temp.0.clone(),
        };
        let good = temp.script(
            "good",
            "for arg do last=$arg; done\nprintf clip > \"$last\"",
        );
        let path = record(&config, "motion", &good, Duration::from_secs(1))
            .await
            .unwrap();
        assert_eq!(fs::read(&path).unwrap(), b"clip");
        assert!(!path.with_extension("mp4.part").exists());
        let failed = temp.script(
            "failed",
            "for arg do last=$arg; done\nprintf partial > \"$last\"\nexit 1",
        );
        let err = record(&config, "feed", &failed, Duration::from_secs(1))
            .await
            .unwrap_err();
        assert!(!err.to_string().contains("SECRET"));
        let day = path.parent().unwrap();
        let files: Vec<_> = fs::read_dir(day)
            .unwrap()
            .map(|e| e.unwrap().path())
            .collect();
        assert_eq!(
            files
                .iter()
                .filter(|p| p.extension().unwrap() == "mp4")
                .count(),
            1
        );
        assert_eq!(
            files
                .iter()
                .filter(|p| p.extension().unwrap() == "part")
                .count(),
            1
        );
        let empty = temp.script("empty", "for arg do last=$arg; done\n: > \"$last\"");
        assert_eq!(
            record(&config, "feed", &empty, Duration::from_secs(1))
                .await
                .unwrap_err()
                .to_string(),
            "empty clip"
        );
        let slow = temp.script("slow", "exec sleep 10");
        assert_eq!(
            record(&config, "feed", &slow, Duration::from_millis(20))
                .await
                .unwrap_err()
                .to_string(),
            "ffmpeg exceeded clip timeout"
        );
    }
    #[test]
    fn missing_and_symlinked_directories_rejected() {
        let temp = Temp::new();
        let missing = temp.0.join("missing");
        assert!(check_directory(&missing, &missing.join("day")).is_err());
        let link = temp.0.join("link");
        symlink(&temp.0, &link).unwrap();
        assert!(check_directory(&link, &link.join("day")).is_err());
        assert!(check_directory(&temp.0, &link).is_err());
    }
}
