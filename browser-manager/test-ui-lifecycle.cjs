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
  const restore = source.slice(source.indexOf('\tRestoreBrowserChannels(sessions)'), source.indexOf('\tCreateChannel(name)'));
  const discover = source.slice(source.indexOf('\tasync _discoverChannels()'), source.indexOf('\tRestoreBrowserChannels(sessions)'));
  const Recovered = new Function('Storage', `return class {${discover}${restore}}`)({Get: () => null});
  const recovered = new Recovered();
  recovered.ID = () => 'test-core';
  const draftID = '856ef98d-1111-4111-8111-111111111111';
  recovered._listProcesses = async () => [];
  recovered.BrowserRequest = async () => [{channel_id: draftID, name: 'Draft browser'}];
  recovered.CreateChannel = () => { throw new Error('Must recover the existing browser channel instead of creating a replacement'); };
  recovered.SelectChannel = id => { recovered.selected = id; };
  await recovered._discoverChannels();
  assert.equal(recovered.selected, draftID);
  assert.equal(recovered.channels.get(draftID).available, false);
  assert.equal(recovered.channels.get(draftID).name, 'Draft browser');
  recovered.channels.get(draftID).available = true;
  recovered.channels.get(draftID).name = 'Saved channel';
  recovered.RestoreBrowserChannels([{channel_id: draftID, name: 'Old browser name'}, {channel_id: '../bad', name: 'Invalid'}]);
  assert.equal(recovered.channels.size, 1);
  assert.equal(recovered.channels.get(draftID).name, 'Saved channel');
  assert.equal(recovered.channels.get(draftID).available, true);
  console.log('UI channel lifecycle tests passed: cleanup, scope, failure/retry, standalone fallback.');
  console.log('Draft recovery tests passed: reload retains browser ownership and preserves saved channels.');
})().catch(e => { console.error(e.message); process.exitCode = 1; });
