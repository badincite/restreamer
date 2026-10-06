#!/bin/sh
set -eu
curl --fail --silent --max-time 2 http://127.0.0.1:5800/ >/dev/null
if [ -n "${BROWSER_RTMP_URL:-}" ]; then
    test -r /tmp/browser-publisher.pid
    kill -0 "$(cat /tmp/browser-publisher.pid)"
fi
