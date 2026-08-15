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

test('machine nodes use icon-first presentation while structural nodes retain cards', () => {
  assert.equal(typeof canvas.topologyNodePresentation, 'function');
  assert.equal(canvas.topologyNodePresentation({type: 'vm', icon: {path: 'M0 0'}}), 'machine');
  assert.equal(canvas.topologyNodePresentation({type: 'firewall', icon: {path: 'M0 0'}}), 'machine');
  assert.equal(canvas.topologyNodePresentation({type: 'gateway', icon: null}), 'machine');
  assert.equal(canvas.topologyNodePresentation({type: 'vm'}), 'machine');
  assert.equal(canvas.topologyNodePresentation({type: 'zone', icon: null}), 'structural');
  assert.equal(canvas.topologyNodePresentation({type: 'site'}), 'structural');
});

test('secondary machine icon uses a prominent badge and glyph', () => {
  assert.equal(canvas.MACHINE_ICON_GEOMETRY.secondaryBadgeRadius, 11);
  assert.equal(canvas.MACHINE_ICON_GEOMETRY.secondarySize, 14);
});

test('hierarchical layout balances siblings beneath their visual parent', () => {
  assert.equal(typeof canvas.calculateHierarchicalLayout, 'function');
  const graph = [
    {id: 'gateway', parent: null},
    {id: 'site:a', parent: 'gateway'},
    {id: 'site:b', parent: 'gateway'},
    {id: 'site:c', parent: 'gateway'},
    {id: 'firewall-zone:a', parent: 'site:a'},
  ];

  const result = canvas.calculateHierarchicalLayout(graph);

  assert.deepEqual(result, {
    version: 1,
    added: true,
    nodes: {
      gateway: {x: 120, y: 70},
      'site:a': {x: 120, y: 180},
      'site:b': {x: 310, y: 180},
      'site:c': {x: -70, y: 180},
      'firewall-zone:a': {x: 120, y: 290},
    },
  });
});

test('hierarchical layout preserves saved positions and skips occupied slots', () => {
  const graph = [
    {id: 'gateway', parent: null},
    {id: 'site:new', parent: 'gateway'},
    {id: 'site:saved', parent: 'gateway'},
  ];
  const saved = {
    version: 1,
    nodes: {
      gateway: {x: 120, y: 70},
      'site:saved': {x: 120, y: 180},
    },
  };

  const first = canvas.calculateHierarchicalLayout(graph, saved);
  const second = canvas.calculateHierarchicalLayout(graph, saved);

  assert.deepEqual(first.nodes.gateway, {x: 120, y: 70});
  assert.deepEqual(first.nodes['site:saved'], {x: 120, y: 180});
  assert.deepEqual(first.nodes['site:new'], {x: 310, y: 180});
  assert.equal(first.added, true);
  assert.deepEqual(second, first);
});

test('hierarchical layout reports a complete saved layout as unchanged', () => {
  const graph = [{id: 'gateway', parent: null}];
  const saved = {version: 1, nodes: {gateway: {x: 444, y: 222}}};

  assert.deepEqual(canvas.calculateHierarchicalLayout(graph, saved), {
    version: 1,
    added: false,
    nodes: {gateway: {x: 444, y: 222}},
  });
});

test('collision fallback chooses the free slot nearest the preferred sibling position', () => {
  const graph = [
    {id: 'gateway', parent: null},
    {id: 'site:zero', parent: 'gateway'},
    {id: 'site:one', parent: 'gateway'},
    {id: 'site:two', parent: 'gateway'},
    {id: 'site:new', parent: 'gateway'},
    {id: 'site:blocker', parent: 'gateway'},
  ];
  const saved = {
    version: 1,
    nodes: {
      gateway: {x: 120, y: 70},
      'site:zero': {x: 900, y: 180},
      'site:one': {x: 1100, y: 180},
      'site:two': {x: 1300, y: 180},
      'site:blocker': {x: 500, y: 180},
    },
  };

  const result = canvas.calculateHierarchicalLayout(graph, saved);

  assert.deepEqual(result.nodes['site:new'], {x: 310, y: 180});
});
