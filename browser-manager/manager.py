"""Admin-only, fixed-template Xorg workers. Never accepts Docker configuration.

The Docker socket is privileged: keep this service private to the gateway.
Only containers and volumes carrying our exact owner label can be managed.
"""
import asyncio
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import time
from urllib.parse import urlsplit

import aiohttp
from aiohttp import web

OWNER = os.getenv("WORKER_OWNER", "badincite-browser-ui-test")
LABEL = "io.badincite.browser.owner"
ID_LABEL = "io.badincite.browser.session"
CORE = os.getenv("CORE_URL", "http://restreamer:8080")
ORIGIN = os.environ.get("PUBLIC_ORIGIN", "https://localhost:18443")
NETWORK = os.getenv("WORKER_NETWORK", OWNER + "_default")
IMAGE = os.getenv("WORKER_IMAGE", "badincite/restreamer:browser-nvidia")
GPU = os.environ.get("GPU_UUID", "")
BUS = os.getenv("GPU_BUS_ID", "auto")
RENDER = os.getenv("GPU_RENDER_NODE", "")
CARD = os.getenv("GPU_CARD_NODE", "")
STATE = Path(os.getenv("STATE_FILE", "/state/sessions.json"))
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "0"))
if MAX_WORKERS < 0:
    raise ValueError('MAX_WORKERS must be zero (no application cap) or positive')
MAX_SESSIONS = int(os.getenv('MAX_SESSIONS', '1000'))
WORKER_CPUS = float(os.getenv('WORKER_CPUS', '4'))
WORKER_MEMORY_MB = int(os.getenv('WORKER_MEMORY_MB', '2048'))
WORKER_SHM_MB = int(os.getenv('WORKER_SHM_MB', '512'))
if MAX_SESSIONS < 1 or not 0 <= WORKER_CPUS <= 1024 or not 256 <= WORKER_MEMORY_MB <= 1048576 or not 64 <= WORKER_SHM_MB <= WORKER_MEMORY_MB:
    raise ValueError('Invalid worker resource or storage settings')
COOKIE = "browser_control"
SID = re.compile(r"[a-f0-9]{32}\Z")

def website(value):
    if not isinstance(value, str) or len(value) > 2048 or any(ord(c) < 32 for c in value):
        raise ValueError("Invalid Website URL")
    if value == "about:blank":
        return value
    p = urlsplit(value)
    if p.scheme not in ("https", "http") or not p.hostname or p.username or p.password:
        raise ValueError("Website URL must be HTTP(S), without embedded credentials")
    # Browser navigation is intentionally allowed to LAN websites; this is an admin API.
    return value

def settings(data):
    allowed = {"name", "url", "resolution", "fps", "auto_match", "channel_id"}
    if not isinstance(data, dict) or set(data) - allowed:
        raise ValueError("Unknown browser setting")
    name = data.get("name", "Browser desktop")
    channel = data.get("channel_id", "")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
        raise ValueError("Name must be 1–80 characters")
    if not isinstance(channel, str) or (channel and not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", channel)):
        raise ValueError("Invalid channel ID")
    resolution = data.get("resolution", "1920x1080")
    fps = data.get("fps", 30)
    auto = data.get("auto_match", True)
    if resolution not in ("1920x1080", "1280x720"):
        raise ValueError("Choose 1080p or 720p")
    if type(fps) is not int or fps not in (24, 25, 30, 50, 60) or type(auto) is not bool:
        raise ValueError("Invalid FPS or auto-match setting")
    return dict(name=name.strip(), url=website(data.get("url", os.getenv("DEFAULT_WEBSITE", "about:blank"))),
        resolution=resolution, fps=fps, auto_match=auto, channel_id=channel)

def worker_name(sid):
    if not SID.fullmatch(sid):
        raise ValueError("Invalid session ID")
    return "badincite-browser-" + sid

def worker_template(sid, cfg):
    name = worker_name(sid)
    width, height = cfg["resolution"].split("x")
    if not re.fullmatch(r"GPU-[a-fA-F0-9-]+", GPU):
        raise ValueError('Set GPU_UUID to the explicitly selected NVIDIA GPU UUID')
    if BUS != 'auto' and not re.fullmatch(r'PCI:\d+(?:@\d+)?:\d+:\d+', BUS):
        raise ValueError('GPU_BUS_ID must be auto or an Xorg PCI BusID')
    for node in (RENDER, CARD):
        if node and not re.fullmatch(r'/dev/dri/(?:card|renderD)\d+', node):
            raise ValueError('GPU device mappings must be /dev/dri/cardN or renderDN')
    env = dict(NVIDIA_VISIBLE_DEVICES=GPU, BROWSER_GPU_UUID=GPU, NVIDIA_DRIVER_CAPABILITIES="compute,utility,video,graphics,display",
        NVIDIA_XORG_BUS_ID=BUS, NVIDIA_RENDER_NODE=RENDER, BROWSER_GPU_RENDER="on", BROWSER_HARDWARE_DECODE="off",
        DISPLAY_WIDTH=width, DISPLAY_HEIGHT=height, BROWSER_WIDTH=width, BROWSER_HEIGHT=height,
        BROWSER_FPS=str(cfg["fps"]), BROWSER_AUTO_MATCH="on" if cfg["auto_match"] else "off",
        BROWSER_AUTO_MAX_FPS="60", BROWSER_URL=cfg["url"], BROWSER_CLEAR_STALE_LOCKS="on",
        BROWSER_VIDEO_ENCODER="h264_nvenc", BROWSER_VIDEO_BITRATE="5500k", BROWSER_VIDEO_MAXRATE="6000k",
        BROWSER_VIDEO_BUFSIZE="6000k", BROWSER_DIRECT_MODE="off",
        BROWSER_RTMP_URL=f"rtmp://restreamer:1935/live/browser-{sid}.stream",
        WEB_AUDIO="1", ENABLE_TERMINAL="0", ENABLE_FILE_MANAGER="0")
    # WEB_AUDIO also starts PulseAudio in this base image; capture needs it.
    fingerprint = hashlib.sha256(json.dumps({"settings": cfg, "template_version": 3, "image": IMAGE,
        'gpu': GPU, 'bus': BUS, 'devices': [RENDER, CARD], 'cpus': WORKER_CPUS,
        'memory': WORKER_MEMORY_MB, 'shm': WORKER_SHM_MB, 'network': NETWORK}, sort_keys=True).encode()).hexdigest()
    return {"Image": IMAGE, "Hostname": name, "Env": [f"{k}={v}" for k, v in env.items()],
        "Labels": {LABEL: OWNER, ID_LABEL: sid, "io.badincite.browser.settings": fingerprint},
        "HostConfig": {"Runtime": "nvidia", "NetworkMode": NETWORK, "NanoCpus": int(WORKER_CPUS * 1_000_000_000),
            "Memory": WORKER_MEMORY_MB * 1024**2, "ShmSize": WORKER_SHM_MB * 1024**2, "RestartPolicy": {"Name": "unless-stopped"},
            "Devices": [{"PathOnHost": p, "PathInContainer": p, "CgroupPermissions": "rwm"} for p in (RENDER, CARD) if p],
            "Mounts": [{"Type": "volume", "Source": "badincite-browser-profile-" + sid, "Target": "/config"}]},
        "NetworkingConfig": {"EndpointsConfig": {NETWORK: {}}}}

class Manager:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.sessions = json.loads(STATE.read_text()) if STATE.exists() else {}
        self.controls = {}

    async def open(self, app):
        self.http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
        self.docker = aiohttp.ClientSession(connector=aiohttp.UnixConnector(path="/var/run/docker.sock"),
            timeout=aiohttp.ClientTimeout(total=45))
        # Startup fails closed if state refers to unsupported settings / IDs.
        for sid, cfg in self.sessions.items():
            worker_name(sid)
            settings(cfg)

    async def close(self, app):
        await self.http.close()
        await self.docker.close()

    def save(self):
        STATE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.sessions))
        tmp.chmod(0o600)
        tmp.replace(STATE)

    async def engine(self, method, path, data=None, missing=False):
        async with self.docker.request(method, "http://docker/v1.41" + path, json=data) as r:
            if missing and r.status == 404:
                return None
            if r.status >= 400:
                # Docker errors can include commands/URLs. Do not relay raw messages.
                await r.read()
                raise web.HTTPBadGateway(text="Container operation failed; check administrator logs/settings")
            body = await r.read()
            return json.loads(body) if body else {}

    async def authenticate(self, token):
        if not token or len(token) > 8192:
            raise web.HTTPUnauthorized(text="Restreamer admin login required")
        # A disabled Core authentication must NEVER grant controller access.
        async with self.http.get(CORE + "/api/v3/config", allow_redirects=False) as r:
            await r.read()
            if r.status != 401:
                raise web.HTTPServiceUnavailable(text="Enable Restreamer API authentication before using browsers")
        async with self.http.get(CORE + "/api/v3/config", headers={"Authorization": "Bearer " + token}, allow_redirects=False) as r:
            await r.read()
            if r.status != 200:
                raise web.HTTPUnauthorized(text="Restreamer admin login expired")

    async def inspect(self, sid):
        if sid not in self.sessions:
            raise web.HTTPNotFound(text="Unknown browser session")
        x = await self.engine("GET", "/containers/" + worker_name(sid) + "/json", missing=True)
        if x and (x["Config"].get("Labels", {}).get(LABEL) != OWNER or x["Config"]["Labels"].get(ID_LABEL) != sid):
            raise web.HTTPConflict(text="Container ownership mismatch")
        return x

    def public(self, sid, x):
        state = x.get("State", {}) if x else {}
        return dict(id=sid, **self.sessions[sid], running=state.get("Running", False),
            status=state.get("Status", "not-created"), health=state.get("Health", {}).get("Status", "unknown"),
            input_url=f"rtmp://localhost:1935/live/browser-{sid}.stream",
            controls_ready=False, control_url=f"/browser-control/{sid}/")

    async def controls_ready(self, x):
        state = x.get("State", {}) if x else {}
        if not state.get("Running") or state.get("Health", {}).get("Status") != "healthy":
            return False
        ip = x.get("NetworkSettings", {}).get("Networks", {}).get(NETWORK, {}).get("IPAddress")
        if not ip or not re.fullmatch(r"[0-9.]+", ip):
            return False
        try:
            async with self.http.get(f"http://{ip}:5800/", allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=2)) as response:
                return response.status == 200 and response.content_type == "text/html"
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return False

    async def describe(self, sid, x):
        result = self.public(sid, x)
        result["controls_ready"] = await self.controls_ready(x)
        return result

    async def config(self, request):
        return web.json_response({"default_url": website(os.getenv("DEFAULT_WEBSITE", "about:blank")), "max_workers": MAX_WORKERS})

    async def listing(self, request):
        async with self.lock:
            inspected = [(sid, await self.inspect(sid)) for sid in self.sessions]
        # Readiness probes must not hold up start/stop operations.
        items = await asyncio.gather(*(self.describe(sid, x) for sid, x in inspected))
        return web.json_response(items)

    async def create(self, request):
        cfg = settings(await request.json())
        async with self.lock:
            if cfg['channel_id'] and any(saved['channel_id'] == cfg['channel_id'] for saved in self.sessions.values()):
                raise web.HTTPConflict(text='This channel already owns a browser; use its existing session')
            if len(self.sessions) >= MAX_SESSIONS:
                raise web.HTTPConflict(text="Session storage limit reached")
            sid = secrets.token_hex(16)
            self.sessions[sid] = cfg
            self.save()
        return web.json_response(self.public(sid, None), status=201)

    async def ensure_container(self, sid, x=None):
        template = worker_template(sid, self.sessions[sid])
        if x and x['Config']['Labels'].get('io.badincite.browser.settings') != template['Labels']['io.badincite.browser.settings']:
            if x['State']['Running']:
                raise web.HTTPConflict(text='Stop this browser before changing its settings')
            await self.engine('DELETE', '/containers/' + worker_name(sid))
            x = None
        if not x:
            volume = 'badincite-browser-profile-' + sid
            v = await self.engine('GET', '/volumes/' + volume, missing=True)
            if v and (v.get('Labels', {}).get(LABEL) != OWNER or v['Labels'].get(ID_LABEL) != sid):
                raise web.HTTPConflict(text='Profile ownership mismatch')
            if not v:
                await self.engine('POST', '/volumes/create', {'Name': volume, 'Labels': {LABEL: OWNER, ID_LABEL: sid}})
            await self.engine('POST', '/containers/create?name=' + worker_name(sid), template)

    async def channel_session(self, request):
        channel = request.match_info['channel']
        data = await request.json()
        if not isinstance(data, dict):
            raise ValueError('Invalid channel settings')
        cfg = settings({**data, 'channel_id': channel})
        async with self.lock:
            sid = next((sid for sid, saved in self.sessions.items() if saved['channel_id'] == channel), None)
            if sid is None:
                if len(self.sessions) >= MAX_SESSIONS:
                    raise web.HTTPConflict(text='Session storage limit reached')
                sid = secrets.token_hex(16)
                self.sessions[sid] = cfg
                self.save()
            x = await self.inspect(sid)
            await self.ensure_container(sid, x)
            x = await self.inspect(sid)
        return web.json_response(await self.describe(sid, x))

    async def delete_channel_sessions(self, request):
        channel = request.match_info['channel']
        removed = []
        async with self.lock:
            ids = [sid for sid, cfg in self.sessions.items() if cfg['channel_id'] == channel]
            for sid in ids:
                await self.remove_session(sid)
                removed.append(sid)
        return web.json_response({'removed': removed, 'profiles_retained': True})

    async def remove_session(self, sid):
        x = await self.inspect(sid)
        if x:
            if x['State']['Running']:
                await self.engine('POST', '/containers/' + worker_name(sid) + '/stop?t=15')
            await self.engine('DELETE', '/containers/' + worker_name(sid))
        # Preserve the profile volume; never implicitly erase login data.
        del self.sessions[sid]
        self.save()

    async def delete_session(self, request):
        sid = request.match_info['sid']
        async with self.lock:
            if sid in self.sessions:
                await self.remove_session(sid)
        return web.json_response({'removed': sid, 'profiles_retained': True})

    async def update(self, request):
        sid = request.match_info["sid"]
        cfg = settings(await request.json())
        async with self.lock:
            x = await self.inspect(sid)
            if self.sessions[sid]['channel_id'] and cfg['channel_id'] != self.sessions[sid]['channel_id']:
                raise web.HTTPConflict(text='A channel-owned browser cannot be reassigned')
            if x and x["State"]["Running"]:
                raise web.HTTPConflict(text="Stop this browser before changing its settings")
            self.sessions[sid] = cfg
            self.save()
        return web.json_response(await self.describe(sid, x))

    async def action(self, request):
        sid, action = request.match_info["sid"], request.match_info["action"]
        if action not in ("start", "stop"):
            raise web.HTTPNotFound()
        async with self.lock:
            x = await self.inspect(sid)
            if action == "stop":
                if x and x["State"]["Running"]:
                    await self.engine("POST", "/containers/" + worker_name(sid) + "/stop?t=15")
            elif not x or not x["State"]["Running"]:
                workers = await self.engine("GET", "/containers/json?all=1")
                active = [c for c in workers if c.get("Labels", {}).get(LABEL) == OWNER and c["State"] in ("running", "restarting", "paused")]
                if MAX_WORKERS and len(active) >= MAX_WORKERS:
                    raise web.HTTPConflict(text=f"Maximum {MAX_WORKERS} active browser desktops")
                await self.ensure_container(sid, x)
                await self.engine("POST", "/containers/" + worker_name(sid) + "/start")
            x = await self.inspect(sid)
        return web.json_response(await self.describe(sid, x))

    async def control_session(self, request):
        now = time.time()
        self.controls = {k: v for k, v in self.controls.items() if v["until"] > now}
        if request.method == "DELETE":
            self.controls.pop(request.cookies.get(COOKIE), None)
            result = web.json_response({"ok": True})
            result.del_cookie(COOKIE, path="/")
            return result
        cid = request.cookies.get(COOKIE)
        if cid not in self.controls:
            if len(self.controls) >= 100:
                raise web.HTTPTooManyRequests()
            cid = secrets.token_urlsafe(32)
        self.controls[cid] = {"token": request["token"], "until": now + 120}
        result = web.json_response({"ok": True})
        result.set_cookie(COOKIE, cid, httponly=True, secure=True, samesite="Strict", path="/", max_age=120)
        return result

    async def controls_proxy(self, request):
        sid = request.match_info["sid"]
        x = await self.inspect(sid)
        if not x or not x["State"]["Running"]:
            raise web.HTTPConflict(text="Start the browser first")
        ip = x["NetworkSettings"]["Networks"].get(NETWORK, {}).get("IPAddress")
        if not ip or not re.fullmatch(r"[0-9.]+", ip):
            raise web.HTTPServiceUnavailable(text="Browser network is not ready")
        tail = request.match_info["tail"]
        if any(part in (".", "..") for part in tail.split("/")) or "\\" in tail:
            raise web.HTTPBadRequest()
        target = f"http://{ip}:5800/" + tail
        query = list(request.query.items())
        if request.headers.get("Upgrade", "").lower() == "websocket":
            if request.headers.get("Origin") != ORIGIN:
                raise web.HTTPForbidden(text="Invalid control origin")
            upstream = await self.http.ws_connect(target, params=query, heartbeat=20,
                protocols=request.headers.get("Sec-WebSocket-Protocol", "").split(", ") if request.headers.get("Sec-WebSocket-Protocol") else ())
            ws = web.WebSocketResponse(protocols=[upstream.protocol] if upstream.protocol else [], heartbeat=20)
            await ws.prepare(request)
            async def relay(src, dst):
                async for message in src:
                    if message.type == aiohttp.WSMsgType.BINARY:
                        await dst.send_bytes(message.data)
                    elif message.type == aiohttp.WSMsgType.TEXT:
                        await dst.send_str(message.data)
                    elif message.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSE):
                        break
            async def valid():
                while True:
                    await asyncio.sleep(30)
                    c = self.controls.get(request.cookies.get(COOKIE), {})
                    if c.get("until", 0) <= time.time():
                        return
                    try:
                        await self.authenticate(c.get("token"))
                    except web.HTTPException:
                        return
            tasks = [asyncio.create_task(relay(ws, upstream)), asyncio.create_task(relay(upstream, ws)), asyncio.create_task(valid())]
            try:
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                await upstream.close()
                await ws.close()
            return ws
        if request.method not in ("GET", "HEAD"):
            raise web.HTTPMethodNotAllowed(request.method, ["GET", "HEAD"])
        async with self.http.request(request.method, target, params=query, allow_redirects=False,
            headers={k: request.headers[k] for k in ("Range", "Accept") if k in request.headers}) as response:
            headers = {k: response.headers[k] for k in ("Content-Type", "Content-Range", "Accept-Ranges") if k in response.headers}
            headers.update({"Cache-Control": "no-store", "Content-Security-Policy": "frame-ancestors 'self'"})
            return web.Response(status=response.status, body=await response.read(), headers=headers)

def create_app(manager=None):
    m = manager or Manager()
    @web.middleware
    async def security(request, handler):
        try:
            if request.path == "/health":
                return web.json_response({"ok": True})
            if request.path.startswith("/browser-control/"):
                c = m.controls.get(request.cookies.get(COOKIE), {})
                if c.get("until", 0) <= time.time():
                    raise web.HTTPUnauthorized(text="Open controls from Restreamer after logging in")
                token = c.get("token")
            else:
                h = request.headers.get("Authorization", "")
                token = h[7:] if h.startswith("Bearer ") else None
            if request.method not in ("GET", "HEAD") and request.headers.get("Origin") not in (None, ORIGIN):
                raise web.HTTPForbidden(text="Invalid origin")
            await m.authenticate(token)
            request["token"] = token
            return await handler(request)
        except (ValueError, json.JSONDecodeError):
            return web.json_response({"message": "Invalid browser settings or request"}, status=400)
        except web.HTTPException as e:
            return web.json_response({"message": e.text}, status=e.status)
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return web.json_response({"message": "Backend unavailable; retry shortly"}, status=503)
    app = web.Application(middlewares=[security], client_max_size=8192)
    app.on_startup.append(m.open)
    app.on_cleanup.append(m.close)
    app.router.add_get("/health", m.config)
    app.router.add_get("/browser-api/config", m.config)
    app.router.add_get("/browser-api/sessions", m.listing)
    app.router.add_post('/browser-api/channels/{channel:[a-zA-Z0-9_-]{1,80}}/session', m.channel_session)
    app.router.add_delete('/browser-api/channels/{channel:[a-zA-Z0-9_-]{1,80}}/sessions', m.delete_channel_sessions)
    app.router.add_post("/browser-api/sessions", m.create)
    app.router.add_patch("/browser-api/sessions/{sid:[a-f0-9]{32}}", m.update)
    app.router.add_delete('/browser-api/sessions/{sid:[a-f0-9]{32}}', m.delete_session)
    app.router.add_post("/browser-api/sessions/{sid:[a-f0-9]{32}}/{action}", m.action)
    app.router.add_post("/browser-api/control-session", m.control_session)
    app.router.add_delete("/browser-api/control-session", m.control_session)
    app.router.add_route("*", "/browser-control/{sid:[a-f0-9]{32}}/{tail:.*}", m.controls_proxy)
    return app

if __name__ == "__main__":
    web.run_app(create_app(), host="0.0.0.0", port=8090, access_log=None)
