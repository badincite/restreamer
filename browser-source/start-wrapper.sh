#!/bin/sh
set -eu

if [ -z "${BROWSER_RTMP_URL:-}" ]; then
    echo "Streaming disabled; browser control is available on port 5800"
    exec tail -f /dev/null
fi

# The supervisor supplies DISPLAY, XAUTHORITY, and PULSE_SERVER. Wait for
# those services to respond, then capture the monitor of Chromium's sink.
attempt=0
sink=""
while [ "$attempt" -lt 60 ]; do
    if xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
        sink=$(pactl get-default-sink 2>/dev/null || true)
        [ -n "$sink" ] && break
    fi
    attempt=$((attempt + 1))
    sleep 1
done
if [ -z "$sink" ]; then
    echo "Display or PulseAudio is not ready; supervisor will retry" >&2
    exit 1
fi
export BROWSER_AUDIO_SOURCE="${BROWSER_AUDIO_SOURCE:-${sink}.monitor}"
sleep "${BROWSER_WARMUP_SECONDS:-15}"
echo "Starting browser screen publisher"
echo "$$" > /tmp/browser-publisher.pid
exec python3 /usr/local/bin/browser-publisher.py
