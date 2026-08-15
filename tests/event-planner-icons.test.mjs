import assert from 'node:assert/strict';
import test from 'node:test';

test('icon library resolves every supported override and exposes labelled options', async () => {
  let icons = {};
  try { icons = await import('../frontend/static/event-planner-icons.js'); } catch {}
  assert.equal(typeof icons.resolvePlannerIcon, 'function');

  const names = ['server','desktop','laptop','ubuntu','linux','debian','kali','windows','router','firewall','attacker','database','web','dns','mail','directory','cloud','container','kubernetes','storage','monitoring'];
  assert.deepEqual(Object.keys(icons.PLANNER_ICONS), names);
  assert.deepEqual(icons.PLANNER_ICON_OPTIONS.map(row => row.value), names);
  for (const name of names) {
    const resolved = icons.resolvePlannerIcon(name);
    assert.match(resolved.path, /^M/);
    assert.equal(resolved.viewBox, '0 0 24 24');
  }
});

test('icon resolver preserves safe custom paths and falls back for malformed values', async () => {
  const {PLANNER_ICONS, resolvePlannerIcon} = await import('../frontend/static/event-planner-icons.js');
  const custom = {svg_path: 'M1 2h3v4z', viewbox: '0 0 8 8'};

  assert.deepEqual(resolvePlannerIcon(custom), {path: 'M1 2h3v4z', viewBox: '0 0 8 8'});
  assert.deepEqual(resolvePlannerIcon('missing'), {path: PLANNER_ICONS.server.path, viewBox: '0 0 24 24'});
  assert.deepEqual(resolvePlannerIcon({svg_path: '', viewbox: 'bad'}), {path: PLANNER_ICONS.server.path, viewBox: '0 0 24 24'});
});
