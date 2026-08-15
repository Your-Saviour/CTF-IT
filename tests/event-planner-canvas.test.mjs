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

test('canvas address annotations enrich accessible labels without inventing placeholders', () => {
  assert.equal(
    canvas.topologyAccessibleLabel({type: 'zone', label: 'Corporate', annotation: 'x.x.{{team_id}}.0/24'}),
    'zone: Corporate, address x.x.{{team_id}}.0/24',
  );
  assert.equal(canvas.topologyAccessibleLabel({type: 'vm', label: 'Web', annotation: null}), 'vm: Web');
  assert.equal(canvas.topologyAccessibleLabel({type: 'vm', label: 'Web', annotation: ''}), 'vm: Web');
});

test('visual address annotations are constrained while short values remain exact', () => {
  assert.equal(canvas.truncatedAnnotation('1234567890', 8), '12345…');
  assert.equal(canvas.truncatedAnnotation('short', 8), 'short');
  assert.equal(canvas.truncatedAnnotation(null, 8), '');
});

test('zones and VMs receive distinct address text presentation', () => {
  assert.deepEqual(canvas.topologyAnnotationPresentation({type: 'zone', annotation: '10.0.0.0/24'}), {
    className: 'zone-address-rail', text: '10.0.0.0/24', x: 0, y: 36, height: 24,
  });
  assert.deepEqual(canvas.topologyAnnotationPresentation({type: 'vm', annotation: '10.0.0.10'}), {
    className: 'topo-node-address', text: '10.0.0.10', x: 0, y: 46,
  });
  assert.equal(canvas.topologyAnnotationPresentation({type: 'site', annotation: 'ignored'}), null);
  assert.equal(canvas.topologyAnnotationPresentation({type: 'vm', annotation: null}), null);
});

test('annotated VM bounds contain the address baseline without enlarging plain VMs', () => {
  const annotated = {type: 'vm', annotation: '10.0.0.10', x: 100, y: 200};
  const plain = {type: 'vm', annotation: null, x: 100, y: 200};
  const address = canvas.topologyAnnotationPresentation(annotated);

  assert.deepEqual(canvas.machineBounds(annotated), {x: 60, y: 170, width: 80, height: 84});
  assert.deepEqual(canvas.machineBounds(plain), {x: 60, y: 170, width: 80, height: 72});
  assert.equal(annotated.y + address.y < canvas.machineBounds(annotated).y + canvas.machineBounds(annotated).height, true);
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

test('secondary machine icon uses a prominent glyph without a surrounding badge', () => {
  assert.equal('secondaryBadgeRadius' in canvas.MACHINE_ICON_GEOMETRY, false);
  assert.deepEqual(canvas.MACHINE_ICON_GEOMETRY, {
    secondarySize: 24,
    secondaryX: -2,
    secondaryY: -7,
  });
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

test('zone bounds use minimum size and grow around direct machine children', () => {
  const zone = {id: 'zone:a/blue', x: 100, y: 200};
  assert.deepEqual(canvas.calculateZoneBounds(zone, []), {
    x: 100, y: 200, width: 280, height: 190, headerHeight: 36,
  });
  assert.deepEqual(canvas.calculateZoneBounds(zone, [
    {id: 'vm:a/blue/web', type: 'vm', x: 430, y: 390},
  ]), {x: 100, y: 200, width: 390, height: 252, headerHeight: 36});

  const graph = [
    {id: 'zone:a/blue', type: 'zone'},
    {id: 'vm:a/blue/web', type: 'vm', parent: 'zone:a/blue'},
    {id: 'zone:a/red', type: 'zone'},
    {id: 'vm:a/red/kali', type: 'vm', parent: 'zone:a/red'},
  ];
  assert.deepEqual(canvas.zoneChildren(graph, 'zone:a/blue').map(row => row.id), [
    'vm:a/blue/web',
  ]);
});

test('subnet rail expands workload zone geometry and shifts arranged VMs below it', () => {
  const annotated = {id: 'zone:a/blue', type: 'zone', annotation: '10.0.0.0/24', x: 100, y: 200};
  const plain = {id: 'zone:a/red', type: 'zone', annotation: null, x: 100, y: 200};
  const firewall = {id: 'firewall-zone:a', type: 'firewall-zone', annotation: 'ignored', x: 100, y: 200};

  assert.equal(canvas.zoneHeaderHeight(annotated), 60);
  assert.equal(canvas.zoneHeaderHeight(plain), 36);
  assert.equal(canvas.zoneHeaderHeight(firewall), 36);
  assert.deepEqual(canvas.calculateZoneBounds(annotated, []), {
    x: 100, y: 200, width: 280, height: 214, headerHeight: 60,
  });
  assert.deepEqual(canvas.arrangeZoneChildren(annotated, [{id: 'vm', type: 'vm'}]).vm, {x: 160, y: 316});
  assert.deepEqual(
    canvas.constrainMachinePosition({x: 80, y: 210}, canvas.calculateZoneBounds(annotated, [])),
    {x: 160, y: 316},
  );
});

test('zone arrangement packs children deterministically and translation is atomic', () => {
  const zone = {id: 'zone:a/blue', x: 100, y: 200};
  const children = ['one', 'two', 'three', 'four', 'five'].map(id => ({id, type: 'vm'}));
  const arranged = canvas.arrangeZoneChildren(zone, children);
  assert.deepEqual(arranged.one, {x: 160, y: 292});
  assert.deepEqual(arranged.two, {x: 264, y: 292});
  assert.deepEqual(arranged.four, {x: 160, y: 388});

  const moved = canvas.translateZoneLayout(
    {version: 1, nodes: {'zone:a/blue': {x: 100, y: 200}, one: arranged.one}},
    'zone:a/blue', ['one'], 25, -10,
  );
  assert.deepEqual(moved.nodes['zone:a/blue'], {x: 125, y: 190});
  assert.deepEqual(moved.nodes.one, {x: 185, y: 282});
});

test('topology links omit contained machines and target container boundaries', () => {
  const nodes = [
    {id: 'site:a', type: 'site', x: 0, y: 0},
    {id: 'zone:a/blue', type: 'zone', parent: 'site:a', x: 100, y: 100},
    {id: 'vm:a/blue/web', type: 'vm', parent: 'zone:a/blue', x: 160, y: 200},
  ];
  const links = canvas.topologyLinks(nodes);
  assert.deepEqual(links.map(link => [link.source.id, link.target.id]), [
    ['site:a', 'zone:a/blue'],
  ]);
  const points = canvas.linkEndpoints(links[0], new Map([
    ['zone:a/blue', {x: 100, y: 100, width: 280, height: 190}],
  ]));
  assert.equal(points.x2, 100);
  assert.equal(points.y2 >= 100 && points.y2 <= 290, true);
});

test('arranged zone layout changes only direct machine children', () => {
  const graph = [
    {id: 'zone:a/blue', type: 'zone', x: 100, y: 200},
    {id: 'vm:a/blue/web', type: 'vm', parent: 'zone:a/blue', x: 900, y: 900},
    {id: 'firewall-zone:a', type: 'firewall-zone', x: 500, y: 200},
    {id: 'firewall:a/primary', type: 'firewall', parent: 'firewall-zone:a', x: 900, y: 900},
  ];
  const layout = {version: 1, nodes: Object.fromEntries(graph.map(({id, x, y}) => [id, {x, y}]))};

  const workload = canvas.arrangedZoneLayout(graph, layout, 'zone:a/blue');
  assert.deepEqual(workload.nodes['vm:a/blue/web'], {x: 160, y: 292});
  assert.deepEqual(workload.nodes['firewall:a/primary'], {x: 900, y: 900});

  const firewall = canvas.arrangedZoneLayout(graph, layout, 'firewall-zone:a');
  assert.deepEqual(firewall.nodes['firewall:a/primary'], {x: 560, y: 292});
  assert.deepEqual(canvas.arrangedZoneLayout(graph, layout, 'vm:a/blue/web'), layout);
});

test('machine movement keeps fixed zone edges and permits right-bottom expansion', () => {
  const bounds = {x: 100, y: 200, width: 280, height: 190};
  assert.deepEqual(canvas.constrainMachinePosition({x: 80, y: 210}, bounds), {x: 160, y: 292});
  assert.deepEqual(canvas.constrainMachinePosition({x: 500, y: 500}, bounds), {x: 500, y: 500});
});

test('hierarchical layout migrates legacy machine centres inside zone frames', () => {
  const graph = [
    {id: 'zone:a/blue', type: 'zone', parent: null},
    {id: 'vm:a/blue/web', type: 'vm', parent: 'zone:a/blue'},
  ];
  const result = canvas.calculateHierarchicalLayout(graph, {
    version: 1,
    nodes: {
      'zone:a/blue': {x: 120, y: 290},
      'vm:a/blue/web': {x: 120, y: 400},
    },
  });

  assert.deepEqual(result.nodes['zone:a/blue'], {x: 120, y: 290});
  assert.deepEqual(result.nodes['vm:a/blue/web'], {x: 180, y: 400});
  assert.equal(result.added, true);
});

test('canvas theme styles are scoped and coordinate updates preserve themes', () => {
  assert.deepEqual(canvas.topologyThemeStyle({color: '#2563eb'}), {
    '--node-theme-color': '#2563eb',
  });
  assert.deepEqual(canvas.topologyThemeStyle({color: null}), {});

  const layout = {
    version: 1,
    nodes: {gateway: {x: 1, y: 2}},
    themes: {'zone:a/blue': {color: '#2563eb'}},
  };
  assert.deepEqual(canvas.mergeLayoutNodes(layout, {gateway: {x: 3, y: 4}}), {
    version: 1,
    nodes: {gateway: {x: 3, y: 4}},
    themes: {'zone:a/blue': {color: '#2563eb'}},
  });
});
