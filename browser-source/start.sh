#!/bin/sh
set -eu

BROWSER_URL=${BROWSER_URL:-https://seasons4u.com/}
BROWSER_WIDTH=${BROWSER_WIDTH:-1280}
BROWSER_HEIGHT=${BROWSER_HEIGHT:-720}
BROWSER_FPS=${BROWSER_FPS:-25}
BROWSER_PROFILE_DIR=${BROWSER_PROFILE_DIR:-/home/browser/profile}

mkdir -p "$XDG_RUNTIME_DIR" "$BROWSER_PROFILE_DIR"
chmod 700 "$XDG_RUNTIME_DIR"

Xvfb "$DISPLAY" -screen 0 "${BROWSER_WIDTH}x${BROWSER_HEIGHT}x24" -nolisten tcp &
xvfb_pid=$!

ready=0
attempt=0
while [ "$attempt" -lt 30 ]; do
    if xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
        ready=1
        break
    fi
    attempt=$((attempt + 1))
    sleep 1
done
if [ "$ready" -ne 1 ]; then
    echo "Xvfb did not start" >&2
    exit 1
fi

pulseaudio --start --exit-idle-time=-1
pactl load-module module-null-sink sink_name=browser_sink >/dev/null
pactl set-default-sink browser_sink

openbox >/dev/null 2>&1 &
x11vnc -display "$DISPLAY" -localhost -rfbport 5900 -nopw -forever -shared -quiet &
websockify --web=/usr/share/novnc 0.0.0.0:6080 localhost:5900 &

chromium \
    --no-sandbox \
    --disable-dev-shm-usage \
    --disable-background-networking \
    --password-store=basic \
    --autoplay-policy=no-user-gesture-required \
    --user-data-dir="$BROWSER_PROFILE_DIR" \
    --window-size="${BROWSER_WIDTH},${BROWSER_HEIGHT}" \
    --kiosk "$BROWSER_URL" &
browser_pid=$!

echo "Browser control ready on port 6080"

if [ -z "${BROWSER_RTMP_URL:-}" ]; then
    echo "BROWSER_RTMP_URL is empty; browser control is available, but streaming is disabled"
    wait "$browser_pid"
    exit $?
fi

video_size="${BROWSER_WIDTH}x${BROWSER_HEIGHT}"
while kill -0 "$browser_pid" 2>/dev/null; do
    ffmpeg -hide_banner -loglevel warning \
        -f x11grab -video_size "$video_size" -framerate "$BROWSER_FPS" -i "${DISPLAY}.0" \
        -f pulse -i browser_sink.monitor \
        -c:v libx264 -preset veryfast -tune zerolatency -pix_fmt yuv420p \
        -g 50 -b:v 3500k \
        -c:a aac -ar 48000 -b:a 128k \
        -f flv "$BROWSER_RTMP_URL" || true
    sleep 3
done

wait "$browser_pid" || true
kill "$xvfb_pid" 2>/dev/null || true
