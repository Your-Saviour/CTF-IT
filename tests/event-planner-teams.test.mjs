import assert from 'node:assert/strict';
import test from 'node:test';

import {TEAM_ROLE_OPTIONS, teamFieldForNode, teamForNode} from '../frontend/static/event-planner-teams.js';

test('firewall zones use the standard team-role field and default to blue', () => {
  const legacySite = {key: 'head_office'};

  assert.deepEqual(TEAM_ROLE_OPTIONS, [
    {value: 'blue', label: 'Blue team'},
    {value: 'red', label: 'Red team'},
  ]);
  assert.deepEqual(teamFieldForNode({type: 'firewall-zone', value: legacySite}), {
    name: 'firewall_team', label: 'Team role', value: 'blue', options: TEAM_ROLE_OPTIONS,
  });
  assert.equal(teamForNode({type: 'firewall-zone', value: legacySite}), 'blue');
});

test('workload and firewall zones expose their persisted team roles consistently', () => {
  const firewall = {type: 'firewall-zone', value: {firewall_team: 'red'}};
  const workload = {type: 'zone', value: {team: 'blue'}};

  assert.equal(teamForNode(firewall), 'red');
  assert.equal(teamForNode(workload), 'blue');
  assert.equal(teamFieldForNode(workload).name, 'team');
  assert.equal(teamFieldForNode({type: 'vm', value: {}}), null);
});
