import {PLANNER_ICONS} from './event-planner-icons.js';

export const clone = value => structuredClone(value);
export const slugify = value => String(value || '').toLowerCase().trim().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '') || 'item';
const SLUG = /^[a-z][a-z0-9_]{0,63}$/;

export function normalizeClientInfrastructure(value) {
  const result = clone(value || {
    vpn_gateway: {
      base_type: 'ubuntu_24_server',
      default_plan: 'vc2-1c-1gb',
      region: 'ewr',
      listen_port: 51820,
    },
    sites: [],
  });
  for (const site of result.sites || []) for (const zone of site.zones || []) {
    const reserved = new Set((zone.endpoints || []).filter(row => row.count == null).map(row => row.key));
    const generated = new Set(), rows = [];
    for (const endpoint of zone.endpoints || []) {
      if (endpoint.count == null) { rows.push(clone(endpoint)); continue; }
      for (let index = 1; index <= Number(endpoint.count); index++) {
        const row = clone(endpoint); delete row.count;
        const base = `${row.key}_${index}`; let key = base, suffix = 2;
        while (reserved.has(key) || generated.has(key)) key = `${base}_${suffix++}`;
        row.key = key; row.name = key.replaceAll('_', ' ').replace(/\b\w/g, c => c.toUpperCase());
        generated.add(key); rows.push(row);
      }
    }
    zone.endpoints = rows;
  }
  return result;
}

export function nodeIndex(infrastructure) {
  const map = new Map([['gateway', {type: 'gateway', value: infrastructure.vpn_gateway, parent: null, path: 'vpn_gateway'}]]);
  (infrastructure.sites || []).forEach((site, si) => {
    const sid = `site:${site.key}`;
    const firewallZoneId = `firewall-zone:${site.key}`;
    map.set(sid, {type: 'site', value: site, parent: 'gateway', path: `sites[${si}]`, site});
    map.set(firewallZoneId, {
      type: 'firewall-zone', value: site, parent: sid, visualParent: sid,
      path: `sites[${si}]`, site,
    });
    map.set(`firewall:${site.key}/primary`, {
      type: 'firewall', value: site.firewall, parent: firewallZoneId,
      visualParent: firewallZoneId, path: `sites[${si}].firewall`, site,
    });
    (site.zones || []).forEach((zone, zi) => {
      const zid = `zone:${site.key}/${zone.key}`;
      map.set(zid, {
        type: 'zone', value: zone, parent: sid, visualParent: firewallZoneId,
        path: `sites[${si}].zones[${zi}]`, site,
      });
      (zone.endpoints || []).forEach((vm, vi) => map.set(`vm:${site.key}/${zone.key}/${vm.key}`, {
        type: 'vm', value: vm, parent: zid, visualParent: zid,
        path: `sites[${si}].zones[${zi}].endpoints[${vi}]`, site,
      }));
    });
  });
  return map;
}

export function normalizeClientLayout(layout, infrastructure) {
  const result = clone(layout || {version: 1, nodes: {}});
  result.version = 1;
  result.nodes = result.nodes && typeof result.nodes === 'object' ? result.nodes : {};
  for (const site of infrastructure.sites || []) {
    const legacyId = `firewall:${site.key}`;
    const primaryId = `firewall:${site.key}/primary`;
    if (result.nodes[legacyId] && !result.nodes[primaryId]) {
      result.nodes[primaryId] = result.nodes[legacyId];
    }
    delete result.nodes[legacyId];
  }
  return result;
}

export function renameStructuralKey(state, nodeId, rawKey) {
  const index = nodeIndex(state.infrastructure), node = index.get(nodeId);
  if (!node || !['site', 'zone', 'vm'].includes(node.type)) return {state, nodeId};
  const key = slugify(rawKey), parts = nodeId.substring(nodeId.indexOf(':') + 1).split('/');
  node.value.key = key;
  const oldToken = node.type === 'site' ? parts[0] : node.type === 'zone' ? `${parts[0]}/${parts[1]}` : parts.join('/');
  const newToken = node.type === 'site' ? key : node.type === 'zone' ? `${parts[0]}/${key}` : `${parts[0]}/${parts[1]}/${key}`;
  const remapped = {};
  for (const [id, position] of Object.entries(state.layout?.nodes || {})) {
    let next = id;
    for (const prefix of node.type === 'site' ? ['site:', 'firewall-zone:', 'firewall:', 'zone:', 'vm:'] : node.type === 'zone' ? ['zone:', 'vm:'] : ['vm:']) {
      if (id === `${prefix}${oldToken}` || id.startsWith(`${prefix}${oldToken}/`)) next = `${prefix}${newToken}${id.slice(prefix.length + oldToken.length)}`;
    }
    remapped[next] = position;
  }
  state.layout = {version: 1, nodes: remapped};
  return {state, nodeId: `${node.type}:${newToken}`};
}

export function pruneLayout(state) {
  const valid = new Set(nodeIndex(state.infrastructure).keys()), nodes = {};
  for (const [id, position] of Object.entries(state.layout?.nodes || {})) {
    if (valid.has(id)) nodes[id] = position;
  }
  state.layout = {version: 1, nodes};
  return state;
}

export function validateClientInfrastructure(value, catalogues = {}) {
  const errors = [], bases = new Set((catalogues.bases || []).map(row => row.id));
  const add = (path, nodeId, message) => errors.push({path, nodeId, message});
  const key = (value, path, nodeId, seen) => {
    if (!SLUG.test(value || '')) add(path, nodeId, 'Key must start with a letter and contain only lowercase letters, numbers, and underscores');
    else if (seen.has(value)) add(path, nodeId, `Key duplicates '${value}'`); else seen.add(value);
  };
  const machine = (row, path, nodeId) => {
    if (!row || typeof row !== 'object') { add(path, nodeId, 'Machine settings are required'); return; }
    if (!row.base_type || (bases.size && !bases.has(row.base_type))) add(`${path}.base_type`, nodeId, 'Choose an available base type');
    if (!String(row.default_plan || '').trim()) add(`${path}.default_plan`, nodeId, 'Cloud plan is required');
    if (row.ust_prompt != null && (typeof row.ust_prompt !== 'string' || row.ust_prompt.length > 8000)) add(`${path}.ust_prompt`, nodeId, 'UST prompt must be at most 8000 characters');
    for (const field of ['primary_icon', 'icon']) {
      if (row[field] != null && (typeof row[field] !== 'string' || !PLANNER_ICONS[row[field]])) add(`${path}.${field}`, nodeId, 'Choose a supported icon or Automatic');
    }
  };
  const gateway = value?.vpn_gateway;
  if (!gateway) add('vpn_gateway', 'gateway', 'VPN gateway is required');
  else {
    machine(gateway, 'vpn_gateway', 'gateway');
    if (!String(gateway.region || '').trim()) add('vpn_gateway.region', 'gateway', 'Region is required');
    if (!Number.isInteger(Number(gateway.listen_port)) || Number(gateway.listen_port) < 1 || Number(gateway.listen_port) > 65535) add('vpn_gateway.listen_port', 'gateway', 'Listen port must be from 1 to 65535');
  }
  if (!(value?.sites || []).length) add('sites', 'gateway', 'Add at least one site');
  const siteKeys = new Set();
  (value?.sites || []).forEach((site, si) => {
    const path = `sites[${si}]`, sid = `site:${site.key}`;
    key(site.key, `${path}.key`, sid, siteKeys);
    if (!String(site.name || '').trim()) add(`${path}.name`, sid, 'Site name is required');
    if (!String(site.region || '').trim()) add(`${path}.region`, sid, 'Region is required');
    machine(site.firewall, `${path}.firewall`, `firewall:${site.key}/primary`);
    if (!(site.zones || []).length) add(`${path}.zones`, sid, 'Add at least one zone');
    if ((site.zones || []).length > 15) add(`${path}.zones`, sid, 'A site supports at most 15 zones');
    const zoneKeys = new Set();
    (site.zones || []).forEach((zone, zi) => {
      const zpath = `${path}.zones[${zi}]`, zid = `zone:${site.key}/${zone.key}`;
      key(zone.key, `${zpath}.key`, zid, zoneKeys);
      if (!String(zone.name || '').trim()) add(`${zpath}.name`, zid, 'Zone name is required');
      if (!['blue', 'red'].includes(zone.team)) add(`${zpath}.team`, zid, 'Team role must be blue or red');
      if ((zone.endpoints || []).length > 245) add(zpath, zid, 'A zone supports at most 245 VMs');
      const vmKeys = new Set();
      (zone.endpoints || []).forEach((vm, vi) => {
        const vpath = `${zpath}.endpoints[${vi}]`, vid = `vm:${site.key}/${zone.key}/${vm.key}`;
        key(vm.key, `${vpath}.key`, vid, vmKeys);
        if (!String(vm.name || '').trim()) add(`${vpath}.name`, vid, 'VM name is required');
        machine(vm, vpath, vid);
      });
    });
  });
  return errors;
}

export function createPlannerStore(initial) {
  let state = clone(initial), listeners = [];
  return {get: () => state, set(next) { state = clone(next); listeners.forEach(fn => fn(state)); }, update(fn) { this.set(fn(clone(state))); }, subscribe(fn) { listeners.push(fn); return () => listeners = listeners.filter(row => row !== fn); }};
}
