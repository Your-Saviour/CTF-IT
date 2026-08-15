export function topologyNodeClass(node) {
  return ['topo-node', node.type, node.selected && 'selected', node.invalid && 'invalid'].filter(Boolean).join(' ');
}

export function topologyLinkClass(link) {
  return `topo-link${link.source.selected || link.target.selected ? ' selected-adjacent' : ''}`;
}

const ROOT_X = 120;
const ROOT_Y = 70;
const HORIZONTAL_GAP = 190;
const VERTICAL_GAP = 110;
const NODE_WIDTH = 140;
const NODE_HEIGHT = 48;
const COLLISION_PADDING = 24;

function balancedSlot(index) {
  if (index === 0) return 0;
  const distance = Math.ceil(index / 2);
  return index % 2 ? distance : -distance;
}

export function calculateHierarchicalLayout(graph, savedLayout = {version: 1, nodes: {}}) {
  const byId = new Map(graph.map(node => [node.id, node]));
  const siblingIndex = new Map();
  const siblingCounts = new Map();
  for (const node of graph) {
    const parent = node.parent && byId.has(node.parent) ? node.parent : null;
    const count = siblingCounts.get(parent) || 0;
    siblingIndex.set(node.id, count);
    siblingCounts.set(parent, count + 1);
  }

  const nodes = {};
  for (const node of graph) {
    const saved = savedLayout?.nodes?.[node.id];
    if (Number.isFinite(saved?.x) && Number.isFinite(saved?.y)) nodes[node.id] = {x: saved.x, y: saved.y};
  }
  const occupied = () => Object.values(nodes);
  const overlaps = candidate => occupied().some(position =>
    Math.abs(position.x - candidate.x) < NODE_WIDTH + COLLISION_PADDING
    && Math.abs(position.y - candidate.y) < NODE_HEIGHT + COLLISION_PADDING
  );
  let added = false;

  function place(node) {
    if (nodes[node.id]) return nodes[node.id];
    const parent = node.parent ? byId.get(node.parent) : null;
    const parentPosition = parent ? place(parent) : null;
    const preferred = siblingIndex.get(node.id) || 0;
    const candidates = [preferred];
    for (let index = 0; candidates.length < graph.length * 2 + 4; index++) {
      if (!candidates.includes(index)) candidates.push(index);
    }
    let position;
    for (const index of candidates) {
      position = {
        x: (parentPosition?.x ?? ROOT_X) + balancedSlot(index) * HORIZONTAL_GAP,
        y: parentPosition ? parentPosition.y + VERTICAL_GAP : ROOT_Y,
      };
      if (!overlaps(position)) break;
    }
    nodes[node.id] = position;
    added = true;
    return position;
  }

  graph.forEach(place);
  return {version: 1, nodes, added};
}

export function createPlannerCanvas(svgElement, callbacks = {}) {
  const svg = d3.select(svgElement);
  const scene = svg.append('g');
  let graph = [];
  let currentLayout = {version: 1, nodes: {}};
  let pendingLayoutKey = null;

  const zoom = d3.zoom()
    .scaleExtent([.35, 2.4])
    .on('zoom', event => scene.attr('transform', event.transform));
  svg.call(zoom);

  function render(nextGraph, layout = {version: 1, nodes: {}}) {
    graph = nextGraph;
    currentLayout = {version: 1, nodes: {...layout.nodes}};
    scene.selectAll('*').remove();

    const completed = calculateHierarchicalLayout(graph, currentLayout);
    currentLayout = {version: 1, nodes: completed.nodes};
    const nodes = graph.map(row => ({
      ...row,
      ...currentLayout.nodes[row.id],
    }));
    const byId = new Map(nodes.map(row => [row.id, row]));
    const links = nodes
      .filter(row => row.parent && byId.has(row.parent))
      .map(row => ({source: byId.get(row.parent), target: row}));
    const linkSelection = scene.selectAll('line')
      .data(links)
      .join('line')
      .attr('class', topologyLinkClass);

    function updateLinks() {
      linkSelection
        .attr('x1', d => d.source.x)
        .attr('y1', d => d.source.y)
        .attr('x2', d => d.target.x)
        .attr('y2', d => d.target.y);
    }
    updateLinks();

    let groups = scene.selectAll('g.topo-node')
      .data(nodes, d => d.id)
      .join('g')
      .attr('class', topologyNodeClass)
      .attr('data-node-id', d => d.id)
      .attr('role', 'button')
      .attr('tabindex', 0)
      .attr('aria-label', d => `${d.type}: ${d.label}`)
      .attr('transform', d => `translate(${d.x},${d.y})`)
      .on('click', (_, d) => callbacks.onSelect?.(d.id))
      .on('keydown', (event, d) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          callbacks.onSelect?.(d.id);
        }
      });

    if (!callbacks.readOnly) {
      groups = groups.call(d3.drag()
        .on('drag', function(event, d) {
          d.x = event.x;
          d.y = event.y;
          d3.select(this).attr('transform', `translate(${d.x},${d.y})`);
          updateLinks();
        })
        .on('end', (_, d) => {
          currentLayout.nodes[d.id] = {x: d.x, y: d.y};
          callbacks.onLayoutChange?.(structuredClone(currentLayout));
        }));
    }

    groups.append('rect')
      .attr('class', 'node-body')
      .attr('x', -70)
      .attr('y', -24)
      .attr('width', 140)
      .attr('height', 48)
      .attr('rx', 7);
    groups.filter(d => d.type === 'vm')
      .append('rect')
      .attr('class', 'vm-edge')
      .attr('x', -70)
      .attr('y', -24)
      .attr('width', 4)
      .attr('height', 48);
    groups.append('text').attr('text-anchor', 'middle').attr('y', 4).text(d => d.label);

    if (completed.added && !callbacks.readOnly) {
      const key = JSON.stringify(completed.nodes);
      if (pendingLayoutKey !== key) {
        pendingLayoutKey = key;
        queueMicrotask(() => {
          if (pendingLayoutKey !== key) return;
          pendingLayoutKey = null;
          callbacks.onLayoutChange?.({version: 1, nodes: structuredClone(completed.nodes)});
        });
      }
    }
  }

  function fit() {
    const bounds = scene.node().getBBox();
    const width = svgElement.clientWidth || 800;
    const height = svgElement.clientHeight || 600;
    if (!bounds.width || !bounds.height) return;
    const scale = Math.min(1.5, .9 / Math.max(bounds.width / width, bounds.height / height));
    const transform = d3.zoomIdentity
      .translate(width / 2 - scale * (bounds.x + bounds.width / 2), height / 2 - scale * (bounds.y + bounds.height / 2))
      .scale(scale);
    svg.transition().duration(200).call(zoom.transform, transform);
  }

  function resetLayout() {
    const completed = calculateHierarchicalLayout(graph, {version: 1, nodes: {}});
    currentLayout = {version: 1, nodes: completed.nodes};
    render(graph, currentLayout);
    callbacks.onLayoutChange?.(structuredClone(currentLayout));
    fit();
  }

  function focusNode(id) {
    scene.select(`[data-node-id="${CSS.escape(id)}"]`).node()?.focus();
  }

  function destroy() {
    svg.on('.zoom', null);
    scene.remove();
  }

  return {render, fit, resetLayout, focusNode, destroy};
}
