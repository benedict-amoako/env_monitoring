"""DS18B20 1-wire temperature probe via the kernel w1-gpio / w1-therm drivers.

Enable the bus in /boot/firmware/config.txt (GPIO17 here):
    dtoverlay=w1-gpio,gpiopin=17
and reboot. Probes then appear as /sys/bus/w1/devices/28-xxxxxxxxxxxx.

A 12-bit conversion takes ~750 ms, which would eat most of the 1 s sampling
budget, so the probe is read continuously in its own thread and the sampler
just picks up the most recent value.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

W1_DEVICES = Path("/sys/bus/w1/devices")
# 1-wire family codes of DS18B20-compatible thermometers handled by w1-therm.
THERM_FAMILIES = ("28-", "10-", "22-", "3b-", "42-")
POWER_ON_RESET_MC = 85000      # 85.000 C: the value a probe returns if a conversion was lost


def find_probes() -> list[str]:
    """Return ids of all thermometer probes on any 1-wire bus (ghost '00-' devices are ignored)."""
    if not W1_DEVICES.exists():
        return []
    return sorted(p.name for p in W1_DEVICES.iterdir() if p.name.startswith(THERM_FAMILIES))


def read_probe(probe_id: str) -> float:
    """One blocking read (~750 ms). Raises OSError/ValueError on a bad read."""
    text = (W1_DEVICES / probe_id / "w1_slave").read_text()
    lines = text.strip().splitlines()
    if len(lines) < 2 or not lines[0].endswith("YES"):
        raise ValueError(f"CRC check failed: {text.strip()!r}")
    milli_c = int(lines[1].rsplit("t=", 1)[1])
    if milli_c == POWER_ON_RESET_MC:
        raise ValueError("probe returned the 85 C power-on value")
    return milli_c / 1000.0


class DS18B20Poller:
    """Background reader that always holds the latest good probe temperature."""

    def __init__(self, probe_id: str | None = None, max_age_s: float = 5.0):
        self.wanted_id = probe_id
        self.probe_id: str | None = None
        self.max_age_s = max_age_s
        self.error: str | None = None
        self._value: float | None = None
        self._value_ts = 0.0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="ds18b20", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)

    def _discover(self) -> None:
        probes = find_probes()
        if self.wanted_id:
            self.probe_id = self.wanted_id if self.wanted_id in probes else None
        else:
            self.probe_id = probes[0] if probes else None
        if self.probe_id:
            log.info("DS18B20 probe %s found%s", self.probe_id,
                     f" (also present: {', '.join(p for p in probes if p != self.probe_id)})"
                     if len(probes) > 1 else "")
            self.error = None
        else:
            self.error = (f"DS18B20 {self.wanted_id} not found" if self.wanted_id
                          else "No DS18B20 probe found on the 1-wire bus")

    def _run(self) -> None:
        failures = 0
        while not self._stop.is_set():
            if self.probe_id is None:
                self._discover()
                if self.probe_id is None:
                    self._stop.wait(10)
                    continue
            try:
                value = read_probe(self.probe_id)
                with self._lock:
                    self._value, self._value_ts = round(value, 3), time.monotonic()
                self.error = None
                failures = 0
            except (OSError, ValueError, IndexError) as exc:
                failures += 1
                self.error = f"DS18B20 read failed: {exc}"
                if failures == 1 or failures % 30 == 0:
                    log.warning(self.error)
                if failures >= 5:            # probe unplugged? look for it again
                    self.probe_id = None
                self._stop.wait(1)

    def latest(self) -> float | None:
        """Most recent reading, or None if it's older than max_age_s (probe missing/failing)."""
        with self._lock:
            if self._value is None or time.monotonic() - self._value_ts > self.max_age_s:
                return None
            return self._value

    def status(self) -> dict:
        return {"id": self.probe_id, "online": self.latest() is not None, "error": self.error}
