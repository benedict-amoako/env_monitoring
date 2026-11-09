#!/usr/bin/env bash
# Show the dashboard full-screen on the Pi's own monitor.
# Started at desktop login by ~/.config/autostart/envmon-kiosk.desktop.
# Exit kiosk mode with Alt+F4 (or Ctrl+W); it won't reopen until the next login.
set -u

# Same port as the envmon service (falls back to the app default, 8000).
PORT=$(systemctl show envmon -p Environment --value 2>/dev/null | tr ' ' '\n' | sed -n 's/^ENVMON_PORT=//p')
URL="${ENVMON_KIOSK_URL:-http://localhost:${PORT:-8000}/}"
URL="${URL%/}/"
# Separate Chromium profile so the kiosk never touches your normal browser session.
PROFILE="$HOME/.cache/envmon-kiosk"

# 1. Wait for a monitor. On a headless boot this just idles until one is plugged in.
until grep -qx connected /sys/class/drm/card*-HDMI-A-*/status 2>/dev/null; do
  sleep 5
done

# 2. Wait up to 2 minutes for the dashboard server to answer.
for _ in $(seq 120); do
  curl -sf -o /dev/null "${URL}api/status" && break
  sleep 1
done

while true; do
  # Power cuts / shutdowns mark the profile as crashed, which would show a
  # "Restore pages?" bar over the dashboard. Mark it clean before each launch.
  sed -i 's/"exit_type":"Crashed"/"exit_type":"Normal"/; s/"exited_cleanly":false/"exited_cleanly":true/' \
    "$PROFILE/Default/Preferences" 2>/dev/null

  chromium "$URL" \
    --kiosk \
    --user-data-dir="$PROFILE" \
    --ozone-platform=wayland \
    --no-first-run \
    --noerrdialogs \
    --disable-infobars \
    --disable-session-crashed-bubble \
    --disable-features=Translate \
    --password-store=basic \
    --check-for-update-interval=31536000

  # Closed on purpose (Alt+F4) -> stay closed. Crashed -> reopen.
  [ $? -eq 0 ] && break
  sleep 5
done
