import assert from 'node:assert/strict';
import test from 'node:test';
import * as canvas from '../frontend/static/event-planner-canvas.js';

test('canvas presentation marks node roles and links beside the selection', () => {
  assert.equal(typeof canvas.topologyNodeClass, 'function');
  assert.equal(typeof canvas.topologyLinkClass, 'function');

  assert.equal(canvas.topologyNodeClass({type: 'firewall', selected: true, invalid: false}), 'topo-node firewall selected');
  assert.equal(canvas.topologyNodeClass({type: 'vm', selected: false, invalid: true}), 'topo-node vm invalid');
  assert.equal(canvas.topologyLinkClass({source: {selected: true}, target: {selected: false}}), 'topo-link selected-adjacent');
  assert.equal(canvas.topologyLinkClass({source: {selected: false}, target: {selected: false}}), 'topo-link');
});
