# Public release review — 2026-10-09

## Result and scope

Reviewed README first, then docs, then every tracked script/module, test, unit,
configuration example and contribution document. Fixed reproducible local defects
and instructions that did not match implementation. No network operations,
package installations, deployments, feeder commands or pushes were performed.
All test artifacts and writes stayed inside this repository.

**Offline checks pass. This is not a hardware certification or a clean-history
privacy certification.** Remaining release decisions are listed below.

The initial checkout was clean at `357d79f`. During this review, external commits
`426d84e`, `3a6d862`, `3353f50`, `2980d0e` `8c196c8` `9d6b71b` and `42cd318` appeared; they added docs/06 and incorporated
some in-progress code fixes. Later wizard replacements reintroduced hardcoded
configuration, listener lifetime defects and removed the relay command; those
regressions were repaired while preserving the new manual-router/discovery flow. Those commits were preserved. The new guide was also
reviewed. This report describes the complete review, not only the final commit's
diff against those intervening commits.

## Findings and fixes

| Area | Problem | Change |
|---|---|---|
| Polar installation | No serial configured; service silently subscribed to example topics. Nondefault port changed the listener/pinned endpoint but not the service's connection. Helpers had no persistent config. | Required validated serial and reachable IPv4 arguments; one `/etc/default/polar` file shared by systemd and wizard; both ports configured; direct-helper environment loading documented. |
| Polar defaults | Pin target was a concrete example LAN address; feed helper accepted placeholder serial. Timezone was deployment-specific. | Removed default pin address; added startup/feed/pin validation; configurable IANA timezone defaults to UTC. Fractional timezone hours no longer round down. |
| Installer failures | Dependency failures were warnings; service checks could fall through to success; reruns did not restart active services. | Fail on dependency/health errors, check paho 2.x API, install capture dependency, restart updated services, enable broker at boot. Config permissions explicitly set. |
| Capture wizard | Listener could encounter stdin EOF; missing socat; detached processes persisted; old capture overwritten; custom port ignored. | One-way raw listener, process-group cleanup on exit, liveness check, refusal to overwrite captures, shared configured port. Start listener before reconnect. |
| Relay | Detached relay could conflict with broker; static vendor IP fallbacks could become stale or ignore custom upstream. | Foreground relay with explicit return-to-serve procedure; resolve only configured upstream hostname; correct configured-port logging. |
| Tower patches | Example serial/IP hardcoded; no persistent responder config; SSH key split on spaces; cron setup falsely reported success on HAOS. | Validated serial/broker env, persisted addon config, configurable timezone, SSH argument array, explicit host debug SSH requirements. Removed unsupported automatic cron claim/action; documented external scheduling. |
| Recorder | No configured serial/RTSP URL; subscription lost after reconnect; direct final MP4 output; ffmpeg failures ignored; directory creation could wedge recording state; malformed/retained messages mishandled. | Required config, reconnect subscription, `.mp4.part` output with explicit format, checked ffmpeg exit status and nonempty file before rename, state cleanup, event guards, free-space and directory checks. Logs avoid exposing credential-bearing ffmpeg argv on failure. |
| Recorder installation/units | Required environment values absent; unescaped paths could break sed/systemd; credentials created before chmod and passed as argv. | Validated restricted path/URL formats, private env/config creation, no password argv, required EnvironmentFile; restart on reinstall. Existing mount dependency retained. |
| Retention | Root path was interpreted as regex syntax. | Change into root and match relative paths; tests verify old owned clips removed while unrelated files survive, even with regex metacharacters in root. |
| HA automation | Python builder differed from JSON example, included pet-name residue, used wrong dashboard tap path, and swallowed HTTP failure. | Use JSON example as single source; validate serial/notify placeholders; HA URL/token env; correct `/tower-feeder/tower` path; HTTP errors fail. |
| Dashboard | Placeholder serial accepted; API failures merely printed; URL path lacked hyphen expected for custom dashboard creation. | Validate serial, propagate API errors, reuse/create `tower-feeder` dashboard, align notification path and docs. |
| Sanitizer | Short CONNECT credentials could survive; escaped JSON strings partially redacted; topic serials outside AF pattern survived; raw binary/socat support overstated. | Drop entire MQTT lines in textual logs; handle escaped JSON strings and topic serials; reject raw binary/socat input. Still heuristic, explicitly requires manual review. |
| Guides | Wrong/missing commands, env, paths, log filenames, HA dependencies and installer scope; TLS redirected to plaintext; DNS port mismatch; permanent/network-independent and rollback claims overstated. | Corrected guides, added environment table, clarified router return-path needs, optional capture, service boot-time pinning, ACK verification, real retention age, schedules and deployment limits. |
| Contributions | Sanitizer output directory did not exist; dossier layout disagreed with commands; ignored capture filename encouraged force-add. | Create model directory first; use `capture-sanitized.log`; copy dossier template into correct directory. |

## Privacy review

Current tracked source contains no detected real device serial, MAC, account email,
member ID, password/token, JWT, private key, or concrete private LAN IP. This result
combines manual reading with pattern scans; it is not a mathematical guarantee.

Removed concrete LAN addresses from runtime defaults, automation, bridge config,
and comments. Removed the personal pet-name residue and host nickname. Replaced a
synthetic test LAN address with an RFC 5737 documentation address. Removed
credential-prefix examples from the newly added docs/06 without reproducing them
here. Stale public cloud IPs were removed for reliability, not because they were
personal data.

Conventions are documented in README: `EXAMPLE_SERIAL`, `192.168.x.x`, other
`YOUR_*` configuration slots, and descriptive angle-bracket fields. Tests use
`TESTSERIAL` and `192.0.2.20`; these are synthetic fixtures. Negative tests retain
invalid legacy placeholder spellings intentionally. Sanitizer output uses named
redaction markers; executable configuration rejects serial placeholders.

**Still present / release follow-up:**

- LICENSE contains the public copyright holder handle `rgnv`. It was preserved
  because the MIT copyright notice must remain. Upstream attribution also remains.
- Git author/committer metadata identifies contributors. Historical commits retain
  earlier concrete example LAN addresses, pet-name residue, and credential
  prefixes from docs/06. Removing text in this commit does not erase history.
  If the public release must have no such history, prepare a separately reviewed
  sanitized release history/export and address any real exposed credentials with
  their issuer. No history rewrite was attempted, especially with concurrent work.
- Captures remain sensitive. Relay TCP chunks can split credentials across lines,
  and unknown fields/encodings can evade the sanitizer. Never treat its output as
  automatically safe to publish. Gitignore is only an accident guard.

## Validation

- `python3 -m unittest discover -s polar/tests -q`: **29 passed**, before and after.
  Updated configuration/timezone fixtures preserve the original behavior checks.
- `python3 -m unittest discover -s tests -q`: **9 passed**. Covers rejected
  configuration, nondefault-port installation and wizard serve/pin/relay using local command stubs,
  fractional timezone offsets, sanitizer regressions, recorder reconnect and
  atomic output/failure handling, credential-safe failure logs, retention isolation.
- `py_compile`: **all 12 Python files** compile, including new release tests.
- `bash -n`: **all 6 shell scripts** pass. `sh -n` also passes for POSIX addon fixer.
- `systemd-analyze verify`: **both rendered Tower service templates, Tower timer,
  and Polar service pass** under an isolated root inside `.review/`, using stub
  executable/dependency units. No host service was started. Direct host verification
  encounters absent deployment executables/Mosquitto and unrelated host unit
  warnings; isolated verification avoids conflating those with template syntax.
- Local Markdown link targets and `git diff --check` pass. External URLs were not
  fetched. Placeholder examples intentionally require substitution before execution.

## Remaining limits and follow-ups

1. **Broker security:** Polar listener allows anonymous LAN clients to issue
   physical commands. Deploy only on an isolated trusted network with firewall
   restrictions. Existing shared Mosquitto auth/listener configuration needs review;
   installer does not merge arbitrary broker policies or install firewall rules.
2. **Physical scheduling:** Polar deduplication is process-local; restarting during
   a scheduled minute can duplicate a feed. MQTT publish acceptance is not device
   execution confirmation. No durable exactly-once execution claim is made.
3. **Tower protocol:** Responder sends empty feeding plans and infers sessions from
   300 seconds of silence. Check saved schedules and fast reconnect behavior on
   hardware. Addon anchors, container slug, entity IDs, paho version and camera
   behavior depend on upstream versions; no upstream source was fetched.
4. **Storage:** Configure external mounts persistently before recorder install.
   RequiresMountsFor handles startup dependencies, not every later mount failure.
   Free-space check is not a quota; retention has no special filesystem snapshot
   awareness. Snapshot notification images have no automatic cleanup.
5. **Offline scope:** No real SSH, MQTT, socat listener, router NAT, ffmpeg/RTSP,
   HA REST/WebSocket, package-manager, or systemd deployment integration was run.
   Hardware and target-host smoke tests remain necessary before advertising the
   generalized installation flow as end-to-end verified.
6. **Network isolation:** DEVICE_CONFIG_SYNC retains the vendor HTTPS hostname.
   MQTT pinning alone does not prove the device makes zero internet requests;
   enforce and verify egress policy separately. Reprovisioning/factory resets or
   firmware changes may undo the stored endpoint.

## Concurrent protocol addition

`docs/protocol/` was added and committed externally during final verification.
All six new documents were then read. Fixed contradictory CONNECT flags by making
that packet an explicitly illustrative C2 example, corrected PUBLISH byte counts,
removed the unsupported 7.4-second keepalive claim, corrected QoS/echo statements,
and aligned sanitizer/runtime limitations. Removed a full MAC example and partial
serial/message IDs. The new committed MAC example remains in prior Git history.
Raw captures are unavailable here, so device-specific observational claims beyond
the code cannot be independently verified offline. Changes after this snapshot
require another review.

The final review commit uses a generic review identity because this checkout has
no configured Git author. No global Git configuration was changed.
