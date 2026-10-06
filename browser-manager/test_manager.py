import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
import manager

class TemplateTests(unittest.TestCase):
    def test_public_default_is_blank(self):
        with patch.dict(manager.os.environ, {}, clear=True):
            self.assertEqual(manager.settings({})['url'], 'about:blank')

    def test_website_validation(self):
        for url in ('file:///etc/passwd', 'javascript:alert(1)', 'https://u:p@example.org/', 'https://example.org/\n'):
            with self.assertRaises(ValueError): manager.website(url)
        self.assertEqual(manager.website('https://example.org/video'), 'https://example.org/video')

    def test_untrusted_template_fields(self):
        for field in ('Image', 'mounts', 'runtime', 'command', 'network', 'devices'):
            with self.assertRaises(ValueError): manager.settings({field: 'evil'})
        for cfg in ({'fps': True}, {'fps': 120}, {'resolution': '3840x2160'}, {'auto_match': 'yes'}):
            with self.assertRaises(ValueError): manager.settings(cfg)

    def test_template_fixed_and_private(self):
        with patch.object(manager, 'GPU', 'GPU-1234-abcd'):
            t = manager.worker_template('a'*32, manager.settings({}))
        self.assertEqual(t['Image'], manager.IMAGE)
        self.assertNotIn('PortBindings', t['HostConfig'])
        self.assertNotIn('Privileged', t['HostConfig'])
        self.assertEqual(t['Labels'][manager.LABEL], manager.OWNER)
        self.assertEqual(len(t['HostConfig']['Mounts']), 1)
        self.assertIn('BROWSER_HARDWARE_DECODE=off', t['Env'])
        with self.assertRaises(ValueError): manager.worker_name('../plex')

class FakeManager(manager.Manager):
    async def open(self, app): pass
    async def close(self, app): pass
    async def authenticate(self, token):
        if token != 'admin-test': raise web.HTTPUnauthorized(text='Denied')
    async def inspect(self, sid):
        if sid not in self.sessions: raise web.HTTPNotFound()
        return self.workers.get(sid)
    async def engine(self, method, path, data=None, missing=False):
        self.calls.append((method, path, data))
        if path == '/containers/json?all=1':
            return [{'Labels': {manager.LABEL: manager.OWNER}, 'State': 'running'} for w in self.workers.values() if w['State']['Running']]
        if method == 'POST' and path.startswith('/containers/create'):
            sid = data['Labels'][manager.ID_LABEL]
            self.workers[sid] = {'Config': {'Labels': data['Labels']}, 'State': {'Running': False, 'Status': 'created'}}
        if method == 'DELETE' and path.startswith('/containers/'):
            for sid in list(self.workers):
                if path == '/containers/' + manager.worker_name(sid):
                    del self.workers[sid]
        if path.endswith('/start') or '/stop?' in path:
            for sid in self.workers:
                if manager.worker_name(sid) in path:
                    self.workers[sid]['State']['Running'] = path.endswith('/start')
        return None if missing else {}

class APITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.patch = patch.object(manager, 'STATE', Path(self.temp.name)/'state.json'); self.patch.start()
        self.gpu = patch.object(manager, 'GPU', 'GPU-1234-abcd'); self.gpu.start()
        self.limit = patch.object(manager, 'MAX_WORKERS', 2); self.limit.start()
        self.m = FakeManager(); self.m.calls = []; self.m.workers = {}
        self.client = TestClient(TestServer(manager.create_app(self.m))); await self.client.start_server()
        self.headers = {'Authorization': 'Bearer admin-test'}
    async def asyncTearDown(self):
        await self.client.close(); self.patch.stop(); self.gpu.stop(); self.limit.stop(); self.temp.cleanup()
    async def test_unauthorized_api_and_controls(self):
        for path in ('/browser-api/config', '/browser-api/sessions', '/browser-control/'+'a'*32+'/'):
            r = await self.client.get(path); self.assertEqual(r.status, 401)
        r = await self.client.post('/browser-api/sessions', json={}); self.assertEqual(r.status, 401)
        self.assertFalse(self.m.calls)
    async def test_cookie_and_origin(self):
        r = await self.client.post('/browser-api/control-session', json={}, headers=self.headers)
        self.assertEqual(r.status, 200)
        cookie = r.cookies[manager.COOKIE]
        self.assertTrue(cookie['httponly']); self.assertTrue(cookie['secure']); self.assertEqual(cookie['samesite'], 'Strict')
        r = await self.client.post('/browser-api/sessions', json={}, headers={**self.headers, 'Origin': 'https://evil.example'})
        self.assertEqual(r.status, 403)
    async def test_lifecycle_idempotence_capacity_and_persistence(self):
        ids = []
        for _ in range(3):
            r = await self.client.post('/browser-api/sessions', json={}, headers=self.headers)
            self.assertEqual(r.status, 201); ids.append((await r.json())['id'])
        self.assertEqual(len(manager.Manager().sessions), 3)
        for sid in ids[:2]:
            for _ in range(2):
                r = await self.client.post(f'/browser-api/sessions/{sid}/start', json={}, headers=self.headers)
                self.assertEqual(r.status, 200)
        self.assertEqual(sum('/containers/create' in c[1] for c in self.m.calls), 2)
        r = await self.client.post(f'/browser-api/sessions/{ids[2]}/start', json={}, headers=self.headers)
        self.assertEqual(r.status, 409)
        r = await self.client.patch(f'/browser-api/sessions/{ids[0]}', json={'url':'https://example.org/'}, headers=self.headers)
        self.assertEqual(r.status, 409)
        for _ in range(2):
            r = await self.client.post(f'/browser-api/sessions/{ids[0]}/stop', json={}, headers=self.headers)
            self.assertEqual(r.status, 200)
        r = await self.client.patch(f'/browser-api/sessions/{ids[0]}', json={'url':'https://example.org/'}, headers=self.headers)
        self.assertEqual(r.status, 200)
        self.assertFalse(any(c[0] == 'DELETE' and '/volumes/' in c[1] for c in self.m.calls))
    async def test_concurrent_starts_respect_capacity(self):
        ids = []
        for _ in range(3):
            r = await self.client.post('/browser-api/sessions', json={}, headers=self.headers); ids.append((await r.json())['id'])
        results = await asyncio.gather(*[self.client.post(f'/browser-api/sessions/{sid}/start', json={}, headers=self.headers) for sid in ids])
        self.assertEqual(sorted(r.status for r in results), [200, 200, 409])

    async def test_controls_readiness_requires_healthy_worker_and_http(self):
        x = {'State': {'Running': True, 'Health': {'Status': 'starting'}},
             'NetworkSettings': {'Networks': {manager.NETWORK: {'IPAddress': '172.20.0.5'}}}}
        self.m.http = MagicMock()
        self.assertFalse(await self.m.controls_ready(x))
        self.m.http.get.assert_not_called()
        x['State']['Health']['Status'] = 'healthy'
        response = MagicMock(status=200, content_type='text/html')
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=response)
        context.__aexit__ = AsyncMock(return_value=False)
        self.m.http.get.return_value = context
        self.assertTrue(await self.m.controls_ready(x))
        response.status = 503
        self.assertFalse(await self.m.controls_ready(x))
        response.status = 200; response.content_type = 'application/json'
        self.assertFalse(await self.m.controls_ready(x))
        context.__aenter__.side_effect = asyncio.TimeoutError
        self.assertFalse(await self.m.controls_ready(x))

    async def test_listing_reports_starting_without_controls(self):
        r = await self.client.post('/browser-api/sessions', json={}, headers=self.headers)
        sid = (await r.json())['id']
        await self.client.post(f'/browser-api/sessions/{sid}/start', json={}, headers=self.headers)
        self.m.workers[sid]['State']['Health'] = {'Status': 'starting'}
        r = await self.client.get('/browser-api/sessions', headers=self.headers)
        session = (await r.json())[0]
        self.assertTrue(session['running']); self.assertEqual(session['health'], 'starting')
        self.assertFalse(session['controls_ready'])

    async def test_channel_creation_is_idempotent_and_creates_container(self):
        results = await asyncio.gather(*[self.client.post('/browser-api/channels/channel-a/session', json={}, headers=self.headers) for _ in range(3)])
        sessions = [await r.json() for r in results]
        self.assertEqual({s['id'] for s in sessions}, {sessions[0]['id']})
        self.assertEqual(sessions[0]['channel_id'], 'channel-a')
        self.assertIn(sessions[0]['id'], self.m.workers)
        self.assertFalse(sessions[0]['running'])
        self.assertEqual(sum('/containers/create' in call[1] for call in self.m.calls), 1)
        r = await self.client.post('/browser-api/sessions', json={'channel_id':'channel-a'}, headers=self.headers)
        self.assertEqual(r.status, 409)
        r = await self.client.patch('/browser-api/sessions/'+sessions[0]['id'], json={'channel_id':'other'}, headers=self.headers)
        self.assertEqual(r.status, 409)

    async def test_channel_deletion_stops_removes_only_owned_session_and_retains_profile(self):
        ids = []
        for channel in ['channel-a', 'channel-b']:
            r = await self.client.post('/browser-api/channels/'+channel+'/session', json={}, headers=self.headers)
            ids.append((await r.json())['id'])
        await self.client.post('/browser-api/sessions/'+ids[0]+'/start', json={}, headers=self.headers)
        r = await self.client.delete('/browser-api/channels/channel-a/sessions', headers=self.headers)
        self.assertEqual(r.status, 200)
        self.assertEqual((await r.json())['removed'], [ids[0]])
        self.assertNotIn(ids[0], self.m.workers); self.assertNotIn(ids[0], self.m.sessions)
        self.assertIn(ids[1], self.m.workers); self.assertIn(ids[1], manager.Manager().sessions)
        self.assertTrue(any('/stop?' in c[1] for c in self.m.calls))
        self.assertFalse(any(c[0]=='DELETE' and c[1].startswith('/volumes/') for c in self.m.calls))
        r = await self.client.delete('/browser-api/channels/channel-a/sessions', headers=self.headers)
        self.assertEqual((await r.json())['removed'], [])

    async def test_failed_container_deletion_preserves_session_for_retry(self):
        r = await self.client.post('/browser-api/channels/channel-a/session', json={}, headers=self.headers)
        sid = (await r.json())['id']
        engine = self.m.engine
        async def fail(method, path, *args, **kwargs):
            if method == 'DELETE': raise web.HTTPBadGateway(text='test failure')
            return await engine(method, path, *args, **kwargs)
        with patch.object(self.m, 'engine', fail):
            r = await self.client.delete('/browser-api/channels/channel-a/sessions', headers=self.headers)
            self.assertEqual(r.status, 502)
        self.assertIn(sid, self.m.sessions)
        r = await self.client.delete('/browser-api/channels/channel-a/sessions', headers=self.headers)
        self.assertEqual(r.status, 200)

    async def test_saved_session_delete_is_scoped_and_idempotent(self):
        r = await self.client.post('/browser-api/channels/orphan/session', json={}, headers=self.headers)
        sid = (await r.json())['id']
        r = await self.client.delete('/browser-api/sessions/'+sid)
        self.assertEqual(r.status, 401); self.assertIn(sid, self.m.sessions)
        for _ in range(2):
            r = await self.client.delete('/browser-api/sessions/'+sid, headers=self.headers)
            self.assertEqual(r.status, 200); self.assertTrue((await r.json())['profiles_retained'])
        self.assertNotIn(sid, self.m.sessions); self.assertNotIn(sid, self.m.workers)
        self.assertFalse(any(c[0]=='DELETE' and c[1].startswith('/volumes/') for c in self.m.calls))

    async def test_no_application_cap_allows_more_than_two_workers(self):
        with patch.object(manager, 'MAX_WORKERS', 0):
            for _ in range(5):
                r = await self.client.post('/browser-api/sessions', json={}, headers=self.headers)
                sid = (await r.json())['id']
                r = await self.client.post('/browser-api/sessions/'+sid+'/start', json={}, headers=self.headers)
                self.assertEqual(r.status, 200)
        self.assertEqual(sum(w['State']['Running'] for w in self.m.workers.values()), 5)

if __name__ == '__main__': unittest.main()
