#!/bin/sh
set -eu

BROWSER_URL=${CHROMIUM_APP_URL:-${BROWSER_URL:-about:blank}}
BROWSER_PROFILE_DIR=${BROWSER_PROFILE_DIR:-/config/google-chrome}
DISPLAY_WIDTH=${DISPLAY_WIDTH:-${BROWSER_WIDTH:-1920}}
DISPLAY_HEIGHT=${DISPLAY_HEIGHT:-${BROWSER_HEIGHT:-1080}}
WIDEVINE_DIR=/opt/google/chrome/WidevineCdm

if [ ! -f "$WIDEVINE_DIR/manifest.json" ] || \
   [ ! -f "$WIDEVINE_DIR/_platform_specific/linux_x64/libwidevinecdm.so" ]; then
    echo "Google Chrome Widevine CDM is missing" >&2
    exit 1
fi

mkdir -p "$BROWSER_PROFILE_DIR"

# Only managed workers opt in. Their manager guarantees one stopped/starting
# container owns this dedicated volume; retain locks rather than wiping profiles.
if [ "${BROWSER_CLEAR_STALE_LOCKS:-off}" = "on" ]; then
    stale="$BROWSER_PROFILE_DIR/stale-locks-$(date +%s)"
    for lock in SingletonLock SingletonSocket SingletonCookie; do
        if [ -e "$BROWSER_PROFILE_DIR/$lock" ] || [ -L "$BROWSER_PROFILE_DIR/$lock" ]; then
            mkdir -p "$stale"
            mv "$BROWSER_PROFILE_DIR/$lock" "$stale/$lock"
        fi
    done
fi

set -- \
    --no-sandbox \
    --disable-dev-shm-usage \
    --no-first-run \
    --no-default-browser-check \
    --password-store=basic \
    --autoplay-policy=no-user-gesture-required \
    --remote-debugging-address=127.0.0.1 \
    --remote-debugging-port="${BROWSER_DEBUG_PORT:-9222}" \
    --user-data-dir="$BROWSER_PROFILE_DIR" \
    --window-size="${DISPLAY_WIDTH},${DISPLAY_HEIGHT}" \
    --widevine-cdm-path="$WIDEVINE_DIR"

if [ "${BROWSER_HARDWARE_DECODE:-off}" = "on" ]; then
    set -- "$@" \
        --enable-features=AcceleratedVideoDecodeLinuxGL,VaapiOnNvidiaGPUs,VaapiIgnoreDriverChecks \
        --ignore-gpu-blocklist \
        --use-gl=angle --use-angle=gl
elif [ "${BROWSER_GPU_RENDER:-off}" = "on" ]; then
    # GPU compositing is useful even when legacy NVIDIA VA-API decoding stalls.
    set -- "$@" \
        --ignore-gpu-blocklist \
        --use-gl=angle --use-angle=gl \
        --disable-accelerated-video-decode
fi

if [ -n "${NVIDIA_RENDER_NODE:-}" ]; then
    set -- "$@" --render-node-override="$NVIDIA_RENDER_NODE"
fi

# The stack mounts administrator supplied unpacked extensions here.
extension_paths=""
for extension in "${BROWSER_EXTENSIONS_DIR:-/home/browser/extensions}"/*; do
    [ -f "$extension/manifest.json" ] || continue
    name=${extension##*/}
    case "$name" in *[!A-Za-z0-9_-]*) continue ;; esac
    extension_paths="${extension_paths:+$extension_paths,}$extension"
done
if [ -n "$extension_paths" ]; then
    set -- "$@" "--load-extension=$extension_paths"
fi

exec google-chrome-stable "$@" --kiosk "$BROWSER_URL"
