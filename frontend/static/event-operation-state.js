export function nextId(prefix, rows) {
  const used = new Set(rows.map(row => row.id));
  let index = 1;
  while (used.has(`${prefix}-${index}`)) index += 1;
  return `${prefix}-${index}`;
}

const clone = value => JSON.parse(JSON.stringify(value));

export function addNode(state, template, position = {x: 240, y: 160}) {
  const next = clone(state);
  next.nodes.push({
    id: nextId(template.type, next.nodes), type: template.type,
    label: template.label || template.type, x: position.x, y: position.y,
    disabled: false, config: clone(template.config || {}),
  });
  return next;
}

export function addEdge(state, source, target, condition = 'success') {
  if (source === target) throw new Error('Edge endpoints must be different');
  if (!['success', 'failure', 'always'].includes(condition)) throw new Error('Invalid edge condition');
  const ids = new Set(state.nodes.map(node => node.id));
  if (!ids.has(source) || !ids.has(target)) throw new Error('Edge endpoint does not exist');
  if (state.edges.some(edge => edge.source === source && edge.target === target && edge.condition === condition)) {
    throw new Error('Typed edge already exists');
  }
  const next = clone(state);
  next.edges.push({id: nextId('edge', next.edges), source, target, condition, label: ''});
  return next;
}

export function deleteSelection(state, selection) {
  const next = clone(state);
  if (!selection) return next;
  if (selection.kind === 'node') {
    if (['start', 'finish'].includes(next.nodes.find(node => node.id === selection.id)?.type)) return next;
    next.nodes = next.nodes.filter(node => node.id !== selection.id);
    next.edges = next.edges.filter(edge => edge.source !== selection.id && edge.target !== selection.id);
  } else if (selection.kind === 'edge') {
    next.edges = next.edges.filter(edge => edge.id !== selection.id);
  }
  return next;
}

export function moveNode(state, nodeId, position) {
  const next = clone(state);
  const node = next.nodes.find(row => row.id === nodeId);
  if (!node) throw new Error('Node does not exist');
  node.x = Math.max(0, Number(position.x) || 0);
  node.y = Math.max(0, Number(position.y) || 0);
  return next;
}

export function autoArrange(state) {
  const next = clone(state);
  const ids = next.nodes.map(node => node.id);
  const incoming = new Map(ids.map(id => [id, 0]));
  const outgoing = new Map(ids.map(id => [id, []]));
  next.edges.forEach(edge => {
    if (!incoming.has(edge.target) || !outgoing.has(edge.source)) return;
    incoming.set(edge.target, incoming.get(edge.target) + 1);
    outgoing.get(edge.source).push(edge.target);
  });
  const queue = ids.filter(id => incoming.get(id) === 0).sort().map(id => [id, 0]);
  const depth = new Map();
  while (queue.length) {
    const [id, level] = queue.shift();
    depth.set(id, Math.max(depth.get(id) || 0, level));
    [...outgoing.get(id)].sort().forEach(target => {
      incoming.set(target, incoming.get(target) - 1);
      if (incoming.get(target) === 0) queue.push([target, level + 1]);
    });
  }
  ids.filter(id => !depth.has(id)).sort().forEach((id, index) => depth.set(id, index));
  const levels = new Map();
  next.nodes.sort((a,b) => a.id.localeCompare(b.id)).forEach(node => {
    const level = depth.get(node.id); const row = levels.get(level) || 0;
    node.x = 80 + level * 240; node.y = 80 + row * 140; levels.set(level, row + 1);
  });
  return next;
}
