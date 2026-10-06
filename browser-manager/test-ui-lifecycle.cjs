// Exercise the production DeleteChannel method without loading the React app.
const fs = require('node:fs');
const assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[2], 'utf8');
const method = source.slice(source.indexOf('\tasync DeleteChannel(channelid)'), source.indexOf('\tSelectChannel(channelid)'));
assert(method.includes('/channels/${channelid}/sessions'));
const Restreamer = new Function(`return class {${method}}`)();
function fixture(request) {
  const r = new Restreamer();
  r.calls = []; r.channels = new Map([['one', {channelid:'one',egresses:[]}], ['two', {channelid:'two',egresses:[]}]]);
  r.browserManagerAvailable = true;
  r.GetChannel = id => r.channels.get(id);
  r.BrowserRequest = request;
  r._dispatchEvent = (...args) => r.calls.push(['error', ...args]);
  r.SelectChannel = () => {};
  for (const name of ['StopAllEgresses','DeleteIngest','DeleteIngestSnapshot','DeleteEgress']) r[name] = async () => r.calls.push(name);
  return r;
}
(async () => {
  let r = fixture(async (path, method) => {
    assert.equal(path, '/channels/one/sessions'); assert.equal(method, 'DELETE'); r.calls.push('cleanup');
  });
  assert.equal(await r.DeleteChannel('one'), true);
  assert.equal(r.calls[0], 'cleanup'); assert(!r.channels.has('one')); assert(r.channels.has('two'));
  r = fixture(async () => { throw new Error('temporary Docker error'); });
  assert.equal(await r.DeleteChannel('one'), false);
  assert(r.channels.has('one')); assert(!r.calls.includes('DeleteIngest'));
  r.BrowserRequest = async () => {};
  assert.equal(await r.DeleteChannel('one'), true);
  r = fixture(async () => { const e = new Error('no manager'); e.status = 404; throw e; });
  r.browserManagerAvailable = false;
  assert.equal(await r.DeleteChannel('one'), true); // standalone upstream compatibility
  console.log('UI channel lifecycle tests passed: cleanup, scope, failure/retry, standalone fallback.');
})().catch(e => { console.error(e.message); process.exitCode = 1; });
