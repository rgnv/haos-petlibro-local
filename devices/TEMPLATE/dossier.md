# Device dossier — <MODEL> (<friendly name>)

> Copy this file to `devices/<MODEL>/dossier.md`, fill it out, and add your
> sanitized capture(s) next to it. Redact consistently (same placeholder for the
> same value everywhere). Delete these instruction lines.

## Identity

- Model code (app → device info): `<MODEL e.g. PLAF107>`
- Friendly name: `<e.g. "Fountain">`
- Firmware version: `<if shown>`
- Serial: `EXAMPLE_SERIAL` (keep the placeholder consistent in the capture!)
- MAC: `<MAC>` (optional; helps us match your capture's network evidence)
- Hardware revision: `<if shown>`

## Connectivity observed

- [ ] Talks to `mqtt.us.petlibro.com:1883` (MQTT — the good case)
- [ ] Talks to `api.us.petlibro.com:443` only (HTTPS polling)
- [ ] Not sure — capture attached, you tell us

## What the device can do (from the app UI)

List its controls/actions as the app shows them (dispense amounts? schedules?
sound? light? camera? battery?):
- <e.g. "Manual feed: 1–10 portions">
- <e.g. "Feeding schedule: up to 10 entries">

## Actions performed during the capture

For traceability — what you triggered and roughly when (clock time is fine):
1. `<e.g. "power-cycled the device at 12:00">`
2. `<e.g. "manual feed 2 portions at 12:02">`
3. `<e.g. "played sound at 12:03">`

## Capture files

- `capture-1.log` — `<description: e.g. "relay log of boot + actions above">`
- (add more if needed)

## Anything else

- Oddities, errors, LED behavior, app quirks, what you tried:
- <free-form>

## Checklist before submitting

- [ ] Ran `scripts/sanitize-capture.py <raw> --report` on every capture file
- [ ] Eyeballed the output — no credentials, no personal data, no real serials
- [ ] Redaction placeholders are consistent across files
- [ ] No app binaries or firmware files included
