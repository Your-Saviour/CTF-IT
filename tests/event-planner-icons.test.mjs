import assert from 'node:assert/strict';
import test from 'node:test';

test('icon library resolves every supported override and exposes labelled options', async () => {
  let icons = {};
  try { icons = await import('../frontend/static/event-planner-icons.js'); } catch {}
  assert.equal(typeof icons.resolvePlannerIcon, 'function');

  const names = [
    'server','desktop','laptop','mobile','appliance',
    'gateway','router','switch','firewall','vpn','proxy','load_balancer',
    'web','database','dns','mail','directory','file_share','storage','certificate_authority','identity',
    'attacker','target','siem','ids','monitoring','logging','honeypot','malware','bastion','vulnerable',
    'cloud','container','kubernetes','backup','git','cicd',
    'linux','ubuntu','debian','kali','redhat','windows','macos','freebsd','opnsense','pfsense',
    'aws','azure','gcp',
  ];
  assert.deepEqual(Object.keys(icons.PLANNER_ICONS), names);
  assert.deepEqual(icons.PLANNER_ICON_OPTIONS.map(row => row.value), names);
  for (const name of names) {
    const resolved = icons.resolvePlannerIcon(name);
    assert.match(resolved.path, /^M/);
    assert.equal(resolved.viewBox, '0 0 24 24');
  }
  assert.equal(new Set(names.map(name => icons.PLANNER_ICONS[name].path)).size, names.length);
});

test('icon options are grouped into the cyber training taxonomy', async () => {
  const {PLANNER_ICON_GROUPS} = await import('../frontend/static/event-planner-icons.js');

  assert.deepEqual(PLANNER_ICON_GROUPS.map(group => group.label), [
    'Devices','Network','Services','Security','Workloads','Platforms','Cloud providers',
  ]);
  assert.equal(PLANNER_ICON_GROUPS.flatMap(group => group.options).length, 50);
  assert.deepEqual(PLANNER_ICON_GROUPS.find(group => group.label === 'Security').options.map(row => row.value), [
    'attacker','target','siem','ids','monitoring','logging','honeypot','malware','bastion','vulnerable',
  ]);
});

test('icon resolver preserves safe custom paths and falls back for malformed values', async () => {
  const {PLANNER_ICONS, resolvePlannerIcon} = await import('../frontend/static/event-planner-icons.js');
  const custom = {svg_path: 'M1 2h3v4z', viewbox: '0 0 8 8'};

  assert.deepEqual(resolvePlannerIcon(custom), {path: 'M1 2h3v4z', viewBox: '0 0 8 8'});
  assert.deepEqual(resolvePlannerIcon('missing'), {path: PLANNER_ICONS.server.path, viewBox: '0 0 24 24'});
  assert.deepEqual(resolvePlannerIcon({svg_path: '', viewbox: 'bad'}), {path: PLANNER_ICONS.server.path, viewBox: '0 0 24 24'});
});

test('machine icon pair combines semantic primary defaults with base secondary icons', async () => {
  const {PLANNER_ICONS, machineIconPair} = await import('../frontend/static/event-planner-icons.js');
  const bases = [{id: 'ubuntu_24_server', icon: 'ubuntu'}, {id: 'custom', icon: {svg_path: 'M0 0h8v8z', viewbox: '0 0 8 8'}}];
  const vm = machineIconPair('vm', {base_type: 'ubuntu_24_server'}, bases);
  const gateway = machineIconPair('gateway', {base_type: 'custom'}, bases);
  const firewall = machineIconPair('firewall', {base_type: 'opnsense'}, [{id: 'opnsense', icon: 'opnsense'}]);

  assert.equal(vm.primary.path, PLANNER_ICONS.server.path);
  assert.equal(vm.secondary.path, PLANNER_ICONS.ubuntu.path);
  assert.equal(gateway.primary.path, PLANNER_ICONS.router.path);
  assert.deepEqual(gateway.secondary, {path: 'M0 0h8v8z', viewBox: '0 0 8 8'});
  assert.equal(firewall.primary.path, PLANNER_ICONS.firewall.path);
  assert.equal(firewall.secondary.path, PLANNER_ICONS.opnsense.path);
});

test('primary and secondary overrides are independently selected and cleared', async () => {
  const {PLANNER_ICONS, machineIconPair, setMachineIconOverride} = await import('../frontend/static/event-planner-icons.js');
  const machine = {base_type: 'ubuntu_24_server'};
  const bases = [{id: 'ubuntu_24_server', icon: 'ubuntu'}];

  setMachineIconOverride(machine, 'primary_icon', 'database');
  setMachineIconOverride(machine, 'icon', 'windows');
  let pair = machineIconPair('vm', machine, bases);
  assert.equal(pair.primary.path, PLANNER_ICONS.database.path);
  assert.equal(pair.secondary.path, PLANNER_ICONS.windows.path);

  setMachineIconOverride(machine, 'primary_icon', '');
  assert.equal('primary_icon' in machine, false);
  assert.equal(machine.icon, 'windows');
  setMachineIconOverride(machine, 'icon', '');
  assert.equal('icon' in machine, false);
  pair = machineIconPair('vm', machine, bases);
  assert.equal(pair.primary.path, PLANNER_ICONS.server.path);
  assert.equal(pair.secondary.path, PLANNER_ICONS.ubuntu.path);
});

test('unsupported fields and values are not persisted as icon overrides', async () => {
  const {setMachineIconOverride} = await import('../frontend/static/event-planner-icons.js');
  const machine = {icon: 'server', primary_icon: 'database'};

  setMachineIconOverride(machine, 'primary_icon', 'not-in-library');
  setMachineIconOverride(machine, 'unknown_icon', 'server');

  assert.equal('primary_icon' in machine, false);
  assert.equal(machine.icon, 'server');
  assert.equal('unknown_icon' in machine, false);
});
