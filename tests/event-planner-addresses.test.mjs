import assert from 'node:assert/strict';
import test from 'node:test';

import {
  addressAnnotationForNode,
  addressFieldForNode,
} from '../frontend/static/event-planner-addresses.js';

test('workload zones expose a free-form address range field and annotation', () => {
  const node = {type: 'zone', value: {address_range: 'x.x.{{team_id}}.0/24'}};

  assert.deepEqual(addressFieldForNode(node), {
    name: 'address_range', label: 'Address range', value: 'x.x.{{team_id}}.0/24',
  });
  assert.equal(addressAnnotationForNode(node), 'x.x.{{team_id}}.0/24');
});

test('VMs expose a free-form address field and annotation', () => {
  const node = {type: 'vm', value: {address: 'x.x.{{team_id}}.10'}};

  assert.deepEqual(addressFieldForNode(node), {
    name: 'address', label: 'Address', value: 'x.x.{{team_id}}.10',
  });
  assert.equal(addressAnnotationForNode(node), 'x.x.{{team_id}}.10');
});

test('missing values render as empty inspector fields but no canvas annotation', () => {
  const node = {type: 'vm', value: {}};

  assert.deepEqual(addressFieldForNode(node), {name: 'address', label: 'Address', value: ''});
  assert.equal(addressAnnotationForNode(node), null);
});

test('gateway, site, and firewall nodes do not expose address controls', () => {
  for (const type of ['gateway', 'site', 'firewall-zone', 'firewall']) {
    const node = {type, value: {address: 'ignored', address_range: 'ignored'}};
    assert.equal(addressFieldForNode(node), null);
    assert.equal(addressAnnotationForNode(node), null);
  }
});
