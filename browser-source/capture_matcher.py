"""Measure the playing video, without downloading or decrypting media."""

import hashlib
import json
import math
import select
import statistics


EXPRESSION = r"""(() => {
  const videos = [];
  function scan(doc, depth) {
    for (const v of doc.querySelectorAll('video')) {
      const r = v.getBoundingClientRect();
      if (v.paused || v.ended || v.readyState < 3 || r.width <= 0 || r.height <= 0) continue;
      const q = v.getVideoPlaybackQuality();
      videos.push({source:v.currentSrc || v.src || v.id, time:v.currentTime,
        frames:q.totalVideoFrames, bytes:v.webkitVideoDecodedByteCount,
        width:v.videoWidth, height:v.videoHeight, area:r.width*r.height});
    }
    if (depth < 3) for (const frame of doc.querySelectorAll('iframe')) {
      try { if (frame.contentDocument) scan(frame.contentDocument, depth + 1); } catch (_) {}
    }
  }
  scan(document, 0);
  return videos.sort((a,b)=>b.area-a.area)[0] || null;
})()"""


class Measurement:
    def __init__(self):
        self.previous = None
        self.windows = []

    def add(self, sample):
        previous, self.previous = self.previous, sample
        if previous is None:
            return None
        dt = sample['time'] - previous['time']
        frames = sample['frames'] - previous['frames']
        if not 3 <= dt <= 12 or frames <= 0:
            self.windows.clear()
            return None
        fps = frames / dt
        standard = min((24, 25, 30, 50, 60), key=lambda rate: abs(rate - fps))
        if abs(standard - fps) > 1.5:
            self.windows.clear()
            return None
        bitrate = None
        if isinstance(sample.get('bytes'), (int, float)) and isinstance(previous.get('bytes'), (int, float)):
            difference = sample['bytes'] - previous['bytes']
            if difference < 0:
                self.windows.clear()
                return None
            if difference > 0:
                bitrate = difference * 8 / dt / 1000
        self.windows.append((standard, bitrate))
        self.windows = self.windows[-3:]
        if len(self.windows) < 3 or len({rate for rate, _ in self.windows}) != 1:
            return None
        result = {'fps': str(standard)}
        bitrates = [rate for _, rate in self.windows if rate is not None]
        if len(bitrates) == 3:
            result['bitrate'] = statistics.median(bitrates)
        return result


class CaptureMatcher:
    def __init__(self, discover, connect, send, receive, minimum=1000, maximum=12000, max_fps=60):
        self.discover, self.connect, self.send, self.receive = discover, connect, send, receive
        self.minimum, self.maximum, self.max_fps = minimum, maximum, max_fps
        self.sockets = {}
        self.latest = {}
        self.measurement = Measurement()
        self.identity = None
        self.last_discovery = self.last_eval = self.last_change = -100
        self.applied = None
        self.applied_identity = None
        self.last_sample = None

    def close(self):
        for sock in self.sockets.values():
            sock.close()
        self.sockets.clear()

    def observe(self, target, sample, now):
        if not isinstance(sample, dict):
            self.latest.pop(target, None)
            return
        try:
            sample = sample.copy()
            for key in ('time', 'frames', 'area', 'width', 'height'):
                sample[key] = float(sample[key])
            if not all(math.isfinite(sample[key]) for key in ('time', 'frames', 'area', 'width', 'height')):
                return
            if sample['width'] < 320 or sample['height'] < 180 or sample['area'] <= 0:
                return
        except (KeyError, TypeError, ValueError):
            return
        byte_count = sample.get('bytes')
        if not isinstance(byte_count, (int, float)) or not math.isfinite(byte_count) or byte_count < 0:
            sample['bytes'] = None
        self.latest[target] = (now, sample)

    def proposal(self, now):
        active = [(target, sample) for target, (seen, sample) in self.latest.items() if now - seen < 8]
        if not active:
            return None
        target, sample = max(active, key=lambda item: item[1]['area'])
        # Keep URLs in memory only; never include them in status or logs.
        identity = hashlib.sha256(f"{target}:{sample.get('source')}:{sample['width']}:{sample['height']}".encode()).hexdigest()
        if identity != self.identity:
            self.identity = identity
            self.measurement = Measurement()
        signature = (identity, sample['time'], sample['frames'], sample.get('bytes'))
        if signature == self.last_sample:
            return None
        self.last_sample = signature
        measured = self.measurement.add(sample)
        if measured is None:
            return None
        measured['fps'] = str(min(int(measured['fps']), self.max_fps))
        if 'bitrate' in measured:
            value = min(self.maximum, max(self.minimum, round(measured['bitrate'] / 250) * 250))
            measured['bitrate'] = f'{value}k'
            measured['maxrate'] = f'{math.ceil(value * 1.1 / 250) * 250}k'
            measured['bufsize'] = measured['maxrate']
        if self.applied is not None:
            # VBR changes with scene complexity. Reconfiguring the encoder for
            # those fluctuations interrupts RTMP without a playback switch.
            # Permit a late byte counter to fill in an FPS-only profile.
            if (self.identity == self.applied_identity and measured['fps'] == self.applied['fps']
                    and ('bitrate' in self.applied or 'bitrate' not in measured)):
                return None
            if 'bitrate' not in measured:
                measured.update({k: v for k, v in self.applied.items() if k != 'fps'})
            old = int(self.applied.get('bitrate', '0k')[:-1])
            new = int(measured.get('bitrate', '0k')[:-1])
            if measured['fps'] == self.applied['fps'] and (new == old or (old > 0 and abs(new - old) / old < .2)):
                self.applied_identity = self.identity
                return None
        if now - self.last_change < 30:
            return None
        self.applied = measured
        self.applied_identity = self.identity
        self.last_change = now
        return measured.copy()

    def tick(self, now):
        if now - self.last_discovery >= 5:
            self.last_discovery = now
            try:
                found = {target['id']: target for target in self.discover()}
                for target in list(self.sockets):
                    if target not in found:
                        self.sockets.pop(target).close()
                        self.latest.pop(target, None)
                for target, info in found.items():
                    if target not in self.sockets:
                        self.sockets[target] = self.connect(info['webSocketDebuggerUrl'])
            except (OSError, ValueError):
                pass
        if now - self.last_eval >= 5:
            self.last_eval = now
            for target, sock in list(self.sockets.items()):
                try:
                    self.send(sock, {'id': 700, 'method': 'Runtime.evaluate',
                                     'params': {'expression': EXPRESSION, 'returnByValue': True}})
                except OSError:
                    self.sockets.pop(target).close()
                    self.latest.pop(target, None)
        updated = False
        for target, sock in list(self.sockets.items()):
            try:
                for _ in range(10):
                    if not select.select([sock], [], [], 0)[0]:
                        break
                    event = self.receive(sock)
                    if event and event.get('id') == 700:
                        self.observe(target, event.get('result', {}).get('result', {}).get('value'), now)
                        updated = True
            except (OSError, ValueError):
                self.sockets.pop(target).close()
                self.latest.pop(target, None)
        return self.proposal(now) if updated else None
