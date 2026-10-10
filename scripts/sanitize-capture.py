#!/usr/bin/env python3
"""sanitize-capture.py — make a Petlibro device capture safe to publish.

Reads a textual relay log or JSON-lines transcript and heuristically redacts
common personal fields. Raw binary/socat captures are rejected:

  serial numbers, MAC addresses, MQTT credentials (CONNECT packets and JSON
  username/password/token fields), member/user ids, emails, private IPs, SSIDs.

Usage:
    python3 sanitize-capture.py raw.log > sanitized.log
    python3 sanitize-capture.py raw.log --report     # show what got replaced

Always EYEBALL the output before committing — heuristics can miss things.
"""
import argparse
import re
import sys

TEXT_RULES = [
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "<EMAIL>"),
    ("topic_serial", re.compile(r"(dl/[A-Za-z0-9]+/)[^/\s]+(?=/device/)"), r"\1EXAMPLE_SERIAL"),
    ("serial", re.compile(r"\bAF[0-9A-Z]{10,}\b"), "EXAMPLE_SERIAL"),
    ("mac_colon", re.compile(r"\b(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\b"), "<MAC>"),
    ("mac_plain", re.compile(r"\b[0-9a-f]{12}\b"), "<MAC>"),
    ("memberId", re.compile(r'("(?:memberId|userId|accountId|uid)"\s*:\s*)"?\d+"?'), r'\1"<MEMBER_ID>"'),
    ("json_password", re.compile(r'("(?:password|passwd|api_token|token|accessToken|refreshToken)"\s*:\s*)"(?:\\.|[^"\\])*"'), r'\1"<REDACTED>"'),
    ("json_username", re.compile(r'("username"\s*:\s*)"(?:\\.|[^"\\])*"'), r'\1"<REDACTED>"'),
    ("ssid", re.compile(r'("(?:wifiSsid|ssid)"\s*:\s*)"(?:\\.|[^"\\])*"'), r'\1"<SSID>"'),
    ("private_ip", re.compile(r"\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"), "<LAN_IP>"),
    ("private_ip_192", re.compile(r"\b192\.168\.\d{1,3}\.\d{1,3}\b"), "<LAN_IP>"),
    ("private_ip_172", re.compile(r"\b172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}\b"), "<LAN_IP>"),
    ("uuidish_creds", re.compile(r"\b[A-Z0-9]{16,32}\b(?=[^A-Za-z0-9]|$)"), None),  # flagged manually
]

# The generic "long uppercase token" rule is too risky to auto-replace (it matches
# serials already handled and some topic ids) — we only report its occurrences.


def sanitize_mqtt_lines(data: bytes) -> bytes:
    """Drop whole CONNECT lines from textual relay logs, including short secrets."""
    return b"\n".join(b"<MQTT_CONNECT_REDACTED>" if b"MQTT" in line else line
                      for line in data.split(b"\n"))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input")
    ap.add_argument("--report", action="store_true", help="print replacement counts")
    args = ap.parse_args()

    raw = open(args.input, "rb").read()
    if any(c < 32 and c not in (9, 10, 13) for c in raw) or re.search(rb"(?m)^[<>] \d{4}/", raw):
        ap.error("raw binary/socat captures require manual packet-aware redaction; use a textual relay log")
    data = sanitize_mqtt_lines(raw)

    counts = {"mqtt_connect_lines": sum(1 for l in raw.split(b"\n") if b"MQTT" in l)}
    text = data.decode("utf-8", errors="replace")
    for name, rx, repl in TEXT_RULES:
        if repl is None:
            counts[name] = counts.get(name, 0) + len(rx.findall(text))
            continue
        text, n = rx.subn(repl, text)
        counts[name] = counts.get(name, 0) + n

    sys.stdout.write(text)
    if args.report:
        print("", file=sys.stderr)
        print("-- sanitize report --", file=sys.stderr)
        for k, v in counts.items():
            if v:
                print(f"  {k}: {v}", file=sys.stderr)
        print("REVIEW the output manually before committing!", file=sys.stderr)


if __name__ == "__main__":
    main()
