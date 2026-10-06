#!/bin/sh
set -eu
test -S /tmp/vnc.sock
xdpyinfo -display "${DISPLAY:-:0}" >/dev/null 2>&1
