#!/usr/bin/env python3
"""Transparent MQTT relay for the Polar feeder takeover.

Device -> (GL DNAT) -> this relay on 0.0.0.0:1884 -> real Petlibro cloud broker.
Logs up to 600 bytes per chunk to /root/polar_relay.log for protocol capture.
"""
import socket
import os
import sys
import threading
import time

# Resolve the configured cloud hostname; stale IP fallbacks can reach another owner.
UPSTREAM_HOST = os.environ.get("POLAR_UPSTREAM", "mqtt.us.petlibro.com")
TARGETS = [(UPSTREAM_HOST, int(os.environ.get("POLAR_UPSTREAM_PORT", "1883")))]
LISTEN_PORT = int(os.environ.get("POLAR_PORT", "1884"))
LOGPATH = "/root/polar_relay.log"
LOG = None
LOG_LOCK = threading.Lock()


def log(tag, data):
    if isinstance(data, str):
        data = data.encode()
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with LOG_LOCK:
            if LOG is not None:
                LOG.write(b"[%s] %s (%dB) %r\n" % (ts.encode(), tag.encode(), len(data), data[:600]))
    except OSError as ex:
        print(f"relay log failed: {ex}", file=sys.stderr)


def pipe(src, dst, tag, close_event):
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
            log(tag, data)
    except Exception as e:
        log(tag + "-ERR", str(e))
    finally:
        close_event.set()
        for s in (src, dst):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass


def handle(client, addr):
    log("NEW-CONN", str(addr))
    upstream = None
    for host, port in TARGETS:
        try:
            upstream = socket.create_connection((host, port), timeout=8)
            upstream.settimeout(None)  # MQTT can be idle for the 100-second keepalive.
            log("UPSTREAM-CONNECTED", "%s:%d" % (host, port))
            break
        except OSError as e:
            if upstream is not None:
                upstream.close()
                upstream = None
            log("UPSTREAM-FAIL", "%s: %s" % (host, e))
    if upstream is None:
        client.close()
        return
    ev = threading.Event()
    workers = [threading.Thread(target=pipe, args=args, daemon=True) for args in
               ((client, upstream, "DEV->CLOUD", ev), (upstream, client, "CLOUD->DEV", ev))]
    try:
        for worker in workers:
            worker.start()
        ev.wait()
    finally:
        for s in (client, upstream):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            s.close()
        for worker in workers:
            if worker.ident is not None:
                worker.join()
    log("CLOSED", str(addr))


def main():
    global LOG
    # Raw MQTT CONNECT captures contain credentials; restrict even existing logs.
    fd = os.open(LOGPATH, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "ab", buffering=0) as logfile, socket.socket() as srv:
        LOG = logfile
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", LISTEN_PORT))
        srv.listen(16)
        log("LISTEN", f"relay up on {LISTEN_PORT} -> " + str(TARGETS))
        try:
            while True:
                client, addr = srv.accept()
                try:
                    threading.Thread(target=handle, args=(client, addr), daemon=True).start()
                except Exception:
                    client.close()
                    raise
        finally:
            with LOG_LOCK:
                LOG = None


if __name__ == "__main__":
    main()
