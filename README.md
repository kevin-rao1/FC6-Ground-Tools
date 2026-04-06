# FC6 Ground Tools

Ground support tooling for FC6 rocketry avionics. Linux (Arch) only.

## mercury-config

CLI tool for configuring [Mercury V1](https://www.altimetercloud.com/) altimeters before flight. Designed to be used at the launch site under pre-launch stress.

**What it does:**
- Discovers Mercury on USB, identifies hardware revision and firmware
- Connects to Mercury's WiFi AP, reads current config via its embedded web server
- Diffs device config against a known-good golden config
- Prompts for QNH (sea-level pressure), with auto-fetch from Open-Meteo
- Pushes corrected config and verifies the write-back
- Restores your WiFi connection on exit
- Logs every action for post-flight audit

**What it doesn't do:**
- Downlink. UKROC rules forbid it.

### Install (Arch)

From the AUR:

```
paru -S python-c6-mercuryconfig-git
```

Or from source:

```
cd mercury-config
pip install -e .
```

### Usage

```
mercury-config              # full auto (uses nmcli for WiFi)
mercury-config --manual-wifi  # connect to Mercury WiFi yourself
```

### Dependencies

- Python >= 3.10
- `pyserial`, `requests`
- NetworkManager (`nmcli`) — unless using `--manual-wifi`

### Hardware

- Mercury V1 Rev2 (BMP390) or Rev3 (BMP581)
- Firmware >= 2.30
- USB-C data cable (charge-only cables won't work)

## License

GPL-3.0-or-later
