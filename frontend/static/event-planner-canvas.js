export function topologyNodeClass(node) {
  return ['topo-node', node.type, node.selected && 'selected', node.invalid && 'invalid'].filter(Boolean).join(' ');
}

export function topologyAccessibleLabel(node) {
  const base = `${node.systemManaged ? 'System-managed zone' : node.type}: ${node.label}`;
  return typeof node.annotation === 'string' && node.annotation !== ''
    ? `${base}, address ${node.annotation}`
    : base;
}

export function topologyAnnotationPresentation(node) {
  if (typeof node?.annotation !== 'string' || node.annotation === '') return null;
  if (['zone', 'firewall-zone'].includes(node.type)) {
    return {
      className: 'zone-address-rail', prefix: 'Range · ', value: truncatedAnnotation(node.annotation, 38),
      x: 0, y: ZONE_CONTAINER_GEOMETRY.baseHeaderHeight,
      height: ZONE_CONTAINER_GEOMETRY.addressRailHeight,
    };
  }
  if (['vm', 'firewall'].includes(node.type)) {
    return {className: 'machine-label topo-node-address', text: truncatedAnnotation(node.annotation, 24), x: 0, y: 46};
  }
  return null;
}

export function truncatedAnnotation(value, maxLength) {
  if (typeof value !== 'string') return '';
  return value.length > maxLength ? `${value.slice(0, Math.max(0, maxLength - 3))}…` : value;
}

export function topologyLinkClass(link) {
  return `topo-link${link.source.selected || link.target.selected ? ' selected-adjacent' : ''}`;
}

export function topologyNodePresentation(node) {
  return ['gateway', 'firewall', 'vm'].includes(node.type) ? 'machine' : 'structural';
}

export function topologyThemeStyle(node) {
  return node?.color ? {'--node-theme-color': node.color} : {};
}

export function arrangeControlPresentation() {
  return {
    width: 32,
    height: 28,
    viewBox: '0 0 24 24',
    path: 'M3 3h7v7H3V3zm11 0h7v7h-7V3zM3 14h7v7H3v-7zm11 0h7v7h-7v-7z',
    title: 'Arrange VMs',
  };
}

export function mergeLayoutNodes(layout, nodes) {
  return {version: 1, nodes: structuredClone(nodes), themes: structuredClone(layout?.themes || {})};
}

export const MACHINE_ICON_GEOMETRY = Object.freeze({
  secondarySize: 24,
  secondaryX: -2,
  secondaryY: -7,
});

export const ZONE_CONTAINER_GEOMETRY = Object.freeze({
  baseHeaderHeight: 36,
  addressRailHeight: 24,
  padding: 20,
  machineWidth: 80,
  machineHeight: 72,
  annotatedMachineHeight: 84,
  machineTop: -30,
  machineAnchorOffset: 36,
  columnGap: 24,
  rowGap: 24,
  titleInset: 12,
  headerTextWidth: 104,
  headerControlGap: 8,
  arrangeControlWidth: 32,
  headerRightInset: 8,
});

export function machineBounds(node) {
  const width = ZONE_CONTAINER_GEOMETRY.machineWidth;
  const annotated = ['vm', 'firewall'].includes(node?.type) && typeof node.annotation === 'string' && node.annotation !== '';
  return {
    x: (node?.x ?? 0) - width / 2,
    y: (node?.y ?? 0) + ZONE_CONTAINER_GEOMETRY.machineTop,
    width,
    height: annotated ? ZONE_CONTAINER_GEOMETRY.annotatedMachineHeight : ZONE_CONTAINER_GEOMETRY.machineHeight,
  };
}

export function zoneHeaderHeight(zone) {
  return ZONE_CONTAINER_GEOMETRY.baseHeaderHeight
    + (['zone', 'firewall-zone'].includes(zone?.type) && typeof zone.annotation === 'string' && nodeHasAnnotation(zone)
      ? ZONE_CONTAINER_GEOMETRY.addressRailHeight : 0);
}

function nodeHasAnnotation(node) {
  return node.annotation !== '';
}

export function zoneChildren(graph, zoneId) {
  return graph.filter(node => node.parent === zoneId && ['vm', 'firewall'].includes(node.type));
}

export function zoneGridMetrics(children) {
  const geometry = ZONE_CONTAINER_GEOMETRY;
  if (!children.length) {
    return {
      columns: 0,
      rows: [],
      width: geometry.padding * 2,
      contentHeight: geometry.padding * 2,
    };
  }
  const columns = Math.ceil(Math.sqrt(children.length));
  const rows = Array.from({length: Math.ceil(children.length / columns)}, (_, row) =>
    children.slice(row * columns, (row + 1) * columns));
  const rowHeights = rows.map(row => Math.max(...row.map(child => machineBounds(child).height)));
  return {
    columns,
    rows,
    width: geometry.padding * 2 + columns * geometry.machineWidth + (columns - 1) * geometry.columnGap,
    contentHeight: geometry.padding + geometry.machineAnchorOffset + geometry.machineTop
      + rowHeights.reduce((height, rowHeight) => height + rowHeight, 0)
      + (rows.length - 1) * geometry.rowGap + geometry.padding,
  };
}

export function calculateZoneBounds(zone, children) {
  const geometry = ZONE_CONTAINER_GEOMETRY;
  const headerHeight = zoneHeaderHeight(zone);
  const metrics = zoneGridMetrics(children);
  const headerWidth = geometry.titleInset + geometry.headerTextWidth + geometry.headerControlGap
    + geometry.arrangeControlWidth + geometry.headerRightInset;
  const minimumWidth = Math.max(headerWidth, metrics.width);
  const minimumHeight = headerHeight + metrics.contentHeight;
  const requiredWidth = children.reduce((width, child) => Math.max(
    width,
    machineBounds(child).x + machineBounds(child).width + geometry.padding - zone.x,
  ), minimumWidth);
  const requiredHeight = children.reduce((height, child) => Math.max(
    height,
    machineBounds(child).y + machineBounds(child).height + geometry.padding - zone.y,
  ), minimumHeight);
  return {x: zone.x, y: zone.y, width: requiredWidth, height: requiredHeight, headerHeight};
}

export function arrangeZoneChildren(zone, children) {
  if (!children.length) return {};
  const geometry = ZONE_CONTAINER_GEOMETRY;
  const {columns, rows} = zoneGridMetrics(children);
  const rowOffsets = rows.map((_, row) => rows.slice(0, row).reduce((offset, previous) =>
    offset + Math.max(...previous.map(child => machineBounds(child).height)) + geometry.rowGap, 0));
  return Object.fromEntries(children.map((child, index) => [child.id, {
    x: zone.x + geometry.padding + geometry.machineWidth / 2
      + (index % columns) * (geometry.machineWidth + geometry.columnGap),
    y: zone.y + zoneHeaderHeight(zone) + geometry.padding + geometry.machineAnchorOffset
      + rowOffsets[Math.floor(index / columns)],
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
  return mergeLayoutNodes(layout, nodes);
}

export function constrainMachinePosition(position, zoneBounds) {
  const geometry = ZONE_CONTAINER_GEOMETRY;
  return {
    x: Math.max(position.x, zoneBounds.x + geometry.padding + geometry.machineWidth / 2),
    y: Math.max(position.y, zoneBounds.y + (zoneBounds.headerHeight ?? geometry.baseHeaderHeight) + geometry.padding + geometry.machineAnchorOffset),
  };
}

export function translateZoneLayout(layout, zoneId, childIds, dx, dy) {
  const nodes = structuredClone(layout?.nodes || {});
  for (const id of [zoneId, ...childIds]) {
    const position = nodes[id];
    if (Number.isFinite(position?.x) && Number.isFinite(position?.y)) {
      nodes[id] = {x: position.x + dx, y: position.y + dy};
    }
  }
  return mergeLayoutNodes(layout, nodes);
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
  for (const node of graph) {
    const parent = byId.get(node.parent);
    if (!isZoneContainer(parent) || !['vm', 'firewall'].includes(node.type)) continue;
    const constrained = constrainMachinePosition(
      nodes[node.id],
      calculateZoneBounds({...parent, ...nodes[parent.id]}, []),
    );
    if (constrained.x !== nodes[node.id].x || constrained.y !== nodes[node.id].y) {
      nodes[node.id] = constrained;
      added = true;
    }
  }
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
    currentLayout = mergeLayoutNodes(layout, layout.nodes || {});
    scene.selectAll('*').remove();

    const completed = calculateHierarchicalLayout(graph, currentLayout);
    currentLayout = mergeLayoutNodes(currentLayout, completed.nodes);
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
      .attr('aria-label', topologyAccessibleLabel)
      .attr('data-colour-inherited', d => d.colorInherited ? 'true' : null)
      .style('--node-theme-color', d => d.color || null)
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
    const annotatedZones = containers.filter(d => topologyAnnotationPresentation(d));
    annotatedZones.append('rect')
      .attr('class', d => topologyAnnotationPresentation(d).className)
      .attr('x', d => topologyAnnotationPresentation(d).x)
      .attr('y', d => topologyAnnotationPresentation(d).y)
      .attr('height', d => topologyAnnotationPresentation(d).height);
    const addressLabels = annotatedZones.append('text')
      .attr('class', 'zone-address-label')
      .attr('x', 12)
      .attr('y', d => topologyAnnotationPresentation(d).y + 16);
    addressLabels.append('tspan').attr('class', 'zone-address-prefix')
      .text(d => topologyAnnotationPresentation(d).prefix);
    addressLabels.append('tspan').attr('class', 'zone-address-value')
      .text(d => topologyAnnotationPresentation(d).value);
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
    const arrangeControl = arrangeControlPresentation();
    arrangeControls.append('title').text(arrangeControl.title);
    arrangeControls.append('rect')
      .attr('width', arrangeControl.width)
      .attr('height', arrangeControl.height)
      .attr('rx', 5);
    arrangeControls.append('svg')
      .attr('x', 8)
      .attr('y', 6)
      .attr('width', 16)
      .attr('height', 16)
      .attr('viewBox', arrangeControl.viewBox)
      .attr('aria-hidden', 'true')
      .append('path')
      .attr('d', arrangeControl.path)
      .attr('aria-hidden', 'true');

    function updateContainers() {
      const bounds = containerBounds();
      containers.attr('transform', d => `translate(${d.x},${d.y})`);
      containers.select('.zone-container-body')
        .attr('width', d => bounds.get(d.id).width)
        .attr('height', d => bounds.get(d.id).height);
      containers.select('.zone-container-header')
        .attr('width', d => bounds.get(d.id).width)
        .attr('height', d => bounds.get(d.id).headerHeight);
      containers.select('.zone-container-divider')
        .attr('x2', d => bounds.get(d.id).width)
        .attr('y1', d => bounds.get(d.id).headerHeight)
        .attr('y2', d => bounds.get(d.id).headerHeight);
      containers.select('.zone-address-rail')
        .attr('width', d => bounds.get(d.id).width);
      arrangeControls.attr('transform', d => `translate(${bounds.get(d.id).width
        - ZONE_CONTAINER_GEOMETRY.arrangeControlWidth - ZONE_CONTAINER_GEOMETRY.headerRightInset},4)`);
    }
    updateContainers();

    let groups = structuralLayer.selectAll('g.topo-node')
      .data(nodes.filter(node => !isZoneContainer(node) && topologyNodePresentation(node) === 'structural'), d => d.id)
      .join('g')
      .attr('class', topologyNodeClass)
      .attr('data-node-id', d => d.id)
      .attr('role', 'button')
      .attr('tabindex', 0)
      .attr('aria-label', topologyAccessibleLabel)
      .attr('data-colour-inherited', d => d.colorInherited ? 'true' : null)
      .style('--node-theme-color', d => d.color || null)
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
      .attr('aria-label', topologyAccessibleLabel)
      .attr('data-colour-inherited', d => d.colorInherited ? 'true' : null)
      .style('--node-theme-color', d => d.color || null)
      .attr('transform', d => `translate(${d.x},${d.y})`)
      .on('click', (_, d) => callbacks.onSelect?.(d.id))
      .on('keydown', (event, d) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          callbacks.onSelect?.(d.id);
        }
      });

    function updateMachineTransforms() {
      machineGroups.attr('transform', d => `translate(${d.x},${d.y})`);
    }

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
          const parent = byId.get(d.parent);
          const bounds = parent && isZoneContainer(parent) ? containerBounds().get(parent.id) : null;
          const position = bounds ? constrainMachinePosition({x: event.x, y: event.y}, bounds) : {x: event.x, y: event.y};
          d.x = position.x;
          d.y = position.y;
          d3.select(this).attr('transform', `translate(${d.x},${d.y})`);
          updateContainers();
          updateLinks();
        })
        .on('end', (_, d) => {
          currentLayout.nodes[d.id] = {x: d.x, y: d.y};
          callbacks.onLayoutChange?.(structuredClone(currentLayout));
        }));

      let dragStartLayout;
      let dragStartPosition;
      let dragChildIds = [];
      containers.select('.zone-container-header').call(d3.drag()
        .on('start', function(event, d) {
          dragStartLayout = structuredClone(currentLayout);
          dragStartPosition = {...dragStartLayout.nodes[d.id]};
          dragChildIds = zoneChildren(nodes, d.id).map(child => child.id);
        })
        .on('drag', function(event, d) {
          const nextLayout = translateZoneLayout(
            dragStartLayout,
            d.id,
            dragChildIds,
            event.x - dragStartPosition.x,
            event.y - dragStartPosition.y,
          );
          currentLayout = nextLayout;
          for (const id of [d.id, ...dragChildIds]) {
            const node = byId.get(id);
            if (node && nextLayout.nodes[id]) Object.assign(node, nextLayout.nodes[id]);
          }
          updateMachineTransforms();
          updateContainers();
          updateLinks();
        })
        .on('end', function(event, d) {
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
      .attr('x', d => machineBounds(d).x - d.x)
      .attr('y', d => machineBounds(d).y - d.y)
      .attr('width', d => machineBounds(d).width)
      .attr('height', d => machineBounds(d).height);
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
    machineGroups.filter(d => topologyAnnotationPresentation(d))
      .append('text')
      .attr('class', d => topologyAnnotationPresentation(d).className)
      .attr('text-anchor', 'middle')
      .attr('x', d => topologyAnnotationPresentation(d).x)
      .attr('y', d => topologyAnnotationPresentation(d).y)
      .text(d => topologyAnnotationPresentation(d).text);

    if (completed.added && !callbacks.readOnly) {
      const key = JSON.stringify(completed.nodes);
      if (pendingLayoutKey !== key) {
        pendingLayoutKey = key;
        queueMicrotask(() => {
          if (pendingLayoutKey !== key) return;
          pendingLayoutKey = null;
          callbacks.onLayoutChange?.(mergeLayoutNodes(currentLayout, completed.nodes));
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
    currentLayout = mergeLayoutNodes(currentLayout, completed.nodes);
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
