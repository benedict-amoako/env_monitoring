#!/usr/bin/env python3
"""Start the environmental monitor: sensor sampling + CSV logging + LAN dashboard."""

import logging
import signal
import socket
import sys

from waitress import serve

from envmon.web import create_app


def lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))   # no packets are sent
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app, monitor = create_app()
    cfg = monitor.cfg
    monitor.start()

    def shutdown(*_):
        logging.info("Shutting down - flushing CSV")
        monitor.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    logging.info("CSV logging is OFF until started from the dashboard; files go to %s", cfg["log_dir"])
    logging.info("Dashboard: http://%s:%d  (also http://%s.local:%d)",
                 lan_ip(), cfg["port"], socket.gethostname(), cfg["port"])
    serve(app, host=cfg["host"], port=cfg["port"], threads=6, _quiet=True)


if __name__ == "__main__":
    main()
