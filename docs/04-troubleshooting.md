# Troubleshooting — every pitfall from the field

Collected from real deployments (both device families). If you hit something not
listed, the answer is usually "check the device's MQTT actually connects first."

## General

**My device disappeared from the vendor app.** Expected on the local path — it no
longer talks to the cloud. To give the app temporary access again: for Polar, run `sudo bash scripts/polar-takeover.sh relay`, which transparently forwards to
the real cloud while logging.

**How do I know the device is really on MY stack?** Watch your broker for its
client id (the device serial): reconnect events + heartbeats every ~100 s. For the
Polar: `mosquitto_sub -h 127.0.0.1 -p 1884 -t '#' -v`.

## Polar (PLAF109)

**No traffic when I watch the network.** Normal — it deep-sleeps. It wakes for
keepalives/events in bursts. Force a reconnect (deauth / broker bounce) instead of
waiting. Also note: forwarded device→internet unicast is invisible to router
`tcpdump` taps on many routers (hardware flow offload) — capture on your broker
side, not the router.

**Deauth command does nothing.** You need the right hostapd socket:
`ls /var/run/hostapd*` → use the folder for the radio carrying the device
(`-p /var/run/hostapd-wifi0 -i wlan0`). If your AP isn't hostapd-based, use a
router UI "block client" toggle or briefly restart the radio.

**A short network block didn't kick it off its session.** TCP survives short
blackouts by retransmitting. Either block for 15+ minutes, or send its session a
RST (router rules), or bounce your own broker (device reconnects within ~2 min).

**The device flooded my log with a 2099 error loop.** You (or your responder)
echoed its service replies. Fix: echo only event-class posts, and skip any payload
that contains `linkId`. See docs/03-protocol-reference.md ("the echo loop").

**Feed command ran but nothing moved / it replied 2099.** The plan-write shape
must be exact (`planId:666666666`, `feedNowState:true`, valid `plate`, positive
`feedingDuration`). A feed while another feed's door window is still open can be
rejected — space them out.

**NTP answers not reaching the device.** If you wrote your own responder, check
your NTP payload builds (we shipped a version with a missing import — unit tests
now cover it). The device re-asks periodically, so a fix takes effect within ~an hour.

## Tower (PLAF203)

**DEVICE_START_EVENT never fires / handshake stalls (fw ≥3.1.49).** The BINDING
reply patch isn't applied (or was wiped by an addon restart). Run `sh /mnt/data/supervisor/share/petlibro-patches/fix-addon.sh` on the HAOS host
and confirm the patch marker in the addon container. This is the #1 issue.

**Entities all unavailable.** Check (in order): (1) does the feeder's MQTT actually
connect to the addon's broker? (2) does the device's boot handshake complete
(DEVICE_START_EVENT)? (3) only then touch the addon. Entities appear only after the
reconcile marks the device online.

**Addon patches vanished after an update/restart.** Supervisor recreates the
container and wipes `/opt`. `fix-addon.sh` is idempotent — run it on the HAOS host, or arrange an external
SSH scheduler. The installer does not install HAOS cron.

**Motion notifications don't fire on injected test payloads.** The condition string-
matches the device's **compact** JSON (`{"cmd":"DETECTION_EVENT",...}` — no spaces).
Pretty-printed test payloads fail silently (check the automation trace).

**The camera takes 10–20 s on first view.** Normal: the first pull cold-starts the
device's P2P video link.

**Recorder records zero-byte files / stops after time.** Make sure you're running
our recorder (not a plain subscribe loop): subscription must be re-established on
every MQTT reconnect (clean session drops it), and clips must be written to a `.part`
then renamed. Both are handled in `tower/recorder/`.

**Retention deleted my other files.** Use our retention script — it only touches
its own clip naming pattern under its own directory. Never point it at a shared dir
with a blanket `*.mp4` delete.
