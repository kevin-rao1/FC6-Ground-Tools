# Mercury Config Tool — Operator-Absent Hardening

**Date:** 2026-04-06
**Scope:** mercury-config (FC6 Ground Tools)
**Status:** Design — awaiting implementation plan

## Problem

The Mercury Config Tool currently relies on an attentive operator to catch
mistakes. Several points in the flow allow silent error propagation:

- Hardware revision is manually entered every run, even for known devices.
  Wrong revision → wrong golden config → wrong `sample_speed` → FC6 firmware
  mismatch. Rev.2 at 50 Hz vs Rev.3 at 80 Hz affects MPC rate, FSM timing,
  and everything downstream.
- QNH accepts Enter-to-keep, which lets a lazy operator skip setting pressure
  without making a conscious choice. On a day where pressure has shifted,
  this produces silently wrong altitude data.
- The GO gate fires *before* the optional browser calibration session. Any
  changes made in the browser are unverified. The tool declares GO on state
  that may no longer be true.
- A crash, `^Z`, or hard kill mid-session leaves the device in an unknown
  state with no record of what happened.
- Warnings printed mid-session can be forgotten by the time the operator
  reaches the end.
- QNH forecast uses hardcoded central-UK coordinates rather than the actual
  launch site.
- No airframe designation tracking — nothing ties a Mercury config session
  to a specific rocket.

## Solution Overview

Six changes, one coherent flow restructure:

1. **Warning registry** — new module that accumulates flight-safety warnings
   and replays them at the GO gate, each requiring explicit `ACCEPT`.
2. **Taint checkpoint** — session state persisted to disk; deleted only on
   successful GO. Crashed sessions force full re-run.
3. **Revision from managed devices** — known devices use stored revision,
   never re-prompted. Rev.2 triggers a loud, specific warning about FC6
   compatibility.
4. **QNH hardening** — no empty input, no shortcuts. Mandatory numeric entry.
   Launch site selection drives site-specific forecast.
5. **Mandatory second verification** — after any browser session (or even
   without one), the tool re-reads the device and re-verifies against golden
   config before the GO gate.
6. **Flight Readiness Review** — final confirmation screen showing full
   device summary, replaying all warnings with per-warning ACCEPT, airframe
   designation entry, and explicit GO typed by the operator.

### New Flow

```
Phase 0   Environment check + launch site selection + taint check
Phase 1   Device discovery
Phase 2   CDC identity + revision (from managed list or physical inspection)
Phase 3   WiFi mode & AP discovery
Phase 4   WiFi connection
Phase 5   Config read & parse
Phase 6   Config diff report
Phase 7   QNH (mandatory numeric entry, site-specific forecast)
Phase 8   Config push + first verification
Phase 8.5 Optional browser calibration
Phase 8.6 Second read-back + re-verification (always runs)
Phase 9   Flight Readiness Review (summary → warnings → airframe → GO)
Phase 10  Teardown (delete taint checkpoint on GO)
```

## 1. Warning Registry

### Module: `warnings.py`

New module. Accumulates flight-safety warnings throughout the session and
replays them at the Phase 9 review gate.

```python
_warnings: list[tuple[str, str]] = []

def register(category: str, message: str) -> None:
    """Register a flight-safety warning.

    Immediately prints via ui.warn() and logs to session log.
    Stored for replay at Phase 9.
    """
    ...

def get_all() -> list[tuple[str, str]]: ...
def clear() -> None: ...
def count() -> int: ...
def serialise() -> list[dict[str, str]]: ...   # For taint checkpoint
def deserialise(data: list[dict[str, str]]) -> None: ...  # For taint display
```

Every call to `register()`:
- Calls `ui.warn(message)` — prints immediately at the relevant phase
- Calls `session_log.log("warning", f"[{category}] {message}")`
- Appends `(category, message)` to the internal list

**Call sites replace `ui.warn()` with `warnings.register()` for any
condition that must be reviewed before flight.** Regular `ui.warn()` remains
for informational messages that do not gate GO (e.g., "WiFi restore failed").

### Grepability

All flight-safety warnings in the codebase are findable with
`grep warnings.register`. No warning can reach the GO gate without passing
through this function.

## 2. Taint Checkpoint

### File location: `~/.mercury-config/sessions/<serial>.json`

### Lifecycle

1. **Created** at Phase 2, when device identity is confirmed.
2. **Updated** at each phase boundary (Phase 8 push, Phase 8 verify,
   Phase 8.5 browser, Phase 8.6 second verify).
3. **Deleted** only on successful GO (Phase 9 complete).

### Schema

```json
{
  "serial": "b4:3a:45:99:0b:64",
  "revision": 2,
  "firmware": "2.30",
  "started": "2026-04-06T14:30:00",
  "phase_reached": "phase8_push",
  "config_pushed": true,
  "first_verify_passed": true,
  "browser_opened": false,
  "second_verify_passed": false,
  "qnh_value": "1013.25",
  "launch_site": "Cox's Field",
  "warnings": [
    {"category": "rev2", "message": "Rev.2 Mercury detected..."}
  ]
}
```

### Startup behaviour (Phase 0)

Before any other action, scan `~/.mercury-config/sessions/` for checkpoint
files. For each one found:

- Display: `"Incomplete session for <serial> started at <timestamp>."`
- Display the phase reached and all registered warnings from that session.
- If `config_pushed` is true and the verification fields are incomplete:
  `"WARNING: Config was pushed but not fully verified. Device may have
  unverified configuration."`

**The tool always forces a full re-run from scratch.** No resumption, no
phase skipping. The checkpoint is informational context, not a resume point.

When the new session reaches Phase 2 for the same serial, the old checkpoint
is overwritten. If it's a different device, the old checkpoint persists and
the warning remains visible.

### Cross-device awareness

If the operator plugs in device A but a taint checkpoint exists for device B,
the tool warns: `"Unresolved session for <B serial>. That device may have
unverified config."` This warning is registered in the warning registry and
will replay at the GO gate for device A — the operator should not be flying
*any* device with an unresolved session.

## 3. Hardware Revision Handling

### Known devices (in `devices.json`)

Revision lookup happens in `main.py` after Phase 2 identity, using the
device serial to query `devices.lookup()`. If a record exists with a
`revision` field, `cdc.ask_hardware_revision()` is not called.

```
   Stored revision: Rev.3 (BMP581)
```

### Unknown devices (first encounter)

`cdc.ask_hardware_revision()` is called only when no stored revision exists.
Physical inspection prompt using `ui.prompt_choice()`:

```
   Are GP6 and GP7 available as surface-mount pads or through-holes?
   [1] Surface-mount pads (Rev.2 — BMP390)
   [2] Through-holes (Rev.3 — BMP581)
```

The answer is stored in `devices.json` on adoption.

### Rev.2 warning

If revision is 2 (from stored record or user input):

```python
warnings.register(
    "rev2",
    "Rev.2 Mercury detected. FC6 expects 80 Hz data from Rev.3 (BMP581). "
    "Rev.2 (BMP390) outputs at 50 Hz. If using this device with FC6, you "
    "MUST review config_tunable.h — MERCURY_OUTPUT_RATE_HZ and all "
    "dependent constexprs (MPC rate, FSM timing). Rebuild and reflash "
    "FC6. Verify the config hash FC6 reports on boot matches your build."
)
```

### Cross-check

After Phase 5 config read, if the device's current `sample_speed` doesn't
match what the loaded golden config expects for the stored revision, register:

```python
warnings.register(
    "revision_mismatch",
    "Device sample_speed doesn't match expected value for stored revision "
    "— verify PCB revision label is correct."
)
```

## 4. QNH Hardening

### Launch site selection (Phase 0)

New prompt before QNH pre-fetch:

```
// LAUNCH SITE
   [1] Cox's Field (C6 Aerospace)
   [2] Chippenham (UKROC Regional)
   [3] Farnborough (Internationals)
```

Uses `ui.prompt_choice()`. The selected site determines forecast coordinates
and is displayed on the final confirmation screen.

### Site coordinates in `weather.py`

```python
LAUNCH_SITES: dict[str, dict[str, float | str]] = {
    "Cox's Field": {
        "lat": 51.6695,
        "lon": -1.3680,
        "label": "C6 Aerospace",
    },
    "Chippenham": {
        "lat": 51.4592,
        "lon": -2.1306,
        "label": "UKROC Regional",
    },
    "Farnborough": {
        "lat": 51.2803,
        "lon": -0.7779,
        "label": "Internationals",
    },
}
```

`fetch_qnh()` takes a site name parameter and uses the corresponding
coordinates for the Open-Meteo request.

### QNH prompt changes (Phase 7)

- **No empty input accepted.** The `[Enter = keep {current}]` default is
  removed entirely.
- Prompt becomes: `"QNH / sea-level pressure (hPa): "`
- Current device value and forecast displayed for reference, but neither
  can be selected by shortcut.
- Typing anything non-numeric → `"Enter a numeric QNH value."`
- Range check (950–1070 hPa) stays.
- If the entered value differs from the forecast by >5 hPa:

```python
warnings.register(
    "qnh_delta",
    f"Entered QNH ({qnh:.1f}) differs from {site_name} forecast "
    f"({forecast:.1f}) by {delta:.1f} hPa."
)
```

The old "use forecast?" choice is removed. The operator typed a number;
that's their number.

## 5. Mandatory Second Verification

### Phase 8.5 — Optional browser calibration

```
// CALIBRATION
   Do you need to calibrate in the browser? [y/N]
```

If yes:
- Open browser to `http://192.168.0.1/settings/`
- Wait for Enter when finished
- Register warning:

```python
warnings.register(
    "browser",
    "Browser calibration session was opened — config may have been "
    "modified outside this tool."
)
```

If no: proceed directly to Phase 8.6.

### Phase 8.6 — Second verification pass

**Always runs, unconditionally.** This is the key safety gate.

1. Re-read `/settings/` and `/outputs/` from device.
2. Run `diff_config()` against golden config.
3. Verify QNH matches the value entered in Phase 7.
4. Print the full diff report (same format as Phase 6).

**If any fixed field mismatches:** Hard NO-GO. The device config has drifted
or was modified in the browser. Print the diff, refuse to proceed to the GO
gate. The operator must re-run from scratch.

```python
if fixed_mismatches > 0:
    ui.mercury_no_go(
        "Second verification failed — fixed fields do not match golden "
        "config. Re-run required."
    )
    session_log.log("session", "Final result: NO-GO (second verify failed)")
    return 1
```

**If QNH doesn't match Phase 7 entry:** Same — hard NO-GO. QNH should not
have changed between Phase 8 and Phase 8.6 unless something went wrong in
the browser.

**If only volatile/identity fields differ:** Register a warning (unusual but
not necessarily fatal), proceed to Phase 9.

## 6. Flight Readiness Review (Phase 9)

Replaces the current Phase 9 entirely. Multi-step gate.

### Step 1 — Summary display

```
// FLIGHT READINESS REVIEW

   Serial:    b4:3a:45:99:0b:64
   Revision:  Rev.3 (BMP581)
   SSID:      MercuryAlt_8951
   QNH:       1013.25 hPa
   Site:      Cox's Field (C6 Aerospace)
```

### Step 2 — Warning replay

Each registered warning displayed one at a time. Operator must type `ACCEPT`
(exact, case-sensitive) for each:

```
   [1/3] Browser calibration session was opened — config may have been
         modified outside this tool.
   // Type ACCEPT to acknowledge: ACCEPT

   [2/3] QNH differs from forecast by 7.3 hPa.
   // Type ACCEPT to acknowledge: ACCEPT

   [3/3] Rev.2 Mercury detected. FC6 expects 80 Hz from Rev.3...
   // Type ACCEPT to acknowledge: ACCEPT
```

Any input other than `ACCEPT` → re-display the warning and re-prompt. No
shortcuts.

If zero warnings: `"No warnings to review."` and proceed.

### Step 3 — Airframe designation

```
   // Airframe designation
   Stored: C6A Nimbus - S-IXb
   // Confirm or enter new designation
   C6A Nimbus - S-IXb
```

Format: `C6A <rocket name> - <rocket model>` (e.g., `C6A Nimbus - S-IXb`).
The full string including the `C6A` prefix is typed by the operator.

For known devices with a stored airframe, the stored value is displayed.
The operator either re-enters it (confirming) or types a new one. Empty
input is not accepted — they must type the full designation.

New field in `DeviceRecord`: `"airframe": "C6A Nimbus - S-IXb"`.
The entered value is saved back to `devices.json`.

For unknown devices or first run: prompt with no stored value shown.

### Step 4 — Final GO

```
   // Type GO to confirm flight readiness: GO

   // MERCURY IS GO
   b4:3a:45:99:0b:64  Rev3  FW 2.30
```

Any input other than `GO` → re-prompt.

On successful GO:
- Taint checkpoint deleted
- Session log records full summary: serial, revision, QNH, airframe, launch
  site, warning count acknowledged
- Post-flight instructions print (including "show the geen light to
  UKROCism")
- Phase 10 teardown runs normally

## Files Modified

| File | Change |
|------|--------|
| `warnings.py` | **New.** Warning registry module. |
| `main.py` | Flow restructure: launch site prompt, taint check, Phase 8.5/8.6, new Phase 9 review gate. |
| `config_engine.py` | `prompt_qnh()` removes Enter-to-keep, removes forecast selection. Takes `launch_site` for display. Rev cross-check after Phase 5. |
| `weather.py` | `LAUNCH_SITES` dict, `fetch_qnh()` takes site name parameter. |
| `devices.py` | `DeviceRecord` gets `"airframe"` field. |
| `cdc.py` | `ask_hardware_revision()` uses `ui.prompt_choice()` with GP6/GP7 question. Only called for unknown devices. |
| `checkpoint.py` | **New.** Taint checkpoint persistence — write/read/delete/scan. |
| `ui.py` | New functions: `flight_readiness_summary()`, `warning_replay()`, `prompt_exact()` (requires exact string match). |
| `session_log.py` | No structural changes — warning registry calls it directly. |

## New file: Taint checkpoint persistence

Either a new `checkpoint.py` module or functions added to `session_log.py`.
Handles write/read/delete of `~/.mercury-config/sessions/<serial>.json`.

Recommend a separate `checkpoint.py` — single responsibility, and it needs
to be called from `main.py` at phase boundaries independently of log writes.

## Testing

- `test_warnings.py`: register, get_all, serialise/deserialise round-trip,
  clear resets state.
- `test_checkpoint.py`: write/read/delete lifecycle, stale checkpoint
  detection, cross-device warning.
- `test_config_engine.py`: extend with QNH-no-empty-input tests, revision
  cross-check tests.
- `test_weather.py`: site-specific coordinate selection, fallback on fetch
  failure.

## Out of Scope

- Automatic hardware revision detection via sensor probing (would require
  firmware support).
- `--manage-devices` subcommand for editing device records (edit JSON
  directly for now).
- FC6 firmware changes for Rev.2 compatibility (`MERCURY_OUTPUT_RATE_HZ`
  constexpr consolidation is a separate task on the firmware side).
- Multi-device concurrent sessions.
