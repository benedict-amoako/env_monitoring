"""Session-based CSV logging of every raw reading.

* Logging is off until start() is called (the dashboard's Start button).
* Every start() opens a brand-new file: data/logs/bme688_YYYY-MM-DD_HH-MM-SS.csv
* When a file would grow past max_bytes (default 1 GiB) the session continues in
  ..._part002.csv, ..._part003.csv, ... each with its own header row.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime
from pathlib import Path

from .sensor import Reading

HPA_TO_PSI = 0.0145037738

FIELDS = [
    "timestamp_iso",
    "epoch",
    "temperature_c",
    "humidity_pct",
    "pressure_hpa",
    "pressure_psi",
    "gas_resistance_ohm",
    "gas_valid",
    "heat_stable",
    "probe_temp_c",
]
HEADER = ",".join(FIELDS) + "\n"


class SessionCsvLogger:
    def __init__(self, log_dir: Path, prefix: str = "bme688", max_bytes: int = 1024 ** 3):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.prefix = prefix
        self.max_bytes = max(1024, int(max_bytes))
        self._lock = threading.Lock()
        self._fh = None
        self._session_id: str | None = None
        self.active = False
        self.part = 0
        self.current_path: Path | None = None
        self.file_bytes = 0
        self.session_rows = 0
        self.session_bytes = 0
        self.session_started: float | None = None
        self.session_files: list[str] = []

    # ------------------------------------------------------------ control
    def start(self) -> None:
        with self._lock:
            if self.active:
                return
            sid = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            n = 2
            base = sid
            while any(self.log_dir.glob(f"{self.prefix}_{sid}*.csv")):   # two starts in one second
                sid = f"{base}-{n}"
                n += 1
            self._session_id = sid
            self.part = 0
            self.session_rows = 0
            self.session_bytes = 0
            self.session_files = []
            self.session_started = time.time()
            self._open_next_part()
            self.active = True

    def stop(self) -> None:
        with self._lock:
            self._close()
            self.active = False

    close = stop

    # ------------------------------------------------------------ writing
    def _open_next_part(self) -> None:
        self._close()
        self.part += 1
        suffix = "" if self.part == 1 else f"_part{self.part:03d}"
        path = self.log_dir / f"{self.prefix}_{self._session_id}{suffix}.csv"
        self._fh = open(path, "w", encoding="ascii", newline="")
        self._fh.write(HEADER)
        self._fh.flush()
        self.file_bytes = len(HEADER)
        self.session_bytes += len(HEADER)
        self.current_path = path
        self.session_files.append(path.name)

    def _close(self) -> None:
        if self._fh:
            self._fh.flush()
            os.fsync(self._fh.fileno())
            self._fh.close()
            self._fh = None

    def write(self, r: Reading) -> None:
        if not self.active:
            return
        now = datetime.fromtimestamp(r.timestamp).astimezone()
        line = ",".join([
            now.isoformat(timespec="milliseconds"),
            f"{r.timestamp:.3f}",
            f"{r.temperature_c}",
            f"{r.humidity_pct}",
            f"{r.pressure_hpa}",
            f"{r.pressure_hpa * HPA_TO_PSI:.4f}",
            "" if r.gas_resistance_ohm is None else f"{r.gas_resistance_ohm}",
            str(int(r.gas_valid)),
            str(int(r.heat_stable)),
            "" if r.probe_temp_c is None else f"{r.probe_temp_c}",
        ]) + "\n"
        with self._lock:
            if not self.active or self._fh is None:
                return
            if self.file_bytes + len(line) > self.max_bytes:
                self._open_next_part()
            self._fh.write(line)
            self._fh.flush()
            self.file_bytes += len(line)
            self.session_bytes += len(line)
            self.session_rows += 1
            if self.session_rows % 60 == 0:      # force to SD card once a minute
                os.fsync(self._fh.fileno())

    # ------------------------------------------------------------ info
    def state(self) -> dict:
        with self._lock:
            return {
                "active": self.active,
                "file": self.current_path.name if self.current_path else None,
                "part": self.part,
                "file_bytes": self.file_bytes,
                "max_bytes": self.max_bytes,
                "rows": self.session_rows,
                "session_bytes": self.session_bytes,
                "started_at": self.session_started,
                "files": list(self.session_files),
            }

    def list_files(self) -> list[dict]:
        files = sorted(self.log_dir.glob("*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
        return [{"name": p.name, "bytes": p.stat().st_size, "modified": p.stat().st_mtime} for p in files]
