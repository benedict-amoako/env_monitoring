"""Flask app: serves the dashboard and a small JSON API."""

from __future__ import annotations

from flask import Flask, abort, jsonify, request, send_from_directory

from . import config
from .monitor import Monitor

RANGES = {"15m": 900, "1h": 3600, "6h": 6 * 3600, "24h": 24 * 3600}


def create_app(cfg: dict | None = None) -> tuple[Flask, Monitor]:
    cfg = cfg or config.load()
    monitor = Monitor(cfg)
    app = Flask(__name__, static_folder=str(config.BASE_DIR / "static"), static_url_path="/static")

    @app.get("/")
    def index():
        return send_from_directory(app.static_folder, "index.html")

    @app.get("/api/status")
    def status():
        return jsonify(monitor.status())

    @app.get("/api/history")
    def history():
        rng = request.args.get("range", "1h")
        if rng not in RANGES:
            abort(400, f"range must be one of {', '.join(RANGES)}")
        return jsonify(monitor.history_window(RANGES[rng]))

    @app.post("/api/logging/start")
    def logging_start():
        monitor.logger.start()
        return jsonify(monitor.logger.state())

    @app.post("/api/logging/stop")
    def logging_stop():
        monitor.logger.stop()
        return jsonify(monitor.logger.state())

    @app.get("/api/logs")
    def logs():
        return jsonify(monitor.logger.list_files())

    @app.get("/logs/<path:name>")
    def download_log(name: str):
        if name not in {f["name"] for f in monitor.logger.list_files()}:
            abort(404)
        return send_from_directory(monitor.logger.log_dir, name, as_attachment=True,
                                   mimetype="text/csv", max_age=0)

    @app.delete("/api/logs/<path:name>")
    def delete_log(name: str):
        # Only names from the listing are accepted, so no path tricks are possible.
        if name not in {f["name"] for f in monitor.logger.list_files()}:
            abort(404)
        state = monitor.logger.state()
        if state["active"] and name == state["file"]:
            return jsonify(error="This file is being recorded. Stop logging first."), 409
        (monitor.logger.log_dir / name).unlink(missing_ok=True)
        return jsonify(deleted=name)

    @app.after_request
    def no_cache_api(resp):
        if request.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    return app, monitor
