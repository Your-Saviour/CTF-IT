export function topologyNodeClass(node) {
  return ['topo-node', node.type, node.selected && 'selected', node.invalid && 'invalid'].filter(Boolean).join(' ');
}

export function topologyLinkClass(link) {
  return `topo-link${link.source.selected || link.target.selected ? ' selected-adjacent' : ''}`;
}

export function topologyNodePresentation(node) {
  return ['gateway', 'firewall', 'vm'].includes(node.type) ? 'machine' : 'structural';
}

export const MACHINE_ICON_GEOMETRY = Object.freeze({
  secondarySize: 24,
  secondaryX: -2,
  secondaryY: -7,
});

export const ZONE_CONTAINER_GEOMETRY = Object.freeze({
  headerHeight: 36,
  padding: 20,
  machineWidth: 80,
  machineHeight: 72,
  columnGap: 24,
  rowGap: 24,
  minWidth: 280,
  minHeight: 190,
});

export function zoneChildren(graph, zoneId) {
  return graph.filter(node => node.parent === zoneId && ['vm', 'firewall'].includes(node.type));
}

export function calculateZoneBounds(zone, children) {
  const geometry = ZONE_CONTAINER_GEOMETRY;
  const requiredWidth = children.reduce((width, child) => Math.max(
    width,
    child.x + geometry.machineWidth / 2 + geometry.padding - zone.x,
  ), geometry.minWidth);
  const requiredHeight = children.reduce((height, child) => Math.max(
    height,
    child.y + geometry.machineHeight / 2 + geometry.padding - zone.y,
  ), geometry.minHeight);
  return {x: zone.x, y: zone.y, width: requiredWidth, height: requiredHeight};
}

export function arrangeZoneChildren(zone, children) {
  if (!children.length) return {};
  const geometry = ZONE_CONTAINER_GEOMETRY;
  const columns = Math.ceil(Math.sqrt(children.length));
  return Object.fromEntries(children.map((child, index) => [child.id, {
    x: zone.x + geometry.padding + geometry.machineWidth / 2
      + (index % columns) * (geometry.machineWidth + geometry.columnGap),
    y: zone.y + geometry.headerHeight + geometry.padding + geometry.machineHeight / 2
      + Math.floor(index / columns) * (geometry.machineHeight + geometry.rowGap),
  }]));
}

export function arrangedZoneLayout(graph, layout, zoneId) {
  const zone = graph.find(node => node.id === zoneId);
  if (!isZoneContainer(zone)) return structuredClone(layout);
  const positionedZone = {...zone, ...layout.nodes?.[zoneId]};
  if (!Number.isFinite(positionedZone.x) || !Number.isFinite(positionedZone.y)) return structuredClone(layout);
  const children = zoneChildren(graph, zoneId);
  const arranged = arrangeZoneChildren(positionedZone, children);
  const nodes = structuredClone(layout.nodes || {});
  for (const [id, position] of Object.entries(arranged)) nodes[id] = position;
  return {version: 1, nodes};
}

export function translateZoneLayout(layout, zoneId, childIds, dx, dy) {
  const nodes = structuredClone(layout?.nodes || {});
  for (const id of [zoneId, ...childIds]) {
    const position = nodes[id];
    if (Number.isFinite(position?.x) && Number.isFinite(position?.y)) {
      nodes[id] = {x: position.x + dx, y: position.y + dy};
    }
  }
  return {version: 1, nodes};
}

export function topologyLinks(nodes) {
  const byId = new Map(nodes.map(node => [node.id, node]));
  return nodes
    .filter(node => {
      const parent = byId.get(node.parent);
      return parent && !(isZoneContainer(parent) && ['vm', 'firewall'].includes(node.type));
    })
    .map(node => ({source: byId.get(node.parent), target: node}));
}

export function linkEndpoints(link, containerBounds = new Map()) {
  const sourceBounds = containerBounds.get(link.source.id);
  const targetBounds = containerBounds.get(link.target.id);
  const sourcePoint = sourceBounds
    ? nearestBoundaryPoint(sourceBounds, link.target)
    : {x: link.source.x, y: link.source.y};
  const targetPoint = targetBounds
    ? nearestBoundaryPoint(targetBounds, link.source)
    : {x: link.target.x, y: link.target.y};
  return {x1: sourcePoint.x, y1: sourcePoint.y, x2: targetPoint.x, y2: targetPoint.y};
}

function isZoneContainer(node) {
  return ['zone', 'firewall-zone'].includes(node?.type);
}

function nearestBoundaryPoint(bounds, point) {
  return {
    x: Math.max(bounds.x, Math.min(bounds.x + bounds.width, point.x)),
    y: Math.max(bounds.y, Math.min(bounds.y + bounds.height, point.y)),
  };
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
    const preferredSlot = balancedSlot(preferred);
    const candidates = Array.from({length: graph.length * 2 + 4}, (_, index) => index)
      .sort((left, right) => {
        const leftSlot = balancedSlot(left), rightSlot = balancedSlot(right);
        return Math.abs(leftSlot - preferredSlot) - Math.abs(rightSlot - preferredSlot)
          || Math.abs(leftSlot) - Math.abs(rightSlot)
          || left - right;
      });
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
    pendingLayoutKey = null;
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
    const links = topologyLinks(nodes);
    const linkLayer = scene.append('g').attr('class', 'topology-links');
    const containerLayer = scene.append('g').attr('class', 'topology-containers');
    const structuralLayer = scene.append('g').attr('class', 'topology-structures');
    const machineLayer = scene.append('g').attr('class', 'topology-machines');
    const linkSelection = linkLayer.selectAll('line')
      .data(links)
      .join('line')
      .attr('class', topologyLinkClass);

    function containerBounds() {
      return new Map(nodes.filter(isZoneContainer).map(zone => [
        zone.id,
        calculateZoneBounds(zone, zoneChildren(nodes, zone.id)),
      ]));
    }

    function updateLinks() {
      const bounds = containerBounds();
      linkSelection
        .attr('x1', d => linkEndpoints(d, bounds).x1)
        .attr('y1', d => linkEndpoints(d, bounds).y1)
        .attr('x2', d => linkEndpoints(d, bounds).x2)
        .attr('y2', d => linkEndpoints(d, bounds).y2);
    }
    updateLinks();

    const containers = containerLayer.selectAll('g.zone-container')
      .data(nodes.filter(isZoneContainer), d => d.id)
      .join('g')
      .attr('class', d => [
        'zone-container',
        d.team && `team-${d.team}`,
        d.systemManaged && 'system-managed',
        d.selected && 'selected',
        d.invalid && 'invalid',
      ].filter(Boolean).join(' '))
      .attr('data-node-id', d => d.id)
      .attr('role', 'group')
      .attr('tabindex', 0)
      .attr('aria-label', d => `${d.systemManaged ? 'System-managed zone' : 'Zone'}: ${d.label}`)
      .attr('transform', d => `translate(${d.x},${d.y})`)
      .on('click', (_, d) => callbacks.onSelect?.(d.id))
      .on('keydown', (event, d) => {
        if ((event.key === 'Enter' || event.key === ' ') && event.target === event.currentTarget) {
          event.preventDefault();
          callbacks.onSelect?.(d.id);
        }
      });
    containers.append('rect').attr('class', 'zone-container-body').attr('rx', 10);
    containers.append('rect').attr('class', 'zone-container-header').attr('rx', 9);
    containers.append('line').attr('class', 'zone-container-divider');
    containers.append('text').attr('class', 'zone-container-title').attr('x', 12).attr('y', 16).text(d => d.label);
    containers.append('text').attr('class', 'zone-container-meta').attr('x', 12).attr('y', 29)
      .text(d => d.systemManaged ? `System managed · ${d.childCount ?? 0} VM` : `${d.team === 'red' ? 'Red' : 'Blue'} team · ${d.childCount ?? 0} VM${d.childCount === 1 ? '' : 's'}`);
    const arrangeControls = containers.append('g')
      .attr('class', 'zone-arrange')
      .attr('role', 'button')
      .attr('tabindex', callbacks.readOnly ? null : 0)
      .attr('aria-label', d => `Arrange VMs in ${d.label}`)
      .attr('aria-disabled', callbacks.readOnly ? 'true' : null)
      .on('click', (event, d) => {
        event.stopPropagation();
        if (!callbacks.readOnly) callbacks.onArrangeZone?.(d.id);
      })
      .on('keydown', (event, d) => {
        if (!callbacks.readOnly && (event.key === 'Enter' || event.key === ' ')) {
          event.preventDefault();
          event.stopPropagation();
          callbacks.onArrangeZone?.(d.id);
        }
      });
    arrangeControls.append('rect').attr('width', 96).attr('height', 28).attr('rx', 5);
    arrangeControls.append('text').attr('x', 48).attr('y', 18).attr('text-anchor', 'middle').text('Arrange VMs');

    function updateContainers() {
      const bounds = containerBounds();
      containers.attr('transform', d => `translate(${d.x},${d.y})`);
      containers.select('.zone-container-body')
        .attr('width', d => bounds.get(d.id).width)
        .attr('height', d => bounds.get(d.id).height);
      containers.select('.zone-container-header')
        .attr('width', d => bounds.get(d.id).width)
        .attr('height', ZONE_CONTAINER_GEOMETRY.headerHeight);
      containers.select('.zone-container-divider')
        .attr('x2', d => bounds.get(d.id).width)
        .attr('y1', ZONE_CONTAINER_GEOMETRY.headerHeight)
        .attr('y2', ZONE_CONTAINER_GEOMETRY.headerHeight);
      arrangeControls.attr('transform', d => `translate(${bounds.get(d.id).width - 104},4)`);
    }
    updateContainers();

    let groups = structuralLayer.selectAll('g.topo-node')
      .data(nodes.filter(node => !isZoneContainer(node) && topologyNodePresentation(node) === 'structural'), d => d.id)
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
    let machineGroups = machineLayer.selectAll('g.topo-node')
      .data(nodes.filter(node => topologyNodePresentation(node) === 'machine'), d => d.id)
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
      machineGroups = machineGroups.call(d3.drag()
        .on('drag', function(event, d) {
          d.x = event.x;
          d.y = event.y;
          d3.select(this).attr('transform', `translate(${d.x},${d.y})`);
          updateContainers();
          updateLinks();
        })
        .on('end', (_, d) => {
          currentLayout.nodes[d.id] = {x: d.x, y: d.y};
          callbacks.onLayoutChange?.(structuredClone(currentLayout));
        }));
    }

    const structuralGroups = groups;

    structuralGroups.append('rect')
      .attr('class', 'node-body')
      .attr('x', -70)
      .attr('y', -24)
      .attr('width', 140)
      .attr('height', 48)
      .attr('rx', 7);
    machineGroups
      .append('rect')
      .attr('class', 'node-hit-target')
      .attr('x', -40)
      .attr('y', -30)
      .attr('width', 80)
      .attr('height', 72);
    machineGroups.append('circle')
      .attr('class', 'node-state-ring')
      .attr('cy', -3)
      .attr('r', 28);
    const primaryIcons = machineGroups
      .append('svg')
      .attr('class', 'node-primary-icon')
      .attr('x', -18)
      .attr('y', -21)
      .attr('width', 36)
      .attr('height', 36)
      .attr('viewBox', d => d.icons?.primary?.viewBox || '0 0 24 24')
      .attr('aria-hidden', 'true');
    primaryIcons.append('path').attr('d', d => d.icons?.primary?.path || '');
    const secondaryIcons = machineGroups
      .append('svg')
      .attr('class', 'node-secondary-icon')
      .attr('x', MACHINE_ICON_GEOMETRY.secondaryX)
      .attr('y', MACHINE_ICON_GEOMETRY.secondaryY)
      .attr('width', MACHINE_ICON_GEOMETRY.secondarySize)
      .attr('height', MACHINE_ICON_GEOMETRY.secondarySize)
      .attr('viewBox', d => d.icons?.secondary?.viewBox || '0 0 24 24')
      .attr('aria-hidden', 'true');
    secondaryIcons.append('path').attr('d', d => d.icons?.secondary?.path || '');
    structuralGroups.append('text').attr('class', 'node-label').attr('text-anchor', 'middle').attr('y', 4).text(d => d.label);
    machineGroups.append('text').attr('class', 'machine-label').attr('text-anchor', 'middle').attr('y', 34).text(d => d.label);

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

  function arrangedLayout(zoneId) {
    return arrangedZoneLayout(graph, currentLayout, zoneId);
  }

  function destroy() {
    pendingLayoutKey = null;
    svg.on('.zoom', null);
    scene.remove();
  }

  return {render, fit, resetLayout, focusNode, arrangedLayout, destroy};
}
