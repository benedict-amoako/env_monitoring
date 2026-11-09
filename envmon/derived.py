"""Values derived from raw readings: dew point and an air-quality *estimate*.

Bosch's official IAQ index needs the closed-source BSEC library. This module
uses the well-known open approach instead: compare gas resistance against a
clean-air baseline (higher resistance = fewer VOCs) and blend in how far
humidity is from a comfortable 40 %. The result is a 0-100 score where higher
is better. Treat it as a relative indicator for *this* room, not a calibrated
measurement.
"""

from __future__ import annotations

import math
import time
from collections import deque

HUM_BASELINE = 40.0     # % RH considered ideal
HUM_WEIGHT = 0.25       # humidity contributes 25 points, gas 75


def dew_point_c(temp_c: float, rh: float) -> float | None:
    """Magnus-formula dew point."""
    if rh <= 0:
        return None
    a, b = 17.62, 243.12
    gamma = math.log(rh / 100.0) + a * temp_c / (b + temp_c)
    return round(b * gamma / (a - gamma), 2)


def aq_status(score: float | None) -> str | None:
    if score is None:
        return None
    if score >= 80:
        return "good"
    if score >= 60:
        return "moderate"
    if score >= 40:
        return "poor"
    return "bad"


class AirQualityEstimator:
    """Tracks a self-adjusting clean-air gas baseline and scores each reading."""

    def __init__(self, burn_in_s: int = 300):
        self.burn_in_s = burn_in_s
        self._started = time.monotonic()
        self._burn_samples: deque[float] = deque(maxlen=60)
        self.baseline: float | None = None

    @property
    def burn_in_remaining(self) -> int:
        return max(0, int(self.burn_in_s - (time.monotonic() - self._started)))

    def update(self, gas: float | None, humidity: float) -> float | None:
        if gas is None or gas <= 0:
            return None

        if self.baseline is None:
            self._burn_samples.append(gas)
            if self.burn_in_remaining > 0:
                return None
            self.baseline = sum(self._burn_samples) / len(self._burn_samples)

        # Cleaner air than the baseline -> move the baseline up fairly quickly.
        # Otherwise let it drift down very slowly (~6 h time constant) so that
        # long-term sensor drift doesn't leave the score stuck low forever.
        if gas > self.baseline:
            self.baseline += 0.05 * (gas - self.baseline)
        else:
            self.baseline += (gas - self.baseline) / (6 * 3600)

        gas_score = min(1.0, gas / self.baseline) * (100 * (1 - HUM_WEIGHT))

        off = humidity - HUM_BASELINE
        if off > 0:
            hum_score = (100 - HUM_BASELINE - off) / (100 - HUM_BASELINE)
        else:
            hum_score = (HUM_BASELINE + off) / HUM_BASELINE
        hum_score = max(0.0, hum_score) * (100 * HUM_WEIGHT)

        return round(gas_score + hum_score, 1)
