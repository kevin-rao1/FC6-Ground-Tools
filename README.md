# FC6 Ground Tools

Ground support tooling for C6 Aerospace's S-IX rockets. Designed to be idiotproof and auditable, even when used at the launch site under pre-launch stress.
Linux (Arch) only. 

## MC6

CLI tool for configuring [Mercury V1](https://www.altimetercloud.com/) altimeters before flight. 

Mercury's own configuration interface (Altimeter Cloud / the embedded web UI) requires manual interaction per device. With 7 Mercurys, config drift between devices is a real risk. MC6 automates the entire flow: discover, diff against a golden config, patch, verify.

### What it does

- Discovers Mercury on USB, identifies hardware revision and firmware
- Connects to Mercury's WiFi AP, reads current config via its embedded web server
- Diffs device config against a known-good golden config
- Prompts for QNH (sea-level pressure), with auto-fetch from Open-Meteo
- Pushes corrected config and verifies the write-back
- Restores your WiFi connection on exit
- Logs every action for post-flight audit

### Install (Arch)

From the AUR:

```
paru -S python-c6-mc6-git
```

Or from source:

```
cd mc6
pip install -e .
```

### Usage

```
# full auto (uses nmcli for WiFi)
mc6

# connect to Mercury WiFi yourself
mc6 --manual-wifi
```

### Dependencies

- Python >= 3.10
- `pyserial`, `requests`
- NetworkManager (`nmcli`) — unless using `--manual-wifi`

### Hardware

- Mercury V1 Altimeter - Rev2 (BMP390) or Rev3 (BMP581)
- Firmware >= 2.30
- USB-C data cable (charge-only cables won't work)

### License

GPL-3.0-or-later
