export const PLANNER_ICONS = Object.freeze({
  server: {label: 'Server', category: 'Devices', path: 'M3 2h18v6H3V2zm3 2v2h2V4H6zm-3 6h18v6H3v-6zm3 2v2h2v-2H6zm-3 6h18v4H3v-4zm3 1v2h2v-2H6z'},
  desktop: {label: 'Desktop', category: 'Devices', path: 'M2 3h20v14H2V3zm3 3v8h14V6H5zm5 13h4v2h4v2H6v-2h4v-2z'},
  laptop: {label: 'Laptop', category: 'Devices', path: 'M4 4h16v13H4V4zm3 3v7h10V7H7zM1 19h22v2H1v-2z'},
  mobile: {label: 'Mobile device', category: 'Devices', path: 'M7 2h10a2 2 0 012 2v16a2 2 0 01-2 2H7a2 2 0 01-2-2V4a2 2 0 012-2zm1 3v13h8V5H8zm3 14h2v1h-2v-1z'},
  appliance: {label: 'Network appliance', category: 'Devices', path: 'M3 4h18a2 2 0 012 2v12a2 2 0 01-2 2H3a2 2 0 01-2-2V6a2 2 0 012-2zm2 4v8h8V8H5zm11 0h3v2h-3V8zm0 4h3v2h-3v-2z'},

  gateway: {label: 'Gateway', category: 'Network', path: 'M3 3h8v5H8v3H3V3zm10 0h8v8h-5V8h-3V3zM3 13h5v3h3v5H3v-8zm13 0h5v8h-8v-5h3v-3zM9 10h6v4H9v-4z'},
  router: {label: 'Router', category: 'Network', path: 'M2 5h20v14H2V5zm3 8h4l-2-2 1.4-1.4L13 14l-4.6 4.4L7 17l2-2H5v-2zm14-2h-4l2 2-1.4 1.4L11 10l4.6-4.4L17 7l-2 2h4v2z'},
  switch: {label: 'Network switch', category: 'Network', path: 'M2 5h20v14H2V5zm3 4v3h3V9H5zm5 0v3h3V9h-3zm5 0v3h4V9h-4zM5 14v2h14v-2H5z'},
  firewall: {label: 'Firewall', category: 'Network', path: 'M2 3h9v5H2V3zm11 0h9v5h-9V3zM2 10h6v5H2v-5zm8 0h12v5H10v-5zm-8 7h9v5H2v-5zm11 0h9v5h-9v-5z'},
  vpn: {label: 'VPN', category: 'Network', path: 'M12 2a7 7 0 017 7v3h2v10H3V12h2V9a7 7 0 017-7zm0 3a4 4 0 00-4 4v3h8V9a4 4 0 00-4-4zm-1 11v3h2v-3h-2z'},
  proxy: {label: 'Proxy', category: 'Network', path: 'M3 5h12l-3-3h4l5 5-5 5h-4l3-3H3V5zm18 14H9l3 3H8l-5-5 5-5h4l-3 3h12v4z'},
  load_balancer: {label: 'Load balancer', category: 'Network', path: 'M3 2h6v6H3V2zm12 0h6v6h-6V2zM9 16H3v6h6v-6zm12 0h-6v6h6v-6zM11 5h2v5h4v4h-2v-2H9v2H7v-4h4V5z'},

  web: {label: 'Web service', category: 'Services', path: 'M12 2a10 10 0 100 20 10 10 0 000-20zm6 6h-3a17 17 0 00-1-3 8 8 0 014 3zM12 4c1 1 2 2 2 4h-4c0-2 1-3 2-4zM4 10h4v4H4a8 8 0 010-4zm1 6h3a17 17 0 001 3 8 8 0 01-4-3zm5 0h4c0 2-1 3-2 4-1-1-2-2-2-4zm6 0h3a8 8 0 01-4 3 17 17 0 001-3z'},
  database: {label: 'Database', category: 'Services', path: 'M12 2C6 2 3 4 3 6v12c0 2 3 4 9 4s9-2 9-4V6c0-2-3-4-9-4zm0 2c4 0 7 1 7 2s-3 2-7 2-7-1-7-2 3-2 7-2zm0 16c-4 0-7-1-7-2v-2c2 1 4 2 7 2s5-1 7-2v2c0 1-3 2-7 2z'},
  dns: {label: 'DNS', category: 'Services', path: 'M12 2l9 5v10l-9 5-9-5V7l9-5zm0 4L7 9v6l5 3 5-3V9l-5-3zM9 9h6v2H9V9zm0 4h4v2H9v-2z'},
  mail: {label: 'Mail server', category: 'Services', path: 'M2 4h20v16H2V4zm3 3l7 5 7-5H5zm14 10V9l-7 5-7-5v8h14z'},
  directory: {label: 'Directory service', category: 'Services', path: 'M9 2h6v6H9V2zM2 16h6v6H2v-6zm14 0h6v6h-6v-6zM11 9h2v3h6v3h-2v-1H7v1H5v-3h6V9z'},
  file_share: {label: 'File share', category: 'Services', path: 'M2 5h8l2 3h10v12H2V5zm5 7a3 3 0 100 6 3 3 0 000-6zm10 0a3 3 0 100 6 3 3 0 000-6zm-7 2h4v2h-4v-2z'},
  storage: {label: 'Storage', category: 'Services', path: 'M4 2h16l2 6v12a2 2 0 01-2 2H4a2 2 0 01-2-2V8l2-6zm2 3L5 8h14l-1-3H6zm2 8h8v5H8v-5z'},
  certificate_authority: {label: 'Certificate authority', category: 'Services', path: 'M5 2h14v13H5V2zm3 3v2h8V5H8zm0 4v2h5V9H8zm4 5a5 5 0 015 5l-2 3-3-2-3 2-2-3a5 5 0 015-5z'},
  identity: {label: 'Identity provider', category: 'Services', path: 'M10 2a5 5 0 110 10 5 5 0 010-10zM2 22v-3c0-4 3-6 8-6 2 0 3 0 4 1l3-1 5 2v3c0 2-2 4-5 5-3-1-5-3-5-5v-2H6c-1 1-1 2-1 3v3H2zm15-7l-3 1v2c0 1 1 2 3 3 2-1 3-2 3-3v-2l-3-1z'},

  attacker: {label: 'Attacker', category: 'Security', path: 'M12 2a10 10 0 100 20 10 10 0 000-20zm0 4a6 6 0 110 12 6 6 0 010-12zm0 3a3 3 0 100 6 3 3 0 000-6zM11 2h2v4h-2V2zm0 16h2v4h-2v-4zM2 11h4v2H2v-2zm16 0h4v2h-4v-2z'},
  target: {label: 'Target', category: 'Security', path: 'M13 2v3a7 7 0 016 6h3v2h-3a7 7 0 01-6 6v3h-2v-3a7 7 0 01-6-6H2v-2h3a7 7 0 016-6V2h2zm-1 6a4 4 0 100 8 4 4 0 000-8zm0 2a2 2 0 110 4 2 2 0 010-4z'},
  siem: {label: 'SIEM', category: 'Security', path: 'M3 3h18v18H3V3zm3 3v5h5V6H6zm7 0v2h5V6h-5zm0 4v2h5v-2h-5zm-7 3v5h12v-2H9v-3H6z'},
  ids: {label: 'IDS / IPS', category: 'Security', path: 'M12 2l9 4v6c0 5-4 9-9 10-5-1-9-5-9-10V6l9-4zm-1 5v7h2V7h-2zm0 9v2h2v-2h-2z'},
  monitoring: {label: 'Monitoring', category: 'Security', path: 'M2 4h20v16H2V4zm3 10h3l2-6 3 8 2-5 2 3h2v2h-3l-1-1-2 6-3-8-1 3H5v-2z'},
  logging: {label: 'Logging', category: 'Security', path: 'M5 2h10l4 4v16H5V2zm9 2v4h4l-4-4zM8 11v2h8v-2H8zm0 4v2h8v-2H8zm0 4v1h5v-1H8z'},
  honeypot: {label: 'Honeypot', category: 'Security', path: 'M7 2h10l2 5-2 3v10a2 2 0 01-2 2H9a2 2 0 01-2-2V10L5 7l2-5zm2 3L8 7l2 2h4l2-2-1-2H9zm1 8v5h4v-5h-4z'},
  malware: {label: 'Malware', category: 'Security', path: 'M9 2h2v3h2V2h2v3a5 5 0 012 2l2-2 2 2-3 3v2h4v2h-4v2l3 3-2 2-2-2a5 5 0 01-10 0l-2 2-2-2 3-3v-2H2v-2h4v-2L3 7l2-2 2 2a5 5 0 012-2V2zm1 8v6h4v-6h-4z'},
  bastion: {label: 'Bastion host', category: 'Security', path: 'M3 2h4v4h3V2h4v4h3V2h4v20H3V2zm5 9v3h8v-3H8zm2 5v6h4v-6h-4z'},
  vulnerable: {label: 'Vulnerable host', category: 'Security', path: 'M12 2l9 4v6c0 5-4 9-9 10-2-1-4-2-6-4l4-5-3-3 7-7-2-1zm2 8l-3 3 2 2-3 4c1 .5 1 .7 2 1 4-1 7-4 7-8V8l-5 2z'},

  cloud: {label: 'Cloud workload', category: 'Workloads', path: 'M18 19H6a5 5 0 01-1-10 8 8 0 0115-1 5.5 5.5 0 01-2 11z'},
  container: {label: 'Container', category: 'Workloads', path: 'M12 2l9 5v10l-9 5-9-5V7l9-5zm0 3L7 8l5 3 5-3-5-3zm-6 6v5l5 3v-5l-5-3zm7 8l5-3v-5l-5 3v5z'},
  kubernetes: {label: 'Kubernetes', category: 'Workloads', path: 'M12 2l9 5v10l-9 5-9-5V7l9-5zm0 4a6 6 0 100 12 6 6 0 000-12zm-1 1h2v3l3-2 1 2-3 2 3 2-1 2-3-2v3h-2v-3l-3 2-1-2 3-2-3-2 1-2 3 2V7z'},
  backup: {label: 'Backup', category: 'Workloads', path: 'M12 3a9 9 0 019 9h3l-4 5-4-5h3a7 7 0 10-2 5l2 2a10 10 0 11-7-17v1zm-1 4h2v5l4 2-1 2-5-3V7z'},
  git: {label: 'Git repository', category: 'Workloads', path: 'M12 2L2 12l10 10 10-10L12 2zM9 7a2 2 0 012 3l3 3a2 2 0 011 0V9h2v4a2 2 0 11-2 2l-4-4v4a2 2 0 11-2 0V10a2 2 0 010-3z'},
  cicd: {label: 'CI / CD pipeline', category: 'Workloads', path: 'M12 2l2 3 4-1 2 3-3 3 1 4-3 2-3-2-3 2-3-2 1-4-3-3 2-3 4 1 2-3zm0 6a4 4 0 100 8 4 4 0 000-8zm-1 1l4 3-4 3V9z'},

  linux: {label: 'Linux', category: 'Platforms', path: 'M12 2c-3 0-5 3-5 7 0 2-2 4-2 7l3 1-2 4h4l2-2 2 2h4l-2-4 3-1c0-3-2-5-2-7 0-4-2-7-5-7zm-2 7a1 1 0 110-2 1 1 0 010 2zm4 0a1 1 0 110-2 1 1 0 010 2zm-4 4h4l-2 2-2-2z'},
  ubuntu: {label: 'Ubuntu', category: 'Platforms', path: 'M7 3a3 3 0 110 6 3 3 0 010-6zm10 1a3 3 0 110 6 3 3 0 010-6zM4 13a3 3 0 110 6 3 3 0 010-6zm5-2a5 5 0 007 4l2 2a8 8 0 01-11-5l2-1zm-1 8a8 8 0 009-7l3-1A11 11 0 018 22v-3z'},
  debian: {label: 'Debian', category: 'Platforms', path: 'M13 2c5 0 8 3 8 7 0 5-5 8-9 8-3 0-6-2-6-5 0-2 2-4 5-4 2 0 4 1 4 3 0 1-1 2-3 2-2 0-3-1-3-2 0-1 1-2 2-2 5 0 6 6 2 9-5 4-13 0-13-7C1 6 6 2 13 2z'},
  kali: {label: 'Kali Linux', category: 'Platforms', path: 'M2 19L8 4l4 6 9-7-5 10 6 6h-7l-3-4-2 4H2zm7-6l-1 3h3l-2-3zm7 3h3l-2-2-1 2z'},
  redhat: {label: 'Red Hat', category: 'Platforms', path: 'M7 5c1-3 3-4 6-3l2 1 3 1 2 6c2 1 3 2 3 4 0 4-5 7-11 7S1 18 1 14c0-2 2-4 5-5l1-4zm0 7c1 2 3 3 6 3s5-1 7-3c-4 1-9 1-13 0z'},
  windows: {label: 'Windows', category: 'Platforms', path: 'M2 4l9-1v9H2V4zm11-1l9-1v10h-9V3zM2 14h9v9l-9-1v-8zm11 0h9v10l-9-1v-9z'},
  macos: {label: 'macOS', category: 'Platforms', path: 'M15 2c0 2-1 4-3 5-1-2 0-4 3-5zm4 12c0-4 3-5 3-5-2-3-5-3-7-2-1 0-2 1-3 1s-2-1-4-1c-3 0-6 3-6 7 0 3 2 8 5 8 1 0 2-1 4-1 1 0 2 1 4 1 3 0 5-4 6-7-1 0-2-1-2-1z'},
  freebsd: {label: 'FreeBSD', category: 'Platforms', path: 'M5 2l4 5h6l4-5 1 7a9 9 0 11-16 0l1-7zm7 7a5 5 0 100 10 5 5 0 000-10zm-3 2l2 2-2 2-1-2 1-2zm6 0l1 2-1 2-2-2 2-2z'},
  opnsense: {label: 'OPNsense', category: 'Platforms', path: 'M12 2l9 4v6c0 5-4 9-9 10-5-1-9-5-9-10V6l9-4zm0 5a5 5 0 100 10 5 5 0 000-10zm0 3a2 2 0 110 4 2 2 0 010-4z'},
  pfsense: {label: 'pfSense', category: 'Platforms', path: 'M12 2l9 4v6c0 5-4 9-9 10-5-1-9-5-9-10V6l9-4zM8 8v9h3v-3h2a3 3 0 000-6H8zm3 2h2a1 1 0 010 2h-2v-2z'},

  aws: {label: 'Amazon Web Services', category: 'Cloud providers', path: 'M6 5h5l3 10h-3l-1-3H7l-1 3H3L6 5zm2 3l-1 2h2L8 8zm7-3h3l1 6 1-6h3l-2 10h-4L15 5zM4 18c5 2 11 2 16-1l1 2c-6 4-13 4-18 1l1-2z'},
  azure: {label: 'Microsoft Azure', category: 'Cloud providers', path: 'M10 2h7L9 16H3L10 2zm2 7l4 13h6L16 9h-4zm-3 9h8l1 4H7l2-4z'},
  gcp: {label: 'Google Cloud', category: 'Cloud providers', path: 'M8 18H6a5 5 0 01-1-10 8 8 0 0114-2l-3 2a5 5 0 00-8 1l2 3H8a3 3 0 000 6zm3 0h7a3 3 0 000-6h-3l-2-3-4 2 2 3h7a1 1 0 010 2h-7v2z'},
});

export const PLANNER_ICON_OPTIONS = Object.freeze(Object.entries(PLANNER_ICONS).map(([value, icon]) => ({value, label: icon.label})));

const ICON_CATEGORY_ORDER = ['Devices', 'Network', 'Services', 'Security', 'Workloads', 'Platforms', 'Cloud providers'];
export const PLANNER_ICON_GROUPS = Object.freeze(ICON_CATEGORY_ORDER.map(label => Object.freeze({
  label,
  options: Object.freeze(Object.entries(PLANNER_ICONS)
    .filter(([, icon]) => icon.category === label)
    .map(([value, icon]) => Object.freeze({value, label: icon.label}))),
})));

const VIEW_BOX = /^-?\d+(?:\.\d+)?(?:\s+-?\d+(?:\.\d+)?){3}$/;

export function resolvePlannerIcon(value) {
  if (typeof value === 'string' && PLANNER_ICONS[value]) {
    return {path: PLANNER_ICONS[value].path, viewBox: '0 0 24 24'};
  }
  if (value && typeof value === 'object' && typeof value.svg_path === 'string' && value.svg_path.trim().startsWith('M')) {
    const viewBox = typeof value.viewbox === 'string' && VIEW_BOX.test(value.viewbox.trim()) ? value.viewbox.trim() : '0 0 24 24';
    return {path: value.svg_path.trim(), viewBox};
  }
  return {path: PLANNER_ICONS.server.path, viewBox: '0 0 24 24'};
}

function baseIconDefinition(machine, baseTypes) {
  const baseIcon = (baseTypes || []).find(base => base.id === machine?.base_type)?.icon;
  return resolvePlannerIcon(machine?.icon || baseIcon || 'server');
}

export function machineIconPair(type, machine, baseTypes) {
  const automaticPrimary = {gateway: 'router', firewall: 'firewall', vm: 'server'}[type] || 'server';
  return {
    primary: resolvePlannerIcon(machine?.primary_icon || automaticPrimary),
    secondary: baseIconDefinition(machine, baseTypes),
  };
}

export function setMachineIconOverride(machine, field, value) {
  if (!['primary_icon', 'icon'].includes(field)) return machine;
  if (typeof value === 'string' && PLANNER_ICONS[value]) machine[field] = value;
  else delete machine[field];
  return machine;
}
