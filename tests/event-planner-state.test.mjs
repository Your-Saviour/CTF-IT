import assert from 'node:assert/strict';
import test from 'node:test';

import {
  effectiveNodeColor,
  normalizeClientInfrastructure,
  normalizeClientLayout,
  nodeIndex,
  pruneLayout,
  renameStructuralKey,
  setNodeThemeColor,
  validateClientInfrastructure,
} from '../frontend/static/event-planner-state.js';

const infrastructure = {
  vpn_gateway: {base_type: 'ubuntu', default_plan: 'small', region: 'ewr', listen_port: 51820},
  sites: [{
    key: 'head_office', name: 'Head Office', region: 'ewr',
    firewall: {base_type: 'opnsense', default_plan: 'medium'},
    zones: [{
      key: 'corporate', name: 'Corporate', team: 'blue',
      endpoints: [{key: 'workstation', name: 'Workstation', base_type: 'ubuntu', default_plan: 'small'}],
    }],
  }],
};

test('null infrastructure opens as an editable empty network', () => {
  const infrastructure = normalizeClientInfrastructure(null);

  assert.deepEqual(infrastructure.sites, []);
  assert.equal(infrastructure.vpn_gateway.listen_port, 51820);
  assert.equal(nodeIndex(infrastructure).has('gateway'), true);
});

test('firewall zone owns firewall VM while the site owns workload zones', () => {
  const index = nodeIndex(infrastructure);

  assert.equal(index.get('firewall-zone:head_office').type, 'firewall-zone');
  assert.equal(index.get('firewall:head_office/primary').parent, 'firewall-zone:head_office');
  assert.equal(index.get('zone:head_office/corporate').parent, 'site:head_office');
  assert.equal(index.get('zone:head_office/corporate').visualParent, 'firewall-zone:head_office');
});

test('legacy firewall coordinates migrate to the primary firewall VM', () => {
  const layout = normalizeClientLayout({version: 1, nodes: {
    'firewall:head_office': {x: 10, y: 20},
    'zone:head_office/corporate': {x: 30, y: 40},
  }}, infrastructure);

  assert.deepEqual(layout.nodes['firewall:head_office/primary'], {x: 10, y: 20});
  assert.equal(layout.nodes['firewall:head_office'], undefined);
  assert.deepEqual(layout.nodes['zone:head_office/corporate'], {x: 30, y: 40});
});

test('theme colours normalize and VM overrides fall back to zone inheritance', () => {
  const layout = normalizeClientLayout({version: 1, nodes: {}, themes: {
    'zone:head_office/corporate': {color: '#2563EB'},
    'vm:head_office/corporate/workstation': {color: 'blue'},
    'site:head_office': {color: '#12345'},
  }}, infrastructure);
  const index = nodeIndex(infrastructure);

  assert.deepEqual(layout.themes, {
    'zone:head_office/corporate': {color: '#2563eb'},
  });
  assert.deepEqual(effectiveNodeColor(index, layout, 'vm:head_office/corporate/workstation'), {
    color: '#2563eb', inherited: true,
  });

  const overridden = setNodeThemeColor(layout, 'vm:head_office/corporate/workstation', '#A855F7');
  assert.deepEqual(effectiveNodeColor(index, overridden, 'vm:head_office/corporate/workstation'), {
    color: '#a855f7', inherited: false,
  });
  const reset = setNodeThemeColor(overridden, 'vm:head_office/corporate/workstation', '');
  assert.equal(reset.themes['vm:head_office/corporate/workstation'], undefined);
  assert.equal(overridden.themes['vm:head_office/corporate/workstation'].color, '#a855f7');
});

test('theme IDs follow structural renames and pruning removes deleted nodes', () => {
  const state = {infrastructure: structuredClone(infrastructure), layout: normalizeClientLayout({
    version: 1,
    nodes: {'zone:head_office/corporate': {x: 3, y: 3}},
    themes: {
      'zone:head_office/corporate': {color: '#2563eb'},
      'vm:head_office/corporate/workstation': {color: '#a855f7'},
    },
  }, infrastructure)};

  const renamed = renameStructuralKey(state, 'zone:head_office/corporate', 'Servers');
  assert.deepEqual(Object.keys(renamed.state.layout.themes).sort(), [
    'vm:head_office/servers/workstation',
    'zone:head_office/servers',
  ]);

  renamed.state.infrastructure.sites[0].zones[0].endpoints = [];
  const pruned = pruneLayout(renamed.state);
  assert.deepEqual(pruned.layout.themes, {
    'zone:head_office/servers': {color: '#2563eb'},
  });
});

test('site key rename remaps firewall zone, primary firewall, zones, and VMs', () => {
  const state = {infrastructure: structuredClone(infrastructure), layout: {version: 1, nodes: {
    'firewall-zone:head_office': {x: 1, y: 1},
    'firewall:head_office/primary': {x: 2, y: 2},
    'zone:head_office/corporate': {x: 3, y: 3},
    'vm:head_office/corporate/workstation': {x: 4, y: 4},
  }}};

  const renamed = renameStructuralKey(state, 'site:head_office', 'Branch Office');

  assert.equal(renamed.nodeId, 'site:branch_office');
  assert.deepEqual(Object.keys(renamed.state.layout.nodes).sort(), [
    'firewall-zone:branch_office',
    'firewall:branch_office/primary',
    'vm:branch_office/corporate/workstation',
    'zone:branch_office/corporate',
  ]);
});

test('both machine icon overrides must come from the planner icon library', () => {
  const value = structuredClone(infrastructure);
  value.sites[0].zones[0].endpoints[0].icon = 'not-in-library';
  value.sites[0].zones[0].endpoints[0].primary_icon = 'also-not-in-library';

  const errors = validateClientInfrastructure(value, {bases: [{id: 'ubuntu'}, {id: 'opnsense'}]});

  assert.equal(errors.some(error => error.path === 'sites[0].zones[0].endpoints[0].icon'), true);
  assert.equal(errors.some(error => error.path === 'sites[0].zones[0].endpoints[0].primary_icon'), true);
});
