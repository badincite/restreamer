// The license plugin requires the control bar, which exists only after ready.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const script = fs.readFileSync(process.argv[2], 'utf8');
for (const chromecast of [false, true]) {
  let ready, initialized = false, licensed = false;
  const player = {
    ready(callback) { ready = callback; },
    license() { assert.ok(initialized, 'License must wait until controls exist'); licensed = true; },
    play() {},
  };
  const context = {
    autoplay: false, chromecast, airplay: false,
    playerConfig: {poster: 'poster.jpg', source: 'live.m3u8', license: {license: 'CC BY 4.0'}, logo: {image: ''}},
    window: {location: {origin: 'https://example.com'}},
    videojs(id, config) {
      assert.equal(id, 'player');
      assert.equal(config.plugins.license, undefined, 'No eager license plugin');
      assert.equal(!!config.plugins.chromecast, chromecast, 'Cast plugin is optional');
      assert.equal(config.sources[0].src, 'https://example.com/live.m3u8');
      initialized = true;
      return player;
    },
  };
  vm.runInNewContext(script, context);
  ready();
  assert.ok(licensed);
}
console.log('Playersite readiness and optional Chromecast regression checks passed');
