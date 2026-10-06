#!/usr/bin/env python3
"""Own one headless NVIDIA Xorg display and its local-only VNC transport."""
import os
from pathlib import Path
import re
import signal
import subprocess
import time
from gpu_detect import selected_bus, require_nvenc

width = int(os.environ.get("DISPLAY_WIDTH", "1920"))
height = int(os.environ.get("DISPLAY_HEIGHT", "1080"))
assert 320 <= width <= 3840 and 180 <= height <= 2160
bus = selected_bus()
require_nvenc()
display = os.environ.get("DISPLAY", ":0")
assert re.fullmatch(r":\d+", display)
config = f'''
Section "ServerFlags"
    Option "AutoAddDevices" "False"
    Option "AutoEnableDevices" "False"
    Option "DontVTSwitch" "True"
EndSection
Section "Device"
    Identifier "BrowserGPU"
    Driver "nvidia"
    BusID "{bus}"
    Option "AllowEmptyInitialConfiguration" "True"
    Option "UseDisplayDevice" "None"
EndSection
Section "Screen"
    Identifier "CaptureScreen"
    Device "BrowserGPU"
    DefaultDepth 24
    SubSection "Display"
        Depth 24
        Virtual {width} {height}
    EndSubSection
EndSection
Section "ServerLayout"
    Identifier "CaptureLayout"
    Screen "CaptureScreen"
EndSection
'''
Path("/tmp/browser-xorg.conf").write_text(config)
children = []
running = True
def stop(_signum, _frame):
    global running
    running = False
signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
try:
    xorg = subprocess.Popen(["Xorg", display, "-config", "/tmp/browser-xorg.conf",
        "-logfile", "/tmp/browser-Xorg.log", "-nolisten", "tcp", "-noreset",
        "-novtswitch", "-sharevts", "-ac"])
    children.append(xorg)
    ready = False
    for _ in range(80):
        if not running or xorg.poll() is not None:
            break
        result = subprocess.run(["xdpyinfo", "-display", display],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
        if result.returncode == 0:
            ready = True
            break
        time.sleep(.1)
    if not ready:
        raise RuntimeError("NVIDIA Xorg failed to become ready; inspect /tmp/browser-Xorg.log")
    # Debian's x0vncserver is a daemonizing Perl wrapper. Supervise the binary.
    children.append(subprocess.Popen(["X0tigervnc", "-display", display,
        "-rfbport=-1", "-rfbunixpath=/tmp/vnc.sock", "-rfbunixmode=0666",
        "-SecurityTypes=None", "-AlwaysShared=1", "-AcceptSetDesktopSize=0"]))
    while running:
        if any(child.poll() is not None for child in children):
            raise RuntimeError("Xorg or VNC transport exited")
        time.sleep(.2)
finally:
    for child in reversed(children):
        if child.poll() is None:
            child.terminate()
    for child in reversed(children):
        try:
            child.wait(timeout=3)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
