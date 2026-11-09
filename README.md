# Environmental Monitor (Raspberry Pi 5 + BME688)

LAN web dashboard for a Bosch BME688 on I2C. It samples once per second and, when logging
is switched on, writes every raw reading to CSV. It shows live tiles and history charts (15 min, 1 h, 6 h, 24 h) for
temperature, humidity, pressure (psi), gas resistance, and an estimated air-quality score.
It has a light/dark theme toggle and starts and stops CSV logging on demand.

## Wiring

| BME688 | Pi 5 header |
|--------|-------------|
| VIN / VCC | Pin 1 (3.3 V) |
| GND | Pin 6 |
| SDA | Pin 3 (GPIO 2) |
| SCL | Pin 5 (GPIO 3) |

Check that the sensor is visible: `i2cdetect -y 1` should show `76` (or `77`).

### DS18B20 probe (1-wire on GPIO17)

| DS18B20 | Pi 5 header |
|---------|-------------|
| VDD (red) | Pin 1 or 17 (3.3 V) |
| GND (black) | Pin 9 / any GND |
| DATA (yellow) | Pin 11 (GPIO 17), with a 4.7 kΩ pull-up to 3.3 V |

Enable the bus in `/boot/firmware/config.txt` with `dtoverlay=w1-gpio,gpiopin=17`, then reboot.
The probe appears as `/sys/bus/w1/devices/28-xxxxxxxxxxxx`
(`cat /sys/bus/w1/devices/28-*/temperature` prints millidegrees). The app finds it automatically.
If the probe is missing, the dashboard says so, the CSV column stays blank, and the app keeps
looking for it every 10 s.

## Setup

```bash
sudo raspi-config nonint do_i2c 0        # enable I2C (reboot if /dev/i2c-1 is missing)
cd ~/environmental_monitoring
python3 -m venv .venv                    # already done
.venv/bin/pip install -r requirements.txt
```

## Run

```bash
.venv/bin/python run.py
```

Open `http://<pi-ip>:8000` (or `http://fais-rpi-1.local:8000`) from any device on the LAN.
To use it as a desktop display on the Pi, open the same URL in Chromium
(`chromium --kiosk http://localhost:8000` for full screen).

### Full-screen on the Pi's own monitor (kiosk)

`kiosk.sh` opens the dashboard in full-screen Chromium. It waits for a monitor to be
connected and for the server to answer, and uses its own browser profile. To run it at every desktop login:

```bash
mkdir -p ~/.config/autostart
cp envmon-kiosk.desktop ~/.config/autostart/
```

Requires desktop autologin (`sudo raspi-config` → System → Boot / Auto Login → Desktop Autologin).
Press **Alt+F4** to leave kiosk mode; it reopens at the next login. Delete
`~/.config/autostart/envmon-kiosk.desktop` to turn it off. The port is read from the `envmon`
service, or set `ENVMON_KIOSK_URL`.

### Start automatically on boot

```bash
sudo cp envmon.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now envmon
journalctl -u envmon -f                  # view logs
```

## CSV logs

Logging is **off when the app starts**. Use the **Start logging** / **Stop logging** button
in the dashboard's *Raw data logs* panel (or `POST /api/logging/start` and `/api/logging/stop`).

* Each start opens a **new file**: `data/logs/bme688_YYYY-MM-DD_HH-MM-SS.csv`, one row per second.
* When a file would exceed **1 GB** (`ENVMON_LOG_MAX_MB`, default 1024 MiB), logging continues
  in `..._part002.csv`, `..._part003.csv`, … each with its own header row. No rows are lost.

| column | meaning |
|---|---|
| `timestamp_iso` | local time with UTC offset, ms precision |
| `epoch` | Unix seconds |
| `temperature_c` | °C (after `ENVMON_TEMP_OFFSET`, if set) |
| `humidity_pct` | % RH |
| `pressure_hpa` | hPa (sensor native unit) |
| `pressure_psi` | psi (same reading converted; the dashboard shows psi) |
| `gas_resistance_ohm` | Ω; blank while the hotplate is not yet stable |
| `gas_valid`, `heat_stable` | sensor status flags (1/0) |
| `probe_temp_c` | DS18B20 probe °C; blank if the probe isn't connected |

Lines are flushed every row and fsync'd to the SD card once a minute and on stop.
Files can be downloaded or deleted from the dashboard (Delete asks for a second click to confirm;
the file currently recording can't be deleted until logging is stopped). On restart, the last 24 h of readings is reloaded
from recent CSV files so the charts don't start empty. Anyone who can open the dashboard on
your LAN can start or stop logging.

## Configuration (environment variables)

| variable | default | |
|---|---|---|
| `ENVMON_PORT` | `8000` | HTTP port |
| `ENVMON_HOST` | `0.0.0.0` | bind address (`127.0.0.1` = this Pi only) |
| `ENVMON_I2C_BUS` / `ENVMON_I2C_ADDR` | `1` / `0x76` | falls back to `0x77` automatically |
| `ENVMON_INTERVAL` | `1.0` | seconds between samples |
| `ENVMON_HEATER_TEMP` / `ENVMON_HEATER_MS` | `320` / `150` | gas hotplate profile |
| `ENVMON_TEMP_OFFSET` | `0` | °C correction for self-heating (e.g. `-1.5`) |
| `ENVMON_AQ_BURN_IN` | `300` | seconds before the air-quality score starts |
| `ENVMON_DATA_DIR` | `./data` | where `logs/` is written |
| `ENVMON_LOG_MAX_MB` | `1024` | split a log into a new part file at this size |
| `ENVMON_DS18B20` | `1` | `0` turns off the 1-wire probe |
| `ENVMON_DS18B20_ID` | *(first found)* | pick a probe, e.g. `28-092330496fdb` |
| `ENVMON_SIMULATE` | `0` | `1` = fake data for UI work (logs to `simulated_*.csv`) |

## Notes

* **Self-heating.** The gas heater and the Pi both warm the sensor, so it usually reads
  1–3 °C high. Compare with a reference thermometer, then set `ENVMON_TEMP_OFFSET`.
  Mounting the sensor on a short cable away from the Pi also helps.
* **Air quality score.** This is an open estimate (gas resistance vs. a self-learning
  clean-air baseline, plus distance from 40 % RH), not Bosch's proprietary BSEC IAQ.
  A new sensor's gas resistance climbs for hours to days during burn-in.
* **API.** `/api/status` (latest reading), `/api/history?range=15m|1h|6h|24h`, `/api/logs`,
  `DELETE /api/logs/<file>`.

## Layout

```
run.py              entry point (waitress server + sampler thread)
envmon/sensor.py    BME688 driver wrapper (+ simulator)
envmon/ds18b20.py   DS18B20 1-wire reader (own thread; a 12-bit read takes ~750 ms)
envmon/monitor.py   1 Hz sampler, 24 h in-memory history, CSV backfill
envmon/csvlog.py    session CSV writer (start/stop, 1 GB split)
envmon/derived.py   dew point + air-quality estimate
envmon/web.py       Flask routes
static/             dashboard (HTML/CSS/JS, no external CDN)
```
