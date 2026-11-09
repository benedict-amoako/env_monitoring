"""Background sampler: reads the sensor at 1 Hz, logs to CSV on demand, keeps 24 h in memory."""

from __future__ import annotations

import csv
import io
import itertools
import logging
import threading
import time
from collections import deque

from .csvlog import SessionCsvLogger
from .derived import AirQualityEstimator, aq_status, dew_point_c
from .sensor import BME688Sensor, SensorError, SimulatedSensor

log = logging.getLogger(__name__)

HISTORY_SECONDS = 24 * 3600
# history row: (epoch, temp, hum, pressure, gas, aq)
FIELDS = ("temperature_c", "humidity_pct", "pressure_hpa", "gas_resistance_ohm", "air_quality")


class Monitor:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.interval = float(cfg["interval_s"])
        self.logger = SessionCsvLogger(cfg["log_dir"],
                                       prefix="simulated" if cfg["simulate"] else "bme688",
                                       max_bytes=cfg["log_max_bytes"])
        self.aq = AirQualityEstimator(burn_in_s=cfg["aq_burn_in_s"])
        self.history: deque[tuple] = deque(maxlen=int(HISTORY_SECONDS / self.interval) + 60)
        self.lock = threading.Lock()
        self.sensor = None
        self.sensor_error: str | None = None
        self.latest: dict | None = None
        self.started_at = time.time()
        self.read_errors = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="sampler", daemon=True)

    # ---------------------------------------------------------------- lifecycle
    def start(self) -> None:
        self._backfill_from_csv()
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=3)
        self.logger.close()

    def _connect(self) -> None:
        if self.cfg["simulate"]:
            self.sensor = SimulatedSensor()
            return
        try:
            self.sensor = BME688Sensor(
                address=self.cfg["i2c_address"],
                bus=self.cfg["i2c_bus"],
                heater_temp_c=self.cfg["heater_temp_c"],
                heater_ms=self.cfg["heater_ms"],
            )
            self.sensor_error = None
        except SensorError as exc:
            self.sensor = None
            self.sensor_error = str(exc)
            log.error("%s - retrying in 10 s", exc)

    # ---------------------------------------------------------------- sampling
    def _run(self) -> None:
        next_tick = time.monotonic()
        while not self._stop.is_set():
            if self.sensor is None:
                self._connect()
                if self.sensor is None:
                    self._stop.wait(10)
                    next_tick = time.monotonic()
                    continue
            try:
                self._sample()
            except SensorError as exc:
                self.read_errors += 1
                self.sensor_error = str(exc)
                log.warning("Read failed: %s", exc)
                if self.read_errors % 5 == 0:   # repeated failures -> re-init
                    self.sensor = None
            except Exception:
                log.exception("Unexpected error while sampling")

            # Fixed-rate schedule so the CSV stays on a steady 1 s cadence.
            next_tick += self.interval
            delay = next_tick - time.monotonic()
            if delay < 0:
                next_tick = time.monotonic()
                delay = 0
            self._stop.wait(delay)

    def _sample(self) -> None:
        r = self.sensor.read()
        r.temperature_c = round(r.temperature_c + self.cfg["temp_offset_c"], 2)
        self.logger.write(r)

        score = self.aq.update(r.gas_resistance_ohm, r.humidity_pct)
        latest = r.as_dict()
        latest.update(
            dew_point_c=dew_point_c(r.temperature_c, r.humidity_pct),
            air_quality=score,
            air_quality_status=aq_status(score),
            aq_burn_in_remaining_s=self.aq.burn_in_remaining if self.aq.baseline is None else 0,
            aq_baseline_ohm=round(self.aq.baseline) if self.aq.baseline else None,
        )
        with self.lock:
            self.latest = latest
            self.history.append((r.timestamp, r.temperature_c, r.humidity_pct,
                                 r.pressure_hpa, r.gas_resistance_ohm, score))
        self.sensor_error = None

    # ---------------------------------------------------------------- startup
    def _backfill_from_csv(self) -> None:
        """Reload the last 24 h of readings from recent CSV files so charts survive a restart.

        Only the tail of each file is read (24 h at 1 Hz is ~10 MB), so 1 GB logs
        don't slow down startup.
        """
        cutoff = time.time() - HISTORY_SECONDS
        tail_bytes = 32 * 1024 * 1024
        rows = []
        for path in self.logger.log_dir.glob("*.csv"):
            try:
                if path.stat().st_mtime < cutoff:
                    continue
                with open(path, "rb") as fh:
                    header = fh.readline().decode("ascii", "replace").strip().split(",")
                    body_start = fh.tell()
                    size = fh.seek(0, 2)
                    if size - tail_bytes > body_start:
                        fh.seek(size - tail_bytes)
                        fh.readline()                      # skip partial line
                    else:
                        fh.seek(body_start)
                    text = fh.read().decode("ascii", "replace")
            except OSError as exc:
                log.warning("Could not read %s: %s", path, exc)
                continue
            if "epoch" not in header:
                continue
            for row in csv.DictReader(io.StringIO(text), fieldnames=header):
                try:
                    ts = float(row["epoch"])
                    if ts < cutoff:
                        continue
                    gas = row.get("gas_resistance_ohm") or None
                    rows.append((ts, float(row["temperature_c"]), float(row["humidity_pct"]),
                                 float(row["pressure_hpa"]), float(gas) if gas else None, None))
                except (KeyError, ValueError, TypeError):
                    continue                                # header row / partial line
        rows.sort(key=lambda x: x[0])
        with self.lock:
            self.history.extend(rows)
        if rows:
            log.info("Back-filled %d readings from CSV", len(rows))

    # ---------------------------------------------------------------- queries
    def status(self) -> dict:
        with self.lock:
            latest = dict(self.latest) if self.latest else None
        return {
            "sensor": getattr(self.sensor, "name", None),
            "address": f"0x{self.sensor.address:02x}" if getattr(self.sensor, "address", None) else None,
            "simulated": bool(self.cfg["simulate"]),
            "online": self.sensor is not None and self.sensor_error is None and latest is not None,
            "error": self.sensor_error,
            "uptime_s": int(time.time() - self.started_at),
            "logging": self.logger.state(),
            "interval_s": self.interval,
            "latest": latest,
            "server_time": time.time(),
        }

    def history_window(self, seconds: int, max_points: int = 900) -> dict:
        """Average raw history into <= max_points buckets for the requested window."""
        now = time.time()
        start = now - seconds
        bucket = max(self.interval, seconds / max_points)
        with self.lock:
            snap = list(itertools.takewhile(lambda row: row[0] >= start, reversed(self.history)))
        snap.reverse()

        out = {"t": []} | {f: [] for f in FIELDS}
        stats = {f: {"min": None, "max": None} for f in FIELDS}
        cur_key, acc = None, None

        def flush():
            out["t"].append(round(cur_key * bucket + bucket / 2, 1))
            for i, f in enumerate(FIELDS):
                vals = acc[i]
                out[f].append(round(sum(vals) / len(vals), 3) if vals else None)

        for row in snap:
            key = int(row[0] // bucket)
            if key != cur_key:
                if acc is not None:
                    flush()
                cur_key, acc = key, [[] for _ in FIELDS]
            for i, v in enumerate(row[1:]):
                if v is not None:
                    acc[i].append(v)
                    s = stats[FIELDS[i]]
                    s["min"] = v if s["min"] is None else min(s["min"], v)
                    s["max"] = v if s["max"] is None else max(s["max"], v)
        if acc is not None:
            flush()

        return {"start": start, "end": now, "bucket_s": bucket, "series": out, "stats": stats}
