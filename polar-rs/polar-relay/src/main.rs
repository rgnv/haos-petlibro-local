use anyhow::Result;
use chrono::Local;
use std::{
    env,
    fs::{File, OpenOptions},
    io::Write,
    os::unix::fs::{OpenOptionsExt, PermissionsExt},
    sync::{Arc, Mutex},
    time::Duration,
};
use tokio::{
    io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt},
    net::{TcpListener, TcpSocket, TcpStream},
    time::timeout,
};

// Python bytes repr is part of the capture/sanitizer interface.
fn bytes_repr(data: &[u8]) -> String {
    let quote = if data.contains(&b'\'') && !data.contains(&b'"') {
        b'"'
    } else {
        b'\''
    };
    let mut result = format!("b{}", quote as char);
    for &b in data {
        match b {
            b'\\' => result.push_str("\\\\"),
            b'\n' => result.push_str("\\n"),
            b'\r' => result.push_str("\\r"),
            b'\t' => result.push_str("\\t"),
            b if b == quote => {
                result.push('\\');
                result.push(b as char);
            }
            32..=126 => result.push(b as char),
            _ => result.push_str(&format!("\\x{b:02x}")),
        }
    }
    result.push(quote as char);
    result
}
fn log_line(timestamp: &str, tag: &str, data: &[u8]) -> String {
    format!(
        "[{timestamp}] {tag} ({}B) {}\n",
        data.len(),
        bytes_repr(&data[..data.len().min(600)])
    )
}
fn write_log(output: &mut impl Write, tag: &str, data: &[u8]) {
    let line = log_line(
        &Local::now().format("%Y-%m-%d %H:%M:%S").to_string(),
        tag,
        data,
    );
    if let Err(err) = output.write_all(line.as_bytes()) {
        eprintln!("relay log failed: {err}");
    }
}
#[derive(Clone)]
struct Log(Arc<Mutex<File>>);
impl Log {
    fn write(&self, tag: &str, data: impl AsRef<[u8]>) {
        write_log(&mut *self.0.lock().unwrap(), tag, data.as_ref());
    }
}
async fn pipe<R: AsyncRead + Unpin, W: AsyncWrite + Unpin>(
    mut src: R,
    mut dst: W,
    tag: &str,
    log: &impl Fn(&str, &[u8]),
) -> std::io::Result<()> {
    let mut data = [0; 65536];
    loop {
        let n = src.read(&mut data).await?;
        if n == 0 {
            return Ok(());
        }
        dst.write_all(&data[..n]).await?;
        log(tag, &data[..n]);
    }
}
async fn relay<A: AsyncRead + AsyncWrite + Unpin, B: AsyncRead + AsyncWrite + Unpin>(
    client: A,
    upstream: B,
    log: impl Fn(&str, &[u8]),
) {
    let (cr, cw) = tokio::io::split(client);
    let (ur, uw) = tokio::io::split(upstream);
    // Dropping both directions closes both sockets, including a stalled peer.
    let (tag, result) = tokio::select! {
        result = pipe(cr, uw, "DEV->CLOUD", &log) => ("DEV->CLOUD-ERR",result),
        result = pipe(ur, cw, "CLOUD->DEV", &log) => ("CLOUD->DEV-ERR",result),
    };
    if let Err(err) = result {
        log(tag, err.to_string().as_bytes());
    }
}
async fn handle(client: TcpStream, addr: std::net::SocketAddr, host: String, port: u16, log: Log) {
    let addr = format!("('{}', {})", addr.ip(), addr.port());
    log.write("NEW-CONN", &addr);
    match timeout(
        Duration::from_secs(8),
        TcpStream::connect((host.as_str(), port)),
    )
    .await
    {
        Ok(Ok(upstream)) => {
            log.write("UPSTREAM-CONNECTED", format!("{host}:{port}"));
            relay(client, upstream, |tag, data| log.write(tag, data)).await;
            log.write("CLOSED", addr);
        }
        Ok(Err(err)) => log.write("UPSTREAM-FAIL", format!("{host}: {err}")),
        Err(_) => log.write("UPSTREAM-FAIL", format!("{host}: timed out")),
    }
}
#[tokio::main]
async fn main() -> Result<()> {
    let host = env::var("POLAR_UPSTREAM").unwrap_or_else(|_| "mqtt.us.petlibro.com".into());
    let port: u16 = env::var("POLAR_UPSTREAM_PORT")
        .unwrap_or_else(|_| "1883".into())
        .parse()?;
    let listen: u16 = env::var("POLAR_PORT")
        .unwrap_or_else(|_| "1884".into())
        .parse()?;
    let file = OpenOptions::new()
        .create(true)
        .append(true)
        .mode(0o600)
        .open("/root/polar_relay.log")?;
    file.set_permissions(std::fs::Permissions::from_mode(0o600))?;
    let log = Log(Arc::new(Mutex::new(file)));
    let socket = TcpSocket::new_v4()?;
    socket.set_reuseaddr(true)?;
    socket.bind(([0, 0, 0, 0], listen).into())?;
    let listener: TcpListener = socket.listen(16)?;
    log.write(
        "LISTEN",
        format!("relay up on {listen} -> [('{host}', {port})]"),
    );
    loop {
        let (client, addr) = listener.accept().await?;
        tokio::spawn(handle(client, addr, host.clone(), port, log.clone()));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn python_repr_and_truncation() {
        assert_eq!(
            bytes_repr(b"\x00MQTT\xff\n\r\t\\'\""),
            r#"b'\x00MQTT\xff\n\r\t\\\'"'"#
        );
        assert_eq!(bytes_repr(b"('test', 1883)"), r#"b"('test', 1883)""#);
        let line = log_line("2026-10-09 01:02:03", "DEV->CLOUD", &[b'x'; 601]);
        assert_eq!(
            line,
            format!(
                "[2026-10-09 01:02:03] DEV->CLOUD (601B) b'{}'\n",
                "x".repeat(600)
            )
        );
    }
    #[test]
    fn log_failure_does_not_escape() {
        struct Broken;
        impl Write for Broken {
            fn write(&mut self, _: &[u8]) -> std::io::Result<usize> {
                Err(std::io::Error::other("disk full"))
            }
            fn flush(&mut self) -> std::io::Result<()> {
                Ok(())
            }
        }
        write_log(&mut Broken, "test", b"data");
    }
    #[tokio::test]
    async fn forwards_both_directions_and_eof_closes_peer_without_idle_timeout() {
        let (mut device, client) = tokio::io::duplex(1024);
        let (upstream, mut cloud) = tokio::io::duplex(1024);
        let task = tokio::spawn(async move {
            relay(client, upstream, |_, _| {}).await;
        });
        device.write_all(b"request").await.unwrap();
        let mut b = [0; 7];
        cloud.read_exact(&mut b).await.unwrap();
        assert_eq!(&b, b"request");
        cloud.write_all(b"reply!!").await.unwrap();
        device.read_exact(&mut b).await.unwrap();
        assert_eq!(&b, b"reply!!");
        device.shutdown().await.unwrap();
        timeout(Duration::from_secs(1), task)
            .await
            .unwrap()
            .unwrap();
        assert_eq!(cloud.read(&mut b).await.unwrap(), 0);
    }
    #[tokio::test]
    async fn send_error_closes_both_directions() {
        let (mut device, client) = tokio::io::duplex(1024);
        let (upstream, cloud) = tokio::io::duplex(1);
        drop(cloud);
        let task = tokio::spawn(async move {
            relay(client, upstream, |_, _| {}).await;
        });
        let _ = device.write_all(b"data").await;
        timeout(Duration::from_secs(1), task)
            .await
            .unwrap()
            .unwrap();
        let mut b = [0];
        assert_eq!(device.read(&mut b).await.unwrap(), 0);
    }
}
