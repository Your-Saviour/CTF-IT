import assert from 'node:assert/strict';
import test from 'node:test';

import {normalizeClientInfrastructure, nodeIndex} from '../frontend/static/event-planner-state.js';

test('null infrastructure opens as an editable empty network', () => {
  const infrastructure = normalizeClientInfrastructure(null);

  assert.deepEqual(infrastructure.sites, []);
  assert.equal(infrastructure.vpn_gateway.listen_port, 51820);
  assert.equal(nodeIndex(infrastructure).has('gateway'), true);
});
