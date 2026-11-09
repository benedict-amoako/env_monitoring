"""BME688 driver wrapper (plus an explicit, opt-in simulator for development)."""

from __future__ import annotations

import logging
import math
import random
import time
from dataclasses import dataclass, asdict

log = logging.getLogger(__name__)


@dataclass
class Reading:
    """One raw sample straight from the sensor (compensated by the Bosch formulas)."""

    timestamp: float            # unix epoch seconds
    temperature_c: float
    humidity_pct: float
    pressure_hpa: float
    gas_resistance_ohm: float | None   # None until the hotplate reading is valid
    gas_valid: bool
    heat_stable: bool

    def as_dict(self) -> dict:
        return asdict(self)


class SensorError(RuntimeError):
    pass


class BME688Sensor:
    """Forced-mode reader for a Bosch BME688 on I2C using Pimoroni's `bme680` library.

    Two library quirks are handled here:
    * The library polls for new data for only 10 x 10 ms after triggering a
      measurement, which is shorter than T/P/H conversion + the 150 ms heater
      phase, so each call would return the *previous* measurement. We widen the
      poll period so every read is fresh (~0.2 s per read).
    * `bme680.ENABLE_GAS_MEAS` is the BME680 value; the BME688 ("high" variant)
      needs `set_gas_status(-1)` so the library picks the right register bit.
    """

    name = "BME688"

    def __init__(self, address: int = 0x76, bus: int = 1,
                 heater_temp_c: int = 320, heater_ms: int = 150):
        try:
            import bme680
            from bme680 import constants
            from smbus2 import SMBus
        except ImportError as exc:  # pragma: no cover
            raise SensorError(f"Missing dependency: {exc}") from exc

        constants.POLL_PERIOD_MS = 40  # 10 polls x 40 ms = 400 ms window

        last_exc: Exception | None = None
        for addr in (address, 0x77 if address == 0x76 else 0x76):
            try:
                self._dev = bme680.BME680(addr, i2c_device=SMBus(bus))
                self.address = addr
                break
            except (OSError, RuntimeError) as exc:
                last_exc = exc
        else:
            raise SensorError(f"No BME68x found on I2C bus {bus} at 0x76/0x77: {last_exc}")

        variant = getattr(self._dev, "_variant", 0)
        self.name = "BME688" if variant == constants.VARIANT_HIGH else "BME680"

        d = self._dev
        d.set_humidity_oversample(bme680.OS_2X)
        d.set_pressure_oversample(bme680.OS_4X)
        d.set_temperature_oversample(bme680.OS_8X)
        d.set_filter(bme680.FILTER_SIZE_3)
        d.set_gas_status(-1)                    # variant-correct "enable gas"
        d.set_gas_heater_temperature(heater_temp_c)
        d.set_gas_heater_duration(heater_ms)
        d.select_gas_heater_profile(0)
        log.info("%s initialised at 0x%02x on i2c-%d (heater %d C / %d ms)",
                 self.name, self.address, bus, heater_temp_c, heater_ms)

    def read(self) -> Reading:
        try:
            ok = self._dev.get_sensor_data()
        except OSError as exc:
            raise SensorError(f"I2C read failed: {exc}") from exc
        if not ok:
            raise SensorError("Sensor did not report new data in time")

        data = self._dev.data
        gas_valid = bool(data.status & 0x20)      # GASM_VALID
        heat_stable = bool(data.heat_stable)
        gas = float(data.gas_resistance) if (gas_valid and heat_stable) else None
        return Reading(
            timestamp=time.time(),
            temperature_c=round(float(data.temperature), 2),
            humidity_pct=round(float(data.humidity), 3),
            pressure_hpa=round(float(data.pressure), 2),
            gas_resistance_ohm=round(gas, 1) if gas is not None else None,
            gas_valid=gas_valid,
            heat_stable=heat_stable,
        )


class SimulatedSensor:
    """Synthetic data for UI development. Only used when ENVMON_SIMULATE=1."""

    name = "Simulated"
    address = None

    def __init__(self):
        self._t0 = time.time()
        self._gas = 20_000.0

    def read(self) -> Reading:
        t = time.time() - self._t0
        day = math.sin(t / 900)
        self._gas = min(250_000.0, self._gas * 1.002 + random.gauss(0, 300))
        return Reading(
            timestamp=time.time(),
            temperature_c=round(22 + 2 * day + random.gauss(0, 0.03), 2),
            humidity_pct=round(45 - 5 * day + random.gauss(0, 0.1), 3),
            pressure_hpa=round(1006 + 0.8 * math.sin(t / 3000) + random.gauss(0, 0.02), 2),
            gas_resistance_ohm=round(self._gas, 1),
            gas_valid=True,
            heat_stable=True,
        )
