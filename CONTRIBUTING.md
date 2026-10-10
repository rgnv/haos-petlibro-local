# Contributing — help us add support for more Petlibro devices

We've fully mapped two device families (**PLAF203** camera feeder, **PLAF109** wet
feeder). Every other Petlibro product (feeders, fountains, others) is fair game —
and the fastest way is **you sending us a sanitized capture of yours**.

You don't need to be a developer. If you can run three commands and redact a log,
you can submit a device dossier.

## What we need (the dossier)

A complete dossier lets us map a device without owning it. It contains:

1. **Device identity** — model/name from the Petlibro app, serial (redacted is fine —
   keep it consistent so we can trace it through the log), firmware version if shown.
2. **A relay capture** of the device's boot + a few actions (e.g. dispense, open
   lid, play sound — whatever it can do). ~2–3 minutes of activity is plenty.
   *How:* put a transparent relay between the device and the cloud (see
   `docs/03-protocol-reference.md` → "Discovery methodology"; the Polar guide
   `docs/02-polar-plaf109.md` walks the same flow).
3. **The device's cloud metadata** — the JSON your HA/cloud integration shows for it
   (rename fields freely but keep structure), or screenshots of the app's device info.

## Step-by-step

```bash
# 1. Capture (example using the polar tooling — works for any MQTT device):
#    redirect the device's 1883 to your host, then capture through the relay.
#    Stop any broker on port 1884 first; Ctrl-C stops the relay.
sudo python3 polar/polar_relay.py        # relay + logging to /root/polar_relay.log

# 2. Sanitize — never submit raw captures:
mkdir -p devices/YOUR_MODEL
sudo python3 scripts/sanitize-capture.py /root/polar_relay.log --report > devices/YOUR_MODEL/capture-sanitized.log
#    REVIEW the output by eye. If anything personal remains, redact it manually.

# 3. Add a dossier folder and a short description:
cp devices/TEMPLATE/dossier.md devices/YOUR_MODEL/dossier.md
#    fill out your new dossier.md; replace YOUR_MODEL with your model code

# 4. Fork this repo, add your folder, open a Pull Request.
#    Title: "device dossier: <MODEL> (<friendly name>)"
```

**What happens then:** we read the capture, map the protocol (topics, commands,
events), and — if it's feasible — publish support (a section in docs/, tooling,
and tests) credited to your dossier. If we need one more capture (e.g. a specific
action), we'll say exactly which and why.

## Rules (important)

- **Never submit credentials or anything that identifies you**: device
  passwords/keys, your account email/member id, your LAN addresses, WiFi SSID.
  The sanitizer covers the common cases — eyeball the output anyway ("review
  before committing" is printed for a reason).
- **Redact consistently.** If you replace your serial with `EXAMPLE_SERIAL`, keep the
  same placeholder everywhere so message correlation still works.
- Don't include vendor app binaries or firmware files in PRs.
- One device model per PR/dossier folder.

## Beyond dossiers

Code contributions are welcome too — common areas: support for more router types
in the redirect tooling, Home Assistant blueprints/dashboards, protocol parsers.
Keep the pattern of the existing code: plain Python (stdlib + paho), small units,
tests next to the module, no credentials in source.

## devices/ folder layout

```
devices/
├── README.md            — index: model → status → link to dossier
├── TEMPLATE/dossier.md  — copy this for your submission
└── <MODEL>/
    ├── dossier.md       — filled-out description
    └── capture-sanitized.log — sanitized capture(s)
```
