# MC6 — Mercury Launch Config Tool — Design Spec

**Date:** 2026-04-06 (rev3 — golden config verified against FW 2.30 HTML dump)
**Updated:** 2026-04-10 (renamed mercury-config → mc6, data directory migration)
**Status:** Implemented. 85 tests passing. AUR package: `python-c6-mc6-git`.
**Target:** FC6-Ground-Tools monorepo, Python, Arch laptop

---

## Purpose

A Python CLI tool that configures Mercury V1 altimeters for flight. Designed to be
used at the launch site under pre-launch stress. It must:

1. Autoconnect to a Mercury via USB CDC
2. Prompt for hardware actions (power button) when needed
3. Push a known-good flight config for the detected hardware revision
4. Ask for volatile pre-launch values (QNH)
5. Verify config was written correctly
6. Handle user mistakes, hardware failures, and its own bugs gracefully

**This is ground tooling for flight-critical hardware. A bug here can ruin a flight.
Write it with the same discipline as flight code.**

---

## Critical Technical Facts

### CDC is NOT UART

- **CDC** = USB virtual serial over USB-C. Used for commands (`ver&`, `app<pass>&`,
  `mode=flight+sensor&`). No flight telemetry unless explicitly triggered by
  `mode=flight+sensor&`.
- **UART** = Physical TX on GP6 pin, 921600 baud. Enabled by NVS key `uart=1`.
  Outputs telemetry automatically in flight mode.
- These are **completely different interfaces**. Never conflate them.

### CDC command set (FW 2.30+)

| Command | Response | Notes |
|---|---|---|
| `ver&` | `VER:2.3` (FW version only — no HW revision or serial) | Echoes back even if unrecognised (pre-2.30) — check for `VER:` prefix, not just echo. **Does not return hardware revision, serial number, or SSID.** |
| `app<password>&` | Sets AP WiFi password | Takes effect after reboot into WiFi mode |
| `w1s<ssid>&` | Sets STA WiFi SSID slot 1 | |
| `w1p<pass>&` | Sets STA WiFi password slot 1 | |
| `mode=flight+sensor&` | Enters flight mode with CDC telemetry | Sets `modeflight=1` in NVS. **Only way modeflight gets set.** |

### Mercury cannot be woken over CDC

The power button is a hardware latch. When Mercury is off, no USB device exists.
The tool MUST detect absence and prompt the user to press the power button.

### USB stays connected throughout the entire flow

USB provides power and prevents Mercury from entering deep sleep (USB holds a
wakelock, `esp_deep_sleep_start()` returns `ESP_ERR_SLEEP_REJECT`). When the user
presses the power button a second time (intending to shut down), the shutdown path
fails and Mercury falls through to WiFi mode. **This is the desired behaviour** —
USB powers the device while we configure it over WiFi.

The CDC serial port and WiFi AP are **simultaneously active** in WiFi mode. The tool
uses CDC for identity (Phase 2), then WiFi for config (Phases 3–8), without
disconnecting USB at any point. The serial port may be held open or closed during
WiFi phases — closing it is cleaner but not required.

### Config is HTTP-only

NVS settings (oversampling, sample_speed, uart, kalman, etc.) can ONLY be read/written
via the HTTP API at `http://192.168.0.1/settings/` and `/outputs/`. CDC cannot do this.
The HTTP API uses GET with query params and `sb=y` confirmation token.

### Launch detection uses sliding delta

Launch detect compares `current_altitude - oldest_buffered_altitude` (~0.6s window),
NOT absolute altitude. QNH errors do NOT affect launch detection — they only affect
logged altitude accuracy and any altitude-triggered outputs.

### Rev2 vs Rev3 config difference

Only `sample_speed` differs:
- **Rev2 (BMP390):** `sample_speed=50` — BMP ODR hardcoded 50Hz. Setting 100 is actively harmful (doubles velocity latency).
- **Rev3 (BMP581):** `sample_speed=100` — BMP ODR hardcoded ~80Hz regardless of this setting.

**⚠ `sample_speed` does NOT change the BMP sensor ODR.** It sets an internal
`rate_divisor` (1 at 50, 2 at 100) that scales the velocity finite-difference window
(12→24 samples), landing detection thresholds, and airbrake step sizes. The **only
sensor it actually changes** is the IMU (LSM6DSO32): 52Hz at `rate_divisor=1`,
104Hz at `rate_divisor=2`.

FC6 derives its own velocity from Mercury's altitude field (unaffected by
`sample_speed`) and does not use Mercury's velocity output at all. The only FC6-
relevant effect of `sample_speed=100` is the higher IMU ODR (104Hz vs 52Hz) giving
better angle data for tilt → horizontal velocity decomposition. Mercury's own
velocity latency penalty from the doubled FD window is irrelevant to us.

Everything else is identical between revisions.

---

## Dependencies

The tool must list these clearly in its README and check for them at startup:

| Dependency | Package | Why |
|---|---|---|
| Python ≥ 3.10 | `python` | Type hints, match statements |
| pyserial | `pip: pyserial` | CDC serial communication |
| requests | `pip: requests` | HTTP to Mercury web server (stdlib urllib is an alternative if we want zero pip deps beyond pyserial) |
| NetworkManager + nmcli | `networkmanager` (system) | WiFi AP connection management |
| User in `uucp` group | Arch-specific | Serial port access without root |

Optional:
| requests (for QNH) | Already required | Weather API pre-fetch |

---

## Managed Device Convention

- All Mercurys managed by this tool use AP password `05c69008`.
  (`05C6:9008` — the infamous USB VID:PID. You know the reference.)
- **Public repo note:** This password is in plaintext and will be visible to anyone
  reading the repo. Risk is low — it's a short-range WiFi AP password for an
  altimeter, not a credential for anything else. The actual risk is accidentally
  running this tool against someone else's Mercury at a launch event and overwriting
  their config with ours. **Phase 2.10 (adopt) must require explicit user
  confirmation with the device serial number visible.**
- Tool maintains a JSON file of known devices: `~/.mc6/devices.json`
  ```json
  {
    "b4:3a:45:99:0b:64": {
      "ssid": "MercuryAlt_8951",
      "revision": 2,
      "firmware": "2.30",
      "last_configured": "2026-04-05T14:30:00"
    }
  }
  ```
- On first contact with an unknown Mercury, tool offers to set password to `05c69008`
  via CDC `app05c69008&` and adds it to the managed list.

---

## Golden Config

**Provenance:** Values below are verified against a fresh FW 2.30 HTML dump
(`/settings/` page, 2026-04-06). Form field names are authoritative — the RE NVS
appendix had several name and value-mapping errors. Where the golden config differs
from the dump defaults, the override is annotated.

**⚠ RE NVS appendix corrections** (the HTML form is ground truth):

| Issue | RE said | Form says |
|---|---|---|
| `anglefilter` mapping | 0=Madgwick, 1=Mahony | **0=Mahony, 1=Madgwick** |
| `recordingstop` mapping | 0=auto 450, 1=manual, 2=auto 900 | **1=auto 450, 2=auto 900, 3=manual** |
| `leddimmer` | Percentage (default 20) | **Divisor** (5=20%, 1=100% brightest) |
| `kalmanfilter` NVS key | `pressurefilter` | Form field is `kalmanfilter` |
| `lockout_time` NVS key | `lockouttime` (no underscore) | Form field is `lockout_time` |
| `lockout_change` NVS key | `lockoutchange` (no underscore) | Form field is `lockout_change` |
| `sync_enable` NVS key | `syncsensors` | Form field is `sync_enable` (1=off, 2=on) |

### Fixed fields (same for both revisions, except sample_speed)

| Field | Rev2 Value | Rev3 Value | Endpoint | FW 2.30 Default | Notes |
|---|---|---|---|---|---|
| `uart` | 1 | 1 | /settings/ | 0 | **Override:** enable GP6 UART for FC6 |
| `sample_speed` | **50** | **100** | /settings/ | 50 on this dump | Rev-dependent (see technical notes above) |
| `oversampling` | 8 | 8 | /settings/ | 8 | Default |
| `iirfilter` | 7 | 7 | /settings/ | 7 | Default |
| `kalmanfilter` | 3 | 3 | /settings/ | 3 | Default (Kalman 2) |
| `anglefilter` | 1 | 1 | /settings/ | 1 | Default (Madgwick). **0=Mahony, 1=Madgwick.** |
| `launchprotection` | 1400 | 1400 | /settings/ | 1400 | Default. Form sends mG (1400 = 1.4G) |
| `launchdetect` | 25 | 25 | /settings/ | 25 | Default |
| `sample_ratio` | 1 | 1 | /settings/ | 10000 | **Override:** 1:1 every sample (default is Hybrid 1/3) |
| `max_samples` | 12000 | 12000 | /settings/ | 12000 | Default |
| `lockout_time` | 500 | 500 | /settings/ | 500 | Default (confirmed from dump, NOT 750) |
| `lockout_change` | 1.0 | 1.0 | /settings/ | 1.0 | Default (confirmed from dump, NOT 1.50) |
| `unit_alt` | 1 | 1 | /settings/ | 1 | Default (meters) |
| `unit_velocity` | 1 | 1 | /settings/ | 1 | Default (m/s) |
| `unit_acc` | 2 | 2 | /settings/ | 1 | **Override:** m/s² instead of mG. Display/CSV only — does NOT affect UART output. |
| `fixed_temp` | 15.0 | 15.0 | /settings/ | 15.0 | Default |
| `use_temp` | 0 | 0 | /settings/ | 0 | Default (disabled) |
| `calc_density` | 1 | 1 | /outputs/ | 1 | Default (enabled). FC6 does NOT compute its own density — Mercury must provide it. Confirmed on /outputs/ page. |
| `emode` | 1 | 1 | /settings/ | 1 | Default |
| `bat_mon` | 0 | 0 | /settings/ | 0 | Default (1 LED blink) |
| `leddimmer` | 5 | 5 | /settings/ | 5 | Default (20%). **Value is a divisor:** 5=20%, 1=100%. |
| `orientation` | 0 | 0 | /settings/ | 0 | Default (upright, USB down) |
| `recordingstop` | 1 | 1 | /settings/ | 1 | Default (auto 450). **1=auto 450, 2=auto 900, 3=manual.** |
| `sync_enable` | 2 | 2 | /settings/ | 2 | Default (enabled). **1=disabled, 2=enabled.** |
| `startup_lock` | 0 | 0 | /settings/ | 0 | Default |

### Volatile fields (set per-launch)

| Field | Range | Validation |
|---|---|---|
| `sealevel` | 950.0–1070.0 hPa | Warn if >5 hPa from weather API (if available) |

### Identity fields (read, don't overwrite)

| Field | Notes |
|---|---|
| `wifiname` | Mercury AP SSID |
| `wifipass` | AP password (should be `05c69008` for managed devices) |

### Ignored fields

Airbrake params, servo config, output/pyro config, action rules, I2C servo —
not part of the standard flight config. Left at whatever they are.

---

## Data Directory Migration

The tool was renamed from `mercury-config` to `mc6`. The user data directory moved
from `~/.mercury-config/` to `~/.mc6/`. Migration is handled automatically:

| Condition | Action |
|---|---|
| `~/.mercury-config/` exists AND `~/.mc6/` does not | Rename `~/.mercury-config/` → `~/.mc6/`. Print: `Migrated data: ~/.mercury-config/ → ~/.mc6/`. |
| `~/.mercury-config/` exists AND `~/.mc6/` also exists | Do nothing. Both coexist — user may have manually created `~/.mc6/`. No data loss. |
| Only `~/.mc6/` exists | Normal state post-migration. No action. |
| Neither exists | First run. Subdirectories created on demand by `devices.py`, `session_log.py`, `checkpoint.py`. |

Migration runs once, before argument parsing, on every entry point (`mc6` and the
legacy `mercury-config` alias). The legacy `mercury-config` command prints a
deprecation notice to stderr then delegates to `main()`.

**The old AUR package (`python-c6-mercuryconfig-git`) should be replaced by
`python-c6-mc6-git`.** The new package `conflicts=('python-c6-mercuryconfig-git')`
and `replaces=('python-c6-mercuryconfig-git')` so pacman handles the transition.
The legacy `mercury-config` entry point ensures scripts and muscle memory still
work during the transition period.

---

## Complete Flow

### Phase 0: Environment Check

| Step | Action | On failure |
|---|---|---|
| 0.1 | Check Python deps importable (pyserial, requests) | Print missing package name and install command. Exit. |
| 0.2 | Check user can access serial ports (try opening a test or check group membership) | Print: `sudo usermod -aG uucp $USER` then reboot. Exit. |
| 0.3 | Check `nmcli` available | "NetworkManager required. Install `networkmanager` or use `--manual-wifi` flag." Exit. |
| 0.4 | Check WiFi hardware: `nmcli radio wifi` | Prompt to enable. If no adapter: exit. |
| 0.5 | Record current active WiFi connection name (to restore later) | If none active: note it, proceed. |
| 0.6 | **Check internet access** (quick HTTP to a reliable endpoint, 3s timeout) | If no internet: warn that QNH auto-fetch won't work. Offer to proceed (manual QNH) or wait for user to connect. |
| 0.7 | **If internet available: pre-fetch QNH** from weather API. Store result. | If API fails: warn, proceed without. Non-critical. |
| 0.8 | Load golden configs (bundled in tool) | Fatal — tool is broken. Exit. |

### Phase 1: Device Discovery

| Step | Action | On failure |
|---|---|---|
| 1.1 | Scan `/dev/ttyACM*` for devices with ESP32-C6 USB VID:PID (`303A:1001`) | |
| 1.2 | If multiple matches: list all with serial info, ask user to pick | |
| 1.3 | If no device: print "Plug in the Mercury via USB-C and press the power button." | |
| 1.4 | Poll for device appearance (pyudev or 1s `/dev/` poll, **30s timeout**) | "Not detected after 30s. Is the cable data-capable? (Charge-only cables won't work.) Try a different cable or port." Exit. |
| 1.5 | Device appears: wait 500ms for CDC enumeration to settle | |
| 1.6 | Device appears then vanishes within 2s | "Mercury connected briefly then disappeared. Press the power button **once** (not twice) and wait for LEDs." Re-enter poll. **Max 3 retries** — after 3 appear-vanish cycles: "Device keeps disconnecting. Try a different USB cable or port." Exit. |

### Phase 2: CDC Identity

| Step | Action | On failure |
|---|---|---|
| 2.1 | Open serial port (115200, though CDC baud is irrelevant) | `PermissionError`: print usermod command, exit. `SerialException` (busy): "Close any terminal emulators (picocom, minicom, screen)." Exit. |
| 2.2 | Flush input buffer, wait 200ms | |
| 2.3 | Send `ver&`, read response with 3s timeout | |
| 2.4 | **Parse response for `VER:` prefix.** On 2.30+ the response is `VER:2.3` (firmware version only — no revision or serial). On pre-2.30, `ver` is unrecognised and only echoes back. Check for `VER:` prefix in response, not just whether we got bytes back. | If only echo (no `VER:` prefix): firmware is pre-2.30. Warn: "Firmware too old (pre-2.30). This tool requires FW 2.30+. Flash update needed." Offer guided esptool flash or exit. |
| 2.5 | Extract firmware version from `VER:` response. **`ver&` does NOT return hardware revision or serial number.** | |
| 2.6 | **Hardware revision:** Not available via CDC. Ask user: "What hardware revision is printed on the back of the PCB? (2 or 3)" (Could also auto-detect later via WiFi web server or BMP sensor probe, but CDC cannot provide this.) | |
| 2.7 | **Serial number / MAC:** Read from USB descriptor if available (ESP32-C6 USB-Serial/JTAG exposes MAC-derived serial). If unavailable: capture the boot banner on CDC — Mercury prints `"NRB Startup..."` with version/serial during cold boot (~0.7s after power-on). Failing both: ask user to read from device label. | |
| 2.8 | **SSID derivation:** The `MercuryAlt_XXXX` SSID suffix is MAC-derived, but the exact derivation algorithm is not fully documented. **Do NOT assume a simple MAC→SSID mapping.** If the device is in the managed list, SSID is already known. If new, we will discover it during WiFi scan (Phase 3). | |
| 2.9 | **Print device identity prominently:** serial number (if obtained), revision, firmware version. This must be visible and unambiguous throughout the session. | |
| 2.10 | Check managed devices list. If unknown Mercury: offer to adopt (set password to `05c69008` via `app05c69008&`). | |

### Phase 3: WiFi Mode & AP Discovery

| Step | Action | On failure |
|---|---|---|
| 3.1 | Scan `nmcli device wifi list` for SSIDs matching `MercuryAlt_*` | |
| 3.2 | **Multiple `MercuryAlt_*` SSIDs are expected** (other team Mercurys nearby). For known managed devices: match SSID from the managed devices list using the serial from Phase 2. For new/unknown devices: **SSID cannot be derived from MAC reliably** (derivation algorithm not fully documented). Strategy: list all visible `MercuryAlt_*` SSIDs, ask user to identify theirs. If only one is visible: offer it as default. **Future improvement:** once connected via WiFi, read `wifiname` from the web server and save the SSID↔serial mapping to the managed devices list for next time. | |
| 3.3 | If zero `MercuryAlt_*` found: Mercury may not be in WiFi mode. Print: "Mercury AP not visible. Is it in WiFi mode? If the blue LED is solid, it should be broadcasting. Try: power cycle (hold button until off, press once to restart) and wait 5 seconds." Rescan. | After 3 retries: exit with clear error. |
| 3.4 | AP password is `05c69008` for all managed devices. If device was just adopted in 2.8, it needs a power cycle for the new password to take effect — prompt user. | |

### Phase 4: WiFi Connection

| Step | Action | On failure |
|---|---|---|
| 4.1 | `nmcli device wifi connect "<SSID>" password "05c69008"` | Wrong password: "WiFi password rejected. This Mercury may not have the managed password set. Reconnect USB and re-run to set it." Exit. |
| 4.2 | | SSID not found: "Mercury AP vanished. Check power. Visible SSIDs: {list}." Retry. |
| 4.3 | | Timeout: retry once. Still failing: "WiFi connection failed. Move closer to Mercury, or check if another device is already connected." |
| 4.4 | Verify HTTP: `GET http://192.168.0.1/settings/` with 5s connect timeout | Connection refused / timeout: "Connected to WiFi but web server not responding. Mercury may still be booting (wait ~5s after power-on)." Retry 3 times with 3s gaps. If still dead: "Power cycle Mercury and restart tool." Exit. |

### Phase 5: Config Read & Parse

| Step | Action | On failure |
|---|---|---|
| 5.1 | `GET http://192.168.0.1/settings/` | HTTP error: retry once. If still bad: log raw response, print error, **print golden config so user can manually verify/set via browser**, exit. |
| 5.2 | Parse HTML form: extract all `<input>` values and `<select>` selected options. **Validate field count:** if the parser finds fewer fields than the golden config expects, treat as parse failure — do not silently proceed with a partial read. | Parse failure: dump raw HTML to `mercury_debug_{serial}_{timestamp}.html`. **Print the golden config for this revision** so user has a reference for manual configuration. Exit. |
| 5.3 | `GET http://192.168.0.1/outputs/` and parse (same pattern) | Same — dump, print golden, exit. |
| 5.4 | Build `current_config` dict from parsed values | |

### Phase 6: Config Verification & Diff

| Step | Action | On failure |
|---|---|---|
| 6.1 | Select golden config for detected revision (Rev2 or Rev3) | |
| 6.2 | Classify each field: **Fixed** (must match golden), **Volatile** (expected to differ), **Identity** (don't touch), **Ignored** (skip) | |
| 6.3 | Print full report with serial number header: | |
| | `// Mercury Config Report — S/N: b4:3a:45:99:0b:64 (Rev2, FW 2.30)` | |
| | ✓ Fixed fields matching golden | |
| | **✗ Fixed fields NOT matching — bold, highlighted, show expected vs actual** | |
| | ⚠ Volatile fields — print current value, flag for update | |
| 6.4 | If all fixed fields match: "Config verified. Only volatile fields need updating." | |
| 6.5 | If mismatches: "Config mismatches found. Push corrected config? (y/n)" Show exact diff. | |

### Phase 7: Volatile Input

| Step | Action | On failure |
|---|---|---|
| 7.1 | Display current `sealevel` value from device | |
| 7.2 | If QNH was pre-fetched (Phase 0.7): display it as suggestion | |
| 7.3 | Prompt: `QNH / sea-level pressure (hPa) [current: {val}]:` | |
| 7.4 | Empty input = keep current value. Print "Keeping: {val} hPa" | |
| 7.5 | Validate: must be float, 950.0 ≤ QNH ≤ 1070.0 | Out of range: "Outside normal atmospheric range (950-1070 hPa). Are you sure? (y/n)" Allow override with confirmation. |
| 7.6 | If pre-fetched QNH available and differs by >5 hPa from user input | "Your value ({input}) differs from forecast ({api}) by {delta} hPa. Which do you want to use? [u]ser / [f]orecast" |

### Phase 8: Config Push & Verify

| Step | Action | On failure |
|---|---|---|
| 8.1 | Build full query string using **read-modify-write**: start with ALL current values read in Phase 5, overlay golden config values for fixed fields, overlay user input for volatile fields, leave identity and ignored fields at their current read-back values. Include `sb=y`. **Never omit fields** — until Open Question 4 is resolved, we must assume that omitted fields may be zeroed by the firmware. The query must contain every field that appeared in the Phase 5 read-back. | |
| 8.2 | `GET /settings/?sb=y&...` | HTTP error: retry once. If still failing: "Config write failed. Check Mercury power." **Print volatile values that may need manual entry** if user must fall back to browser. Exit. |
| 8.3 | `GET /outputs/?sb=y&...` (if output config is part of golden) | Same pattern. |
| 8.4 | **Read back**: `GET /settings/`, parse, compare against what we sent | |
| 8.5 | Every field matches: "Config verified. All {N} fields written and confirmed." | |
| 8.6 | Mismatch on read-back: **"WRITE VERIFICATION FAILED."** List failing fields. "Do NOT fly with unverified config. Retry? (y/n)" This is a serious error — NVS may be full or firmware has a bug. | |

### Phase 9: Flight Readiness

| Step | Action |
|---|---|
| 9.1 | Print final config summary with serial number, revision, firmware, all field values. Highlight volatile values. |
| 9.2 | Print **`// MERCURY IS GO`** in C6 Accent (`#e64097`) or nearest terminal equivalent. Large, distinctive, unmissable. |
| 9.3 | Print: "To arm for flight:" |
| | "  1. Disconnect USB" |
| | "  2. Install Mercury in rocket (USB port down)" |
| | "  3. Press power button once" |
| | "  4. Wait for green flashing LED (~8 seconds)" |
| | **"  5. Remember to check we're showing the geen light to UKROCism before launch"** |
| 9.4 | **Do NOT enter flight mode.** Do NOT send `mode=flight+sensor&`. User arms at the pad. |

### Phase 10: Teardown

| Step | Action | On failure |
|---|---|---|
| 10.1 | Disconnect from Mercury WiFi: `nmcli connection down <mercury_connection>` | Non-critical. Warn: "Couldn't auto-disconnect Mercury WiFi. Reconnect to normal network manually." |
| 10.2 | Restore previous WiFi (from Phase 0.5): `nmcli connection up <saved_name>` | **Print prominently:** "⚠ Could not restore WiFi. **Your laptop is NOT connected to the internet.** Reconnect manually." (At a launch site with no other WiFi, restoration will always fail — make this obvious.) |
| 10.3 | Close serial port | |
| 10.4 | Save session log to `~/.mc6/logs/config_{serial}_{timestamp}.log` | |
| 10.5 | Update managed devices list with last-configured timestamp | |
| 10.6 | Print log file path | |

---

## Cross-Cutting Concerns

### Timeouts

Every serial read, HTTP request, and nmcli command has an explicit timeout.
No unbounded waits. No `while True` without a counter. Specific values:

- Serial read: 3s
- HTTP connect: 5s
- HTTP read: 10s
- nmcli operations: 15s
- Device discovery poll: 30s total, 1s intervals

### Session Logging

Every action, response, user input, and error → appended to a timestamped log file.
This is the audit trail. If a flight goes wrong, we can prove what the tool configured.
Log path: `~/.mc6/logs/config_{serial}_{timestamp}.log`

### Ctrl+C / SIGINT Handling

Graceful teardown: restore WiFi, close serial port, save partial log. Never leave
the laptop stuck on Mercury WiFi. Print what was and wasn't completed.

### Error Logging

On ANY error, log:
- What we were trying to do
- What we got instead (raw response, exception, etc.)
- What the user should do next
- Current state of config (what was pushed, what wasn't)
- **Volatile values that may need manual entry** (if we had fetched QNH but failed mid-push)

### No Implicit State

The tool reads config from the device every time. No "I set this last run" caching.
If you run it twice, it reads twice. The device is the source of truth.

### Serial Number Visibility

The Mercury serial number (MAC address) must be printed:
- At initial identification
- In the config report header
- In the final GO/NO-GO summary
- In the log file name
- Never ambiguous which device is being configured

---

## UI / Terminal Style

Adapted from C6 style guide for terminal output. Uses 24-bit truecolor (explicit RGB)
where supported so terminal themes and transparency cannot remap colours. Falls back to
256-color / basic ANSI on older terminals.

On startup, the tool forces the terminal background to C6 Void (`#0a0a0b`) via OSC 11,
ensuring opaque dark surface regardless of user transparency settings. Background is
restored to the terminal default (OSC 112) on every exit path including SIGINT.

| Element | Treatment | Colour |
|---|---|---|
| Section headers | `// SECTION NAME` prefix, bold | C6 Accent `#e64097` |
| Success | Green checkmark (✓) | `rgb(52, 211, 153)` / ANSI 32 fallback |
| Warnings | Amber warning sign (⚠) | `rgb(251, 191, 36)` / ANSI 33 fallback |
| Errors | Red cross (✗), bold | `rgb(248, 113, 113)` / ANSI 31 fallback |
| `MERCURY IS GO` | Bold, prominent | C6 Accent `#e64097` |
| Field values | White/cloud for current, red for mismatches | |
| Prompts | `//` prefix | C6 Accent `#e64097` |
| Serial number | Always bold | C6 White `#f5f5f7` / bold white fallback |
| Body text | Default terminal colour (light) | |

No emojis. Checkmarks (✓) and crosses (✗) for pass/fail. Clean, legible under sunlight on a laptop screen.

---

## Directory Structure

```
mc6/
├── mc6/
│   ├── __init__.py
│   ├── main.py              # Entry point, CLI arg parsing
│   ├── discovery.py          # Phase 0-1: env check, device discovery, USB polling
│   ├── cdc.py                # Phase 2: serial communication, ver& parsing
│   ├── wifi.py               # Phase 3-4: nmcli wrapper, AP connection
│   ├── http_config.py        # Phase 5: HTTP read, HTML parsing
│   ├── config_engine.py      # Phase 6-8: golden configs, diff, push, verify
│   ├── ui.py                 # Terminal output formatting, colors, prompts
│   ├── weather.py            # QNH fetch (optional, best-effort)
│   ├── devices.py            # Managed device list (JSON persistence)
│   └── session_log.py        # Logging to file
├── golden_configs/
│   ├── rev2.json
│   └── rev3.json
├── tests/                    # Unit tests for each module
├── pyproject.toml
├── README.md
└── LICENSE
```

Each module has one clear responsibility. No circular imports. `ui.py` is the
only module that prints to stdout. Everything else returns data or raises exceptions.

---

## What This Tool Does NOT Do

- Enter flight mode (`mode=flight+sensor&`) — user arms at the pad
- Configure outputs/pyro — rocket-specific, not part of standard config
- Configure action rules — same
- Configure airbrake parameters — same
- Flash firmware — separate esptool workflow
- Download flight data — separate tool concern
- Talk to AltimeterCloud — we avoid it deliberately

---

## Open Questions

1. ~~**`ver&` response format**~~ — **RESOLVED.** Response is `VER:2.3`. Parser:
   check for `VER:` prefix.

2. ~~**HTML form structure**~~ — **RESOLVED.** `/settings/` HTML captured 2026-04-06.
   All form field names, value mappings, and valid options now documented in golden
   config table. Parser extracts `<input>` and `<select>` values. Form uses
   GET with `sb=y` confirmation token.

3. ~~**Weather API**~~ — **RESOLVED.** Open-Meteo (no API key required). Implemented
   in `weather.py` with 5s timeout and graceful fallback.

4. **Absent fields behaviour**: Does sending `sb=y` with only SOME fields zero out
   the missing ones? Not yet tested empirically. Implementation uses read-modify-write
   (sends ALL fields) as the safe default. The form includes hidden field
   `adjustlaunchangle=0` and ROC2 fields — these are preserved in read-modify-write.

5. ~~**Golden config field name audit**~~ — **RESOLVED.** The RE NVS appendix had
   several wrong names and value mappings. The HTML form is ground truth. See
   corrections table above golden config.

6. ~~**`launchprotection` units**~~ — **RESOLVED.** Form sends mG (1400 = 1.4G).

7. **SSID↔MAC derivation**: Still undocumented. The dump shows `MercuryAlt_4679`.
   Collecting more MAC→SSID pairs would let us reverse-engineer the derivation.
   Current implementation saves the SSID↔serial mapping on first contact.

8. ~~**Boot banner serial extraction**~~ — **RESOLVED.** Implemented in `cdc.py`
   (`try_capture_boot_serial`). Works when boot is caught within ~0.7s. Falls back
   to USB sysfs descriptor, then manual entry.

9. ~~**`/outputs/` page structure**~~ — **RESOLVED.** `/outputs/` HTML captured
   2026-04-06. `calc_density` confirmed on `/outputs/`. Parser handles both pages.

10. **`unit_acc=2` effect on UART**: Does changing the acceleration unit setting
    affect UART output format, or only web UI and flash CSV? If UART changes, FC6's
    parser needs to know. Not yet tested empirically.
