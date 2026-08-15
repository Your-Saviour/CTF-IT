import assert from 'node:assert/strict';
import test from 'node:test';

import {
  normalizeClientInfrastructure,
  normalizeClientLayout,
  nodeIndex,
  renameStructuralKey,
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

test('machine icon overrides must come from the planner icon library', () => {
  const value = structuredClone(infrastructure);
  value.sites[0].zones[0].endpoints[0].icon = 'not-in-library';

  const errors = validateClientInfrastructure(value, {bases: [{id: 'ubuntu'}, {id: 'opnsense'}]});

  assert.equal(errors.some(error => error.path === 'sites[0].zones[0].endpoints[0].icon'), true);
});
