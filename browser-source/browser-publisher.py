#!/usr/bin/env python3
"""Publish browser screen capture, with optional direct media detection.

The DevTools socket is loopback-only. Never print discovered URLs: they can
contain short-lived access tokens belonging to the signed-in browser session.
"""

import base64
import hashlib
import json
import os
import re
import select
import signal
import socket
import subprocess
import time
import urllib.request
from urllib.parse import urlsplit
from capture_matcher import CaptureMatcher


RTMP = os.environ["BROWSER_RTMP_URL"]
DISPLAY = os.environ.get("DISPLAY", ":99")
SIZE = f'{os.environ.get("BROWSER_WIDTH", "1280")}x{os.environ.get("BROWSER_HEIGHT", "720")}'
FPS = os.environ.get("BROWSER_FPS", "25")
AUDIO_SOURCE = os.environ.get("BROWSER_AUDIO_SOURCE", "browser_sink.monitor")
MODE = os.environ.get("BROWSER_DIRECT_MODE", "off").lower()
PORT = int(os.environ.get("BROWSER_DEBUG_PORT", "9222"))
SWITCH_COOLDOWN = max(0, int(os.environ.get("BROWSER_SWITCH_COOLDOWN", "8")))
MEDIA_SUFFIX = re.compile(r"\.(?:m3u8|mpd|mp4|m4v)(?:$|[?#])", re.I)
# DASH fragments often arrive as video/mp4; they are not playable streams on
# their own, so only detect MP4 by an actual .mp4/.m4v URL suffix.
MEDIA_MIMES = ("mpegurl", "dash+xml")
# The experimental DRM probe can download unbounded media data to /tmp.
# Keep its code for later redesign, but never run it in this build.
ENABLE_DRM_PROBE = False
RUNNING = True
AUTO_MATCH = os.environ.get("BROWSER_AUTO_MATCH", "off").lower() in ("1", "true", "on")


def stop(_signum, _frame):
    global RUNNING
    RUNNING = False


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)


def websocket(url):
    """Minimal client for Chrome's uncompressed local DevTools WebSocket."""
    parts = urlsplit(url)
    sock = socket.create_connection(("127.0.0.1", PORT), 3)
    sock.settimeout(3)
    key = base64.b64encode(os.urandom(16)).decode()
    request = (f"GET {parts.path or '/'} HTTP/1.1\r\n"
               f"Host: 127.0.0.1:{PORT}\r\nUpgrade: websocket\r\n"
               f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
               "Sec-WebSocket-Version: 13\r\n\r\n")
    sock.sendall(request.encode())
    response = bytearray()
    while b"\r\n\r\n" not in response and len(response) < 8192:
        response.extend(sock.recv(4096))
    expected = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest())
    if not response.startswith(b"HTTP/1.1 101") or expected not in response:
        sock.close()
        raise OSError("DevTools WebSocket handshake failed")
    sock.settimeout(2)
    return sock


def send_ws(sock, message):
    data = json.dumps(message, separators=(",", ":")).encode()
    mask = os.urandom(4)
    length = len(data)
    header = bytearray([0x81])
    if length < 126:
        header.append(0x80 | length)
    elif length < 65536:
        header.extend((0x80 | 126,))
        header.extend(length.to_bytes(2, "big"))
    else:
        header.extend((0x80 | 127,))
        header.extend(length.to_bytes(8, "big"))
    sock.sendall(header + mask + bytes(byte ^ mask[i % 4] for i, byte in enumerate(data)))


def read_exact(sock, count):
    result = bytearray()
    while len(result) < count:
        chunk = sock.recv(count - len(result))
        if not chunk:
            raise OSError("DevTools socket closed")
        result.extend(chunk)
    return bytes(result)


def recv_ws(sock):
    first, second = read_exact(sock, 2)
    length = second & 127
    if length == 126:
        length = int.from_bytes(read_exact(sock, 2), "big")
    elif length == 127:
        length = int.from_bytes(read_exact(sock, 8), "big")
    if length > 2_000_000:
        raise OSError("Oversized DevTools message")
    mask = read_exact(sock, 4) if second & 128 else None
    data = read_exact(sock, length)
    if mask:
        data = bytes(byte ^ mask[i % 4] for i, byte in enumerate(data))
    if first & 15 == 8:
        raise OSError("DevTools socket closed")
    if first & 15 != 1:
        return None
    return json.loads(data)


def targets():
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/list", timeout=2) as response:
        return [target for target in json.load(response)
                if target.get("type") == "page" and target.get("webSocketDebuggerUrl")]


def input_options(candidate):
    options = ["-rw_timeout", "5000000"]
    headers = candidate.get("headers", {})
    agent = headers.get("User-Agent") or headers.get("user-agent")
    referer = headers.get("Referer") or headers.get("referer")
    if agent:
        options += ["-user_agent", agent]
    if referer:
        options += ["-referer", referer]
    return options


def playable(candidate):
    command = ["ffprobe", "-v", "error", *input_options(candidate),
               "-show_entries", "stream=codec_name,codec_type", "-of", "json", candidate["url"]]
    try:
        result = subprocess.run(command, capture_output=True, timeout=15, check=True)
        streams = json.loads(result.stdout).get("streams", [])
        videos = [s for s in streams if s.get("codec_type") == "video"]
        audios = [s for s in streams if s.get("codec_type") == "audio"]
        return bool(videos and videos[0].get("codec_name") == "h264" and
                    (not audios or audios[0].get("codec_name") == "aac"))
    except (OSError, subprocess.SubprocessError, ValueError, KeyError):
        return False


def command_for(mode, candidate=None, settings=None):
    base = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error"]
    if mode == "direct" and candidate:
        return [*base, *input_options(candidate), "-i", candidate["url"],
                "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy",
                "-f", "flv", RTMP]
    settings = settings or {}
    fps = settings.get("fps", FPS)
    encoder = os.environ.get("BROWSER_VIDEO_ENCODER", "libx264")
    if encoder == "libx264":
        encoder_options = ["-c:v", encoder, "-preset", os.environ.get("BROWSER_X264_PRESET", "ultrafast"),
                           "-tune", "zerolatency"]
    elif encoder == "h264_nvenc":
        encoder_options = ["-c:v", encoder, "-preset", "p1", "-tune", "ll",
                           "-rc", "vbr", "-bf", "0", "-rc-lookahead", "0", "-zerolatency", "1"]
    else:
        raise ValueError("Unsupported BROWSER_VIDEO_ENCODER")
    # Both devices timestamp against the system clock. Preserve their different
    # startup times rather than independently moving each input to time zero.
    # Audio delivery can arrive in bursts. Keep enough video queued to survive
    # those short waits; an eight-frame queue dropped frames during live audio.
    return [*base, "-thread_queue_size", os.environ.get("BROWSER_AUDIO_QUEUE_PACKETS", "256"), "-f", "pulse",
            "-fragment_size", "2048", "-i", AUDIO_SOURCE,
            "-thread_queue_size", os.environ.get("BROWSER_VIDEO_QUEUE_FRAMES", "32"), "-f", "x11grab",
            "-video_size", SIZE, "-framerate", fps, "-isync", "0", "-i", f"{DISPLAY}.0",
            "-map", "1:v:0", "-map", "0:a:0",
            *encoder_options,
            "-pix_fmt", "yuv420p", "-g", str(max(1, round(float(fps) * 2))),
            "-b:v", settings.get("bitrate", os.environ.get("BROWSER_VIDEO_BITRATE", "5500k")),
            "-maxrate", settings.get("maxrate", os.environ.get("BROWSER_VIDEO_MAXRATE", "6000k")),
            "-bufsize", settings.get("bufsize", os.environ.get("BROWSER_VIDEO_BUFSIZE", "6000k")),
            "-af", "aresample=async=1", "-c:a", "aac", "-ar", "48000", "-b:a", "128k", "-f", "flv", RTMP]


def launch(mode, candidate=None, settings=None):
    print(f"Publisher mode: {mode}", flush=True)
    # FFmpeg may include signed media URLs in errors. Do not put them in logs.
    return subprocess.Popen(command_for(mode, candidate, settings), stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            env=os.environ.copy(), start_new_session=True)


def terminate(process):
    if process and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def next_candidate(candidates, active_url, blocked, now):
    """Prefer a recent manifest for a different stream, ignoring refreshes."""
    while candidates:
        candidate = candidates.pop()
        url = candidate.get("url", "")
        if (not url.startswith(("https://", "http://")) or
                url == active_url or blocked.get(url, 0) > now or
                now - candidate.get("seen", now) > 30):
            continue
        if not candidate.get("headers"):
            # currentSrc can be observed after Network.requestWillBeSent;
            # retain that request's User-Agent/Referer for the probe.
            candidate = next((item for item in reversed(candidates)
                              if item.get("url") == url and item.get("headers")), candidate)
        return candidate
    return None


def main():
    matcher = CaptureMatcher(targets, websocket, send_ws, recv_ws,
                             minimum=int(os.environ.get("BROWSER_AUTO_MIN_KBPS", "1000")),
                             maximum=int(os.environ.get("BROWSER_AUTO_MAX_KBPS", "12000")),
                             max_fps=int(os.environ.get("BROWSER_AUTO_MAX_FPS", "60"))) if AUTO_MATCH else None
    settings = None
    process = launch("capture")
    mode = "capture"
    active_url = None
    blocked = {}
    sockets = {}
    requests = {}
    candidates = []
    last_discovery = 0
    last_eval = 0
    switch_after = 0
    try:
        while RUNNING:
            now = time.monotonic()
            if process.poll() is not None:
                if mode == "direct" and active_url:
                    blocked[active_url] = now + 120
                    print("Direct input stopped; returning to browser capture", flush=True)
                mode, active_url = "capture", None
                switch_after = 0
                time.sleep(2)
                if not RUNNING:
                    break
                process = launch("capture", settings=settings) if settings else launch("capture")
            if matcher:
                proposal = matcher.tick(now)
                if proposal:
                    settings = proposal
                    print(f"Matching screen publisher: {settings['fps']} fps, {settings.get('bitrate', 'configured bitrate')}", flush=True)
                    terminate(process)
                    process = launch("capture", settings=settings)
                time.sleep(.2)
                continue
            if MODE != "auto":
                time.sleep(1)
                continue
            if now - last_discovery > 5:
                last_discovery = now
                try:
                    found = {t["id"]: t for t in targets()}
                    for target_id in list(sockets):
                        if target_id not in found:
                            sockets.pop(target_id).close()
                    for target_id, target in found.items():
                        if target_id not in sockets:
                            sock = websocket(target["webSocketDebuggerUrl"])
                            send_ws(sock, {"id": 1, "method": "Network.enable"})
                            sockets[target_id] = sock
                except (OSError, ValueError):
                    pass
            if now - last_eval > 10:
                last_eval = now
                expression = "Array.from(document.querySelectorAll('video')).map(v=>v.currentSrc||v.src).filter(Boolean).join('\\n')"
                for sock in sockets.values():
                    try:
                        send_ws(sock, {"id": 2, "method": "Runtime.evaluate", "params": {"expression": expression, "returnByValue": True}})
                    except OSError:
                        pass
            for target_id, sock in list(sockets.items()):
                try:
                    if not select.select([sock], [], [], 0.1)[0]:
                        continue
                    event = recv_ws(sock)
                    if not event:
                        continue
                    params = event.get("params", {})
                    method = event.get("method")
                    if method == "Network.requestWillBeSent":
                        request = params.get("request", {})
                        requests[params.get("requestId")] = request
                        url = request.get("url", "")
                        if MEDIA_SUFFIX.search(url):
                            candidates.append({"url": url, "headers": request.get("headers", {}), "seen": now})
                    elif method == "Network.responseReceived":
                        mime = params.get("response", {}).get("mimeType", "").lower()
                        if any(media in mime for media in MEDIA_MIMES):
                            request = requests.get(params.get("requestId"), {})
                            url = request.get("url") or params.get("response", {}).get("url", "")
                            candidates.append({"url": url, "headers": request.get("headers", {}), "seen": now})
                    elif event.get("id") == 2:
                        value = event.get("result", {}).get("result", {}).get("value", "")
                        for url in value.splitlines():
                            if MEDIA_SUFFIX.search(url):
                                candidates.append({"url": url, "headers": {}, "seen": now})
                except (OSError, ValueError, json.JSONDecodeError):
                    sockets.pop(target_id, None)
                    sock.close()
            if len(requests) > 1000:
                requests.clear()
            # A different playable manifest means the admin selected another
            # video. Probe it while the old feed stays live, then switch the
            # single RTMP publisher. A short cooldown avoids master/variant
            # manifests from one playback causing an immediate second switch.
            while candidates and now >= switch_after:
                candidate = next_candidate(candidates, active_url, blocked, now)
                if candidate is None:
                    break
                url = candidate["url"]
                blocked[url] = now + 120
                if not playable(candidate):
                    continue
                if mode == "direct":
                    print("Switching to newly selected direct input", flush=True)
                terminate(process)
                process = launch("direct", candidate)
                mode, active_url = "direct", url
                candidates.clear()
                switch_after = time.monotonic() + SWITCH_COOLDOWN
                break
            if len(candidates) > 100:
                candidates = candidates[-100:]
    finally:
        terminate(process)
        if matcher:
            matcher.close()
        for sock in sockets.values():
            sock.close()


if __name__ == "__main__":
    main()


