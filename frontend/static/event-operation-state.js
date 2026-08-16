export function nextId(prefix, rows) {
  const used = new Set(rows.map(row => row.id));
  let index = 1;
  while (used.has(`${prefix}-${index}`)) index += 1;
  return `${prefix}-${index}`;
}

const clone = value => JSON.parse(JSON.stringify(value));

export function isTriggerType(type) {
  return ['manual_trigger', 'event_start_trigger', 'scheduled_trigger'].includes(type);
}

export function replaceTrigger(state, template) {
  if (!isTriggerType(template.type)) throw new Error('Replacement must be a trigger');
  const next = clone(state);
  const triggers = next.nodes.filter(node => isTriggerType(node.type));
  if (triggers.length !== 1) throw new Error('Graph must contain exactly one trigger to replace');
  const current = triggers[0];
  Object.assign(current, {
    type: template.type,
    label: template.label || template.type,
    config: clone(template.config || {}),
  });
  return next;
}

export function addNode(state, template, position = {x: 240, y: 160}) {
  const next = clone(state);
  next.nodes.push({
    id: nextId(template.type, next.nodes), type: template.type,
    label: template.label || template.type, x: position.x, y: position.y,
    disabled: false, config: clone(template.config || {}),
  });
  return next;
}

export function connectionError(state, source, target, condition = 'success') {
  if (source === target) return 'Edge endpoints must be different';
  if (!['success', 'failure', 'always'].includes(condition)) return 'Invalid edge condition';
  const ids = new Set(state.nodes.map(node => node.id));
  if (!ids.has(source) || !ids.has(target)) return 'Edge endpoint does not exist';
  if (isTriggerType(state.nodes.find(node => node.id === target)?.type)) return 'Trigger nodes cannot receive connections';
  if (state.edges.some(edge => edge.source === source && edge.target === target && edge.condition === condition)) {
    return 'Typed edge already exists';
  }
  const outgoing = new Map(state.nodes.map(node => [node.id, []]));
  state.edges.forEach(edge => outgoing.get(edge.source)?.push(edge.target));
  const pending = [target], visited = new Set();
  while (pending.length) {
    const id = pending.pop();
    if (id === source) return 'Connection would create a cycle';
    if (visited.has(id)) continue;
    visited.add(id);
    pending.push(...(outgoing.get(id) || []));
  }
  return null;
}

export function addEdge(state, source, target, condition = 'success') {
  const error = connectionError(state, source, target, condition);
  if (error) throw new Error(error);
  const next = clone(state);
  next.edges.push({id: nextId('edge', next.edges), source, target, condition, label: ''});
  return next;
}

export function insertConnectedNode(state, source, condition, template, position) {
  const withNode = addNode(state, template, position);
  const nodeId = withNode.nodes.at(-1).id;
  const next = addEdge(withNode, source, nodeId, condition);
  return {state: next, nodeId, edgeId: next.edges.at(-1).id};
}

export function deleteSelection(state, selection) {
  const next = clone(state);
  if (!selection) return next;
  if (selection.kind === 'node') {
    const type = next.nodes.find(node => node.id === selection.id)?.type;
    if (type === 'finish' || isTriggerType(type)) return next;
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
  node.x = Number(position.x) || 0;
  node.y = Number(position.y) || 0;
  return next;
}

export function moveNodes(state, nodeIds, delta) {
  const selected = new Set(nodeIds);
  const next = clone(state);
  next.nodes.forEach(node => {
    if (!selected.has(node.id)) return;
    node.x += Number(delta.x) || 0;
    node.y += Number(delta.y) || 0;
  });
  return next;
}

export function duplicateNodes(state, nodeIds, offset = {x: 40, y: 40}) {
  const selected = new Set(nodeIds), next = clone(state), mapping = new Map(), created = [];
  state.nodes.filter(node => selected.has(node.id) && !isTriggerType(node.type)).forEach(node => {
    const id = nextId(node.type, next.nodes);
    mapping.set(node.id, id);
    next.nodes.push({...clone(node), id, x:Math.max(0,node.x+(Number(offset.x)||0)), y:Math.max(0,node.y+(Number(offset.y)||0))});
    created.push(id);
  });
  state.edges.filter(edge => mapping.has(edge.source) && mapping.has(edge.target)).forEach(edge => {
    next.edges.push({...clone(edge), id:nextId('edge',next.edges), source:mapping.get(edge.source), target:mapping.get(edge.target)});
  });
  return {state: next, nodeIds: created};
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
