export const PLANNER_ICONS = Object.freeze({
  server: {label: 'Server', path: 'M3 3h18v4H3V3zm0 7h18v4H3v-4zm0 7h18v4H3v-4z'},
  desktop: {label: 'Desktop', path: 'M3 3h18v13H3V3zm8 14h2v2h4v2H7v-2h4v-2z'},
  laptop: {label: 'Laptop', path: 'M4 5h16v11H4V5zm-2 13h20v2H2v-2z'},
  ubuntu: {label: 'Ubuntu', path: 'M4 4h16a1 1 0 011 1v14a1 1 0 01-1 1H4a1 1 0 01-1-1V5a1 1 0 011-1zm2 5l2 2-2 2 1 1 3-3-3-3-1 1zm6 5h4v2h-4v-2z'},
  linux: {label: 'Linux', path: 'M4 4h16a1 1 0 011 1v14a1 1 0 01-1 1H4a1 1 0 01-1-1V5a1 1 0 011-1zm2 5l2 2-2 2 1 1 3-3-3-3-1 1zm6 5h4v2h-4v-2z'},
  debian: {label: 'Debian', path: 'M4 4h16a1 1 0 011 1v14a1 1 0 01-1 1H4a1 1 0 01-1-1V5a1 1 0 011-1zm2 5l2 2-2 2 1 1 3-3-3-3-1 1zm6 5h4v2h-4v-2z'},
  kali: {label: 'Kali', path: 'M12 2C7 2 4 5.5 4 9c0 2.6 1.4 4.8 3.5 6L7 18h1v2h8v-2h1l-.5-3C18.6 13.8 20 11.6 20 9c0-3.5-3-7-8-7zm-2 9a1.5 1.5 0 110-3 1.5 1.5 0 010 3zm4 0a1.5 1.5 0 110-3 1.5 1.5 0 010 3z'},
  windows: {label: 'Windows', path: 'M3 3h9v9H3V3zm10 0h9v9h-9V3zM3 13h9v9H3v-9zm10 0h9v9h-9v-9z'},
  router: {label: 'Router', path: 'M1 9l2 2c4.97-4.97 13.03-4.97 18 0l2-2C16.93 2.93 7.08 2.93 1 9zm8 8l3 3 3-3a4.24 4.24 0 00-6 0zm-4-4l2 2a7.07 7.07 0 0110 0l2-2a9.9 9.9 0 00-14 0z'},
  firewall: {label: 'Firewall', path: 'M3 3h8v4H3V3zm10 0h8v4h-8V3zM3 9h5v4H3V9zm7 0h11v4H10V9zM3 15h8v6H3v-6zm10 0h8v6h-8v-6z'},
  attacker: {label: 'Attacker', path: 'M12 2a10 10 0 100 20 10 10 0 000-20zm0 4a6 6 0 110 12 6 6 0 010-12zm0 3a3 3 0 100 6 3 3 0 000-6zM11 2h2v4h-2V2zm0 16h2v4h-2v-4zM2 11h4v2H2v-2zm16 0h4v2h-4v-2z'},
  database: {label: 'Database', path: 'M12 2C6.5 2 3 3.8 3 6v12c0 2.2 3.5 4 9 4s9-1.8 9-4V6c0-2.2-3.5-4-9-4zm0 2c4.4 0 7 1.3 7 2s-2.6 2-7 2-7-1.3-7-2 2.6-2 7-2zm0 16c-4.4 0-7-1.3-7-2v-2.5c1.6 1 4.1 1.5 7 1.5s5.4-.5 7-1.5V18c0 .7-2.6 2-7 2z'},
  web: {label: 'Web', path: 'M12 2a10 10 0 100 20 10 10 0 000-20zm6.9 6h-3a15.7 15.7 0 00-1.4-3.6A8.1 8.1 0 0118.9 8zM12 4c.8 1 1.5 2.3 1.9 4h-3.8c.4-1.7 1.1-3 1.9-4zM4.3 14a8.3 8.3 0 010-4h3.4a16.5 16.5 0 000 4H4.3zm.8 2h3a15.7 15.7 0 001.4 3.6A8.1 8.1 0 015.1 16z'},
  dns: {label: 'DNS', path: 'M12 2l9 5v10l-9 5-9-5V7l9-5zm0 3.3L6 8.6v6.8l6 3.3 6-3.3V8.6l-6-3.3zM8 10h8v2H8v-2zm0 4h5v2H8v-2z'},
  mail: {label: 'Mail', path: 'M2 4h20v16H2V4zm2 3v11h16V7l-8 6-8-6zm1-1l7 5 7-5H5z'},
  directory: {label: 'Directory', path: 'M10 3h4v4h-4V3zM4 17h4v4H4v-4zm12 0h4v4h-4v-4zM6 15v-4h5V9h2v2h5v4h-2v-2h-8v2H6z'},
  cloud: {label: 'Cloud', path: 'M19 18H6a4 4 0 01-.5-8A7 7 0 0119 8.5a4.8 4.8 0 010 9.5z'},
  container: {label: 'Container', path: 'M12 2l9 5v10l-9 5-9-5V7l9-5zm0 2.3L6 7.6l6 3.3 6-3.3-6-3.3zM5 9.3v6.5l6 3.3v-6.5L5 9.3zm8 9.8l6-3.3V9.3l-6 3.3v6.5z'},
  kubernetes: {label: 'Kubernetes', path: 'M12 2l8.7 5v10L12 22l-8.7-5V7L12 2zm0 4a6 6 0 100 12 6 6 0 000-12zm-1 1h2v3.3l2.9-1.7 1 1.8-2.9 1.6 2.9 1.6-1 1.8-2.9-1.7V17h-2v-3.3l-2.9 1.7-1-1.8L10 12 7.1 10.4l1-1.8 2.9 1.7V7z'},
  storage: {label: 'Storage', path: 'M5 2h11l3 3v17H5V2zm2 2v7h10V6l-2-2H7zm1 10h8v6H8v-6z'},
  monitoring: {label: 'Monitoring', path: 'M3 3h18v18H3V3zm3 13h2l2-5 3 4 2-7 3 5h1v2h-2l-2-3-2 7-3-4-1 3H6v-2z'},
});

export const PLANNER_ICON_OPTIONS = Object.freeze(Object.entries(PLANNER_ICONS).map(([value, icon]) => ({value, label: icon.label})));

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

export function machineIconDefinition(machine, baseTypes) {
  const baseIcon = (baseTypes || []).find(base => base.id === machine?.base_type)?.icon;
  return resolvePlannerIcon(machine?.icon || baseIcon || 'server');
}

export function setMachineIconOverride(machine, value) {
  if (typeof value === 'string' && PLANNER_ICONS[value]) machine.icon = value;
  else delete machine.icon;
  return machine;
}
