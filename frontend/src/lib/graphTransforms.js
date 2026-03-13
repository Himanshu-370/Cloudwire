import { normalizeServiceName } from "./serviceVisuals";

function stableLabel(node) {
  return String(node.label || node.id || "");
}

export function normalizeGraph(rawGraph) {
  const nodes = (rawGraph?.nodes || []).map((node) => ({
    ...node,
    service: normalizeServiceName(node.service),
    region: node.region || node.aws_region || null,
  }));
  const nodeIds = new Set(nodes.map((node) => node.id));
  const edges = (rawGraph?.edges || []).filter((edge) => nodeIds.has(edge.source) && nodeIds.has(edge.target));
  return {
    nodes,
    edges,
    metadata: rawGraph?.metadata || {},
  };
}

export function splitByConnectivity(nodes, edges) {
  const nodeMap = new Map(nodes.map((node) => [node.id, node]));
  const adjacency = new Map(nodes.map((node) => [node.id, []]));
  const connectedIds = new Set();
  const filteredEdges = [];

  edges.forEach((edge) => {
    if (!nodeMap.has(edge.source) || !nodeMap.has(edge.target)) return;
    filteredEdges.push(edge);
    adjacency.get(edge.source).push(edge.target);
    adjacency.get(edge.target).push(edge.source);
    connectedIds.add(edge.source);
    connectedIds.add(edge.target);
  });

  const components = [];
  const visited = new Set();
  Array.from(connectedIds)
    .sort()
    .forEach((start) => {
      if (visited.has(start)) return;
      const queue = [start];
      const ids = [];
      visited.add(start);
      while (queue.length) {
        const current = queue.shift();
        ids.push(current);
        for (const next of adjacency.get(current) || []) {
          if (visited.has(next)) continue;
          visited.add(next);
          queue.push(next);
        }
      }
      components.push(ids);
    });

  return {
    components: components
      .map((ids) => ids.map((id) => nodeMap.get(id)).filter(Boolean))
      .sort((a, b) => b.length - a.length),
    isolated: nodes.filter((node) => !connectedIds.has(node.id)),
    connectedIds,
    filteredEdges,
  };
}

export function buildLevels(nodes, edges) {
  const indegree = new Map();
  const outgoing = new Map();

  nodes.forEach((node) => {
    indegree.set(node.id, 0);
    outgoing.set(node.id, []);
  });

  edges.forEach((edge) => {
    if (!outgoing.has(edge.source) || !outgoing.has(edge.target)) return;
    indegree.set(edge.target, (indegree.get(edge.target) || 0) + 1);
    outgoing.get(edge.source).push(edge.target);
  });

  const queue = Array.from(indegree.entries())
    .filter(([, degree]) => degree === 0)
    .map(([id]) => id)
    .sort();
  const levels = new Map(queue.map((id) => [id, 0]));

  while (queue.length) {
    const current = queue.shift();
    const level = levels.get(current) || 0;
    for (const next of outgoing.get(current) || []) {
      levels.set(next, Math.max(levels.get(next) || 0, level + 1));
      indegree.set(next, (indegree.get(next) || 0) - 1);
      if ((indegree.get(next) || 0) === 0) queue.push(next);
    }
  }

  // Cycle nodes — assign to one level past the deepest reachable level
  let maxLevel = 0;
  for (const v of levels.values()) if (v > maxLevel) maxLevel = v;
  const fallback = maxLevel + 1;
  nodes.forEach((node) => {
    if (!levels.has(node.id)) {
      levels.set(node.id, fallback);  // all unreachable nodes at same level
    }
  });

  return levels;
}

function edgesForIds(edges, ids) {
  return edges.filter((edge) => ids.has(edge.source) && ids.has(edge.target));
}

function orderByConnectivity(bucket, edges) {
  const adj = new Map();
  bucket.forEach((n) => adj.set(n.id, { in: [], out: [] }));
  edges.forEach((e) => {
    if (adj.has(e.source) && adj.has(e.target)) {
      adj.get(e.source).out.push(e.target);
      adj.get(e.target).in.push(e.source);
    }
  });
  return [...bucket].sort((a, b) => {
    const aIn = adj.get(a.id)?.in.length || 0;
    const bIn = adj.get(b.id)?.in.length || 0;
    if (aIn !== bIn) return aIn - bIn;
    return stableLabel(a).localeCompare(stableLabel(b));
  });
}

function layoutFlowGroup(nodes, edges, offsetX, offsetY) {
  const levels = buildLevels(nodes, edges);
  const buckets = new Map();
  nodes.forEach((node) => {
    const level = levels.get(node.id) || 0;
    if (!buckets.has(level)) buckets.set(level, []);
    buckets.get(level).push(node);
  });

  const xSpacing = 340;
  const ySpacing = 180;
  const maxRowsPerColumn = 6;
  const laneOffset = 170;

  const positioned = [];
  const sortedLevels = Array.from(buckets.keys()).sort((a, b) => a - b);

  sortedLevels.forEach((level) => {
    const bucket = orderByConnectivity(buckets.get(level) || [], edges);
    const totalInBucket = bucket.length;
    const rowsPerColumn = Math.min(totalInBucket, maxRowsPerColumn);

    bucket.forEach((node, index) => {
      const lane = Math.floor(index / rowsPerColumn);
      const row = index % rowsPerColumn;
      const colSize = Math.min(rowsPerColumn, totalInBucket - lane * rowsPerColumn);
      const yCenter = ((colSize - 1) * ySpacing) / 2;
      positioned.push({
        ...node,
        position: {
          x: offsetX + level * xSpacing + lane * laneOffset,
          y: offsetY + row * ySpacing - yCenter,
        },
      });
    });
  });
  return positioned;
}

function layoutCircularGroup(nodes, edges, centerX, centerY) {
  const levels = buildLevels(nodes, edges);
  const buckets = new Map();
  nodes.forEach((node) => {
    const level = levels.get(node.id) || 0;
    if (!buckets.has(level)) buckets.set(level, []);
    buckets.get(level).push(node);
  });

  const positioned = [];
  Array.from(buckets.keys())
    .sort((a, b) => a - b)
    .forEach((level) => {
      const bucket = [...(buckets.get(level) || [])].sort((a, b) => stableLabel(a).localeCompare(stableLabel(b)));
      if (level === 0 && bucket.length === 1) {
        positioned.push({ ...bucket[0], position: { x: centerX, y: centerY } });
        return;
      }

      let cursor = 0;
      let ring = 0;
      while (cursor < bucket.length) {
        const radius = 180 + level * 200 + ring * 160;
        const minArc = 240;
        const capacity = Math.max(4, Math.floor((2 * Math.PI * radius) / minArc));
        const count = Math.min(capacity, bucket.length - cursor);
        for (let index = 0; index < count; index += 1) {
          const angle = (Math.PI * 2 * index) / count + level * 0.16 + ring * 0.08;
          positioned.push({
            ...bucket[cursor + index],
            position: {
              x: centerX + Math.cos(angle) * radius,
              y: centerY + Math.sin(angle) * radius,
            },
          });
        }
        cursor += count;
        ring += 1;
      }
    });
  return positioned;
}

function computeBounds(nodes) {
  if (!nodes.length) return null;
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (const node of nodes) {
    const { x, y } = node.position;
    if (x < minX) minX = x;
    if (x > maxX) maxX = x;
    if (y < minY) minY = y;
    if (y > maxY) maxY = y;
  }
  return { minX, maxX, minY, maxY };
}

function annotate(bounds, paddingX, paddingY, title, subtitle, tone) {
  if (!bounds) return null;
  return {
    id: `${title.toLowerCase().replace(/\s+/g, "-")}-${tone}`,
    title,
    subtitle,
    tone,
    minX: bounds.minX - paddingX,
    maxX: bounds.maxX + paddingX,
    minY: bounds.minY - paddingY,
    maxY: bounds.maxY + paddingY,
  };
}

export function layoutHybridGraph(nodes, edges, mode = "flow") {
  const { components, isolated, connectedIds, filteredEdges } = splitByConnectivity(nodes, edges);
  const laidOut = [];
  const annotations = [];
  const columns = Math.max(1, Math.ceil(Math.sqrt(components.length || 1)));
  const connectedNodes = [];
  let maxConnectedX = 0;

  components.forEach((component, index) => {
    const column = index % columns;
    const row = Math.floor(index / columns);
    const ids = new Set(component.map((node) => node.id));
    const groupEdges = edgesForIds(filteredEdges, ids);
    const nodesForGroup =
      mode === "flow"
        ? layoutFlowGroup(component, groupEdges, 170 + column * 1200, 180 + row * 960)
        : layoutCircularGroup(component, groupEdges, 500 + column * 1200, 500 + row * 1100);
    nodesForGroup.forEach((node) => {
      maxConnectedX = Math.max(maxConnectedX, node.position.x);
      connectedNodes.push(node);
      laidOut.push(node);
    });
  });

  const isolatedBaseX = (maxConnectedX || 420) + 500;
  const isolatedColumns = Math.max(3, Math.ceil(Math.sqrt(isolated.length || 1)));
  const isolatedNodes = [...isolated]
    .sort((a, b) => stableLabel(a).localeCompare(stableLabel(b)))
    .map((node, index) => ({
      ...node,
      position: {
        x: isolatedBaseX + (index % isolatedColumns) * 260,
        y: 180 + Math.floor(index / isolatedColumns) * 180,
      },
    }));

  laidOut.push(...isolatedNodes);

  const connectedAnnotation = annotate(
    computeBounds(connectedNodes),
    160,
    150,
    "Connected Flows",
    `${components.length} grouped system${components.length === 1 ? "" : "s"}`,
    "primary"
  );
  const isolatedAnnotation = annotate(
    computeBounds(isolatedNodes),
    120,
    110,
    "Unconnected Resources",
    `${isolatedNodes.length} isolated node${isolatedNodes.length === 1 ? "" : "s"}`,
    "muted"
  );

  if (connectedAnnotation) annotations.push(connectedAnnotation);
  if (isolatedAnnotation) annotations.push(isolatedAnnotation);

  return {
    nodes: laidOut,
    annotations,
    connectedIds,
    componentCount: components.length,
    isolatedCount: isolatedNodes.length,
  };
}

export function countServices(nodes) {
  const counts = {};
  nodes.forEach((node) => {
    const service = normalizeServiceName(node.service);
    counts[service] = (counts[service] || 0) + 1;
  });
  return counts;
}

export function filterGraphByRegion(nodes, edges, region) {
  const allowedNodes = nodes.filter((node) => {
    if (!region) return true;
    if (!node.region) return true;
    return node.region === region || node.region === "global";
  });
  const ids = new Set(allowedNodes.map((node) => node.id));
  return {
    nodes: allowedNodes,
    edges: edges.filter((edge) => ids.has(edge.source) && ids.has(edge.target)),
  };
}

// Partition nodes into connected (have at least one edge) vs isolated (no edges)
export function partitionByConnectivity(nodes, edges) {
  const nodeIds = new Set(nodes.map((n) => n.id));
  const connectedIds = new Set();
  edges.forEach((edge) => {
    if (nodeIds.has(edge.source) && nodeIds.has(edge.target)) {
      connectedIds.add(edge.source);
      connectedIds.add(edge.target);
    }
  });
  return {
    connected: nodes.filter((n) => connectedIds.has(n.id)),
    isolated: nodes.filter((n) => !connectedIds.has(n.id)),
  };
}

// Collapse ISOLATED nodes of collapsed services into cluster nodes.
// Connected nodes of collapsed services remain as individual nodes.
// Edges involving only isolated nodes are rerouted; connected node edges stay as-is.
export function buildClusteredGraph(nodes, edges, collapsedServices) {
  if (!collapsedServices || collapsedServices.size === 0) return { nodes, edges };

  // Determine which nodes have connections
  const connectedIds = new Set();
  edges.forEach((e) => {
    connectedIds.add(e.source);
    connectedIds.add(e.target);
  });

  const nodeToCluster = new Map();
  const clusterData = new Map();

  nodes.forEach((node) => {
    // Only cluster isolated nodes of collapsed services
    if (collapsedServices.has(node.service) && !connectedIds.has(node.id)) {
      const clusterId = `cluster:${node.service}`;
      nodeToCluster.set(node.id, clusterId);
      if (!clusterData.has(node.service)) {
        clusterData.set(node.service, { count: 0, nodeIds: [] });
      }
      const data = clusterData.get(node.service);
      data.count += 1;
      data.nodeIds.push(node.id);
    }
  });

  const clusterNodes = Array.from(clusterData.entries()).map(([service, data]) => ({
    id: `cluster:${service}`,
    service,
    type: "cluster",
    label: `${data.count} ${service} (unconnected)`,
    count: data.count,
    nodeIds: data.nodeIds,
  }));

  const outNodes = [
    ...nodes.filter((n) => !nodeToCluster.has(n.id)),
    ...clusterNodes,
  ];

  const edgeSet = new Set();
  const outEdges = [];
  edges.forEach((edge) => {
    const src = nodeToCluster.get(edge.source) || edge.source;
    const tgt = nodeToCluster.get(edge.target) || edge.target;
    if (src === tgt) return;
    const key = `${src}→${tgt}`;
    if (edgeSet.has(key)) return;
    edgeSet.add(key);
    outEdges.push({ ...edge, id: key, source: src, target: tgt });
  });

  return { nodes: outNodes, edges: outEdges };
}

// Return a subgraph containing centerNodeId and all nodes within `depth` hops (bidirectional).
export function computeFocusSubgraph(nodes, edges, centerNodeId, depth) {
  const nodeMap = new Map(nodes.map((n) => [n.id, n]));
  if (!nodeMap.has(centerNodeId)) return { nodes, edges };

  const included = new Set([centerNodeId]);
  let frontier = new Set([centerNodeId]);

  for (let i = 0; i < depth; i += 1) {
    const next = new Set();
    edges.forEach((edge) => {
      if (frontier.has(edge.source) && nodeMap.has(edge.target) && !included.has(edge.target)) {
        included.add(edge.target);
        next.add(edge.target);
      }
      if (frontier.has(edge.target) && nodeMap.has(edge.source) && !included.has(edge.source)) {
        included.add(edge.source);
        next.add(edge.source);
      }
    });
    frontier = next;
    if (frontier.size === 0) break;
  }

  return {
    nodes: nodes.filter((n) => included.has(n.id)),
    edges: edges.filter((e) => included.has(e.source) && included.has(e.target)),
  };
}

// --- Collapse/expand VPC/subnet containers ---

export function collapseContainerNodes(nodes, edges, collapsedContainers) {
  if (!collapsedContainers || collapsedContainers.size === 0) return { nodes, edges };

  const nodeMap = new Map(nodes.map((n) => [n.id, n]));

  // Build containment: container node id → Set of direct children
  // Then resolve which annotation IDs map to which container node IDs
  const containerChildren = new Map();

  // Collect direct "contains" children for VPC infra nodes
  edges.forEach((e) => {
    if (e.relationship !== "contains") return;
    const src = nodeMap.get(e.source);
    if (!src || src.service !== "vpc") return;
    if (!containerChildren.has(e.source)) containerChildren.set(e.source, new Set());
    containerChildren.get(e.source).add(e.target);
  });

  // Map annotation IDs to container node IDs
  const annotationToContainer = new Map();
  containerChildren.forEach((_, containerId) => {
    const node = nodeMap.get(containerId);
    if (!node) return;
    if (node.type === "vpc") annotationToContainer.set(`vpc-zone:${containerId}`, containerId);
    else if (node.type === "subnet") annotationToContainer.set(`subnet-zone:${containerId}`, containerId);
  });

  // For AZ annotations, collect subnets in that AZ within a VPC
  // AZ annotation IDs are "az-zone:{vpcNodeId}:{az}"
  collapsedContainers.forEach((annId) => {
    if (!annId.startsWith("az-zone:")) return;
    const rest = annId.slice("az-zone:".length);
    const lastColon = rest.lastIndexOf(":");
    if (lastColon < 0) return;
    const vpcNodeId = rest.slice(0, lastColon);
    const az = rest.slice(lastColon + 1);
    // Find all subnets in this VPC with this AZ
    const vpcKids = containerChildren.get(vpcNodeId);
    if (!vpcKids) return;
    const azChildren = new Set();
    vpcKids.forEach((childId) => {
      const child = nodeMap.get(childId);
      if (child && child.type === "subnet" && child.availability_zone === az) {
        azChildren.add(childId);
        // Also add the subnet's children
        const subKids = containerChildren.get(childId);
        if (subKids) subKids.forEach((id) => azChildren.add(id));
      }
    });
    if (azChildren.size > 0) {
      // Create a synthetic container key for AZ
      annotationToContainer.set(annId, `__az__:${annId}`);
      containerChildren.set(`__az__:${annId}`, azChildren);
    }
  });

  // Collect all node IDs to remove (transitive children of collapsed containers)
  const removedIds = new Set();
  const syntheticNodes = [];
  const idMapping = new Map(); // removed node ID → synthetic node ID

  const collectTransitive = (containerId, into) => {
    const kids = containerChildren.get(containerId);
    if (!kids) return;
    kids.forEach((childId) => {
      into.add(childId);
      collectTransitive(childId, into);
    });
  };

  collapsedContainers.forEach((annId) => {
    const containerId = annotationToContainer.get(annId);
    if (!containerId) return;

    const allChildren = new Set();
    collectTransitive(containerId, allChildren);
    if (allChildren.size === 0) return;

    // Don't collapse the container node itself (e.g., the VPC node stays)
    const containerNode = nodeMap.get(containerId);
    allChildren.delete(containerId);

    const syntheticId = `collapsed:${containerId}`;
    allChildren.forEach((id) => {
      removedIds.add(id);
      idMapping.set(id, syntheticId);
    });

    syntheticNodes.push({
      id: syntheticId,
      service: "vpc",
      type: "cluster",
      label: `${containerNode?.label || containerId} (${allChildren.size})`,
      count: allChildren.size,
    });
  });

  if (removedIds.size === 0) return { nodes, edges };

  const outNodes = [
    ...nodes.filter((n) => !removedIds.has(n.id)),
    ...syntheticNodes,
  ];

  const edgeSet = new Set();
  const outEdges = [];
  edges.forEach((edge) => {
    const src = idMapping.get(edge.source) || edge.source;
    const tgt = idMapping.get(edge.target) || edge.target;
    if (src === tgt) return;
    const key = `${src}→${tgt}`;
    if (edgeSet.has(key)) return;
    edgeSet.add(key);
    outEdges.push({ ...edge, id: key, source: src, target: tgt });
  });

  return { nodes: outNodes, edges: outEdges };
}

// --- Role classification ---

export function classifyNodeRole(node, allEdges) {
  // Internet anchor nodes are entry points — place in trigger lane
  if (node.type === "internet") return "trigger";
  const svc = String(node.service || "").toLowerCase();
  if (["apigateway", "eventbridge", "cloudfront", "route53", "appsync", "cognito", "elb"].includes(svc)) return "trigger";
  if (["lambda", "ec2", "ecs", "stepfunctions", "glue"].includes(svc)) return "processor";
  if (["dynamodb", "s3", "rds", "elasticache", "aurora", "redshift"].includes(svc)) return "storage";
  if (["sqs", "sns", "kinesis"].includes(svc)) return "queue";
  if (svc === "vpc") return "network";
  if (["iam", "secretsmanager", "kms"].includes(svc)) return "unknown";
  // Fallback by connectivity
  const hasIn = allEdges.some((e) => e.target === node.id);
  const hasOut = allEdges.some((e) => e.source === node.id);
  if (hasIn && !hasOut) return "storage";
  if (!hasIn && hasOut) return "trigger";
  return "unknown";
}

// --- Swimlane layout ---

const LANE_ORDER = ["trigger", "queue", "processor", "storage", "network", "unknown"];
const LANE_Y_BASE = 160;
const LANE_SPACING = 300;
const NODE_X_SPACING = 260;

export function layoutSwimlane(nodes, edges) {
  if (!nodes.length) return { nodes: [], edges, annotations: [], componentCount: 1 };

  // Group nodes by role
  const lanes = {};
  LANE_ORDER.forEach((r) => { lanes[r] = []; });
  nodes.forEach((n) => {
    const role = classifyNodeRole(n, edges);
    (lanes[role] || lanes["unknown"]).push(n);
  });

  // Sort within each lane by connectivity (most connections first)
  const degreeMap = new Map();
  edges.forEach((e) => {
    degreeMap.set(e.source, (degreeMap.get(e.source) || 0) + 1);
    degreeMap.set(e.target, (degreeMap.get(e.target) || 0) + 1);
  });
  const connectionCount = (id) => degreeMap.get(id) || 0;
  Object.values(lanes).forEach((group) =>
    group.sort((a, b) => connectionCount(b.id) - connectionCount(a.id))
  );

  // Assign positions
  const positionedNodes = [];
  const annotations = [];
  let laneIndex = 0;
  const LANE_LABELS = {
    trigger: "TRIGGERS & ENTRY POINTS",
    queue: "EVENTS & QUEUES",
    processor: "PROCESSORS & FUNCTIONS",
    storage: "DATA STORES",
    network: "NETWORK TOPOLOGY",
    unknown: "OTHER RESOURCES",
  };
  const LANE_TONES = {
    trigger: "lane-trigger",
    queue: "lane-queue",
    processor: "lane-processor",
    storage: "lane-storage",
    network: "lane-network",
    unknown: "lane-unknown",
  };

  LANE_ORDER.forEach((role) => {
    const group = lanes[role];
    if (!group.length) return;

    const laneY = LANE_Y_BASE + laneIndex * LANE_SPACING;
    const totalWidth = (group.length - 1) * NODE_X_SPACING;
    const startX = -totalWidth / 2;

    group.forEach((node, i) => {
      positionedNodes.push({
        ...node,
        position: { x: startX + i * NODE_X_SPACING, y: laneY },
      });
    });

    // Lane annotation rect
    const padding = 80;
    annotations.push({
      id: `lane-${role}`,
      title: LANE_LABELS[role],
      subtitle: `${group.length} resource${group.length === 1 ? "" : "s"}`,
      minX: startX - padding,
      maxX: startX + totalWidth + padding,
      minY: laneY - 80,
      maxY: laneY + 80,
      tone: LANE_TONES[role],
    });

    laneIndex += 1;
  });

  return { nodes: positionedNodes, edges, annotations, componentCount: 1 };
}

// --- Shortest path (BFS, directed) ---

// --- Network topology container annotations ---

export function computeNetworkAnnotations(positionedNodes, edges) {
  const annotations = [];
  const nodeMap = new Map(positionedNodes.map((n) => [n.id, n]));

  // Build containment maps from edges: VPC → children, subnet → children
  const vpcChildren = new Map();   // vpc node id → Set of child node ids
  const subnetChildren = new Map(); // subnet node id → Set of child node ids

  edges.forEach((e) => {
    if (e.relationship !== "contains") return;
    const src = nodeMap.get(e.source);
    if (!src || src.service !== "vpc") return;
    if (src.type === "vpc") {
      if (!vpcChildren.has(e.source)) vpcChildren.set(e.source, new Set());
      vpcChildren.get(e.source).add(e.target);
      // Transitively add grandchildren (subnet's children belong to VPC too)
    } else if (src.type === "subnet") {
      if (!subnetChildren.has(e.source)) subnetChildren.set(e.source, new Set());
      subnetChildren.get(e.source).add(e.target);
    }
  });

  // Add subnet children to their parent VPC
  // Also build AZ grouping: azKey → Set of subnet + child node IDs
  const azGroups = new Map(); // "${vpcId}:${az}" → Set of node IDs (subnets + their children)

  edges.forEach((e) => {
    if (e.relationship !== "contains") return;
    const src = nodeMap.get(e.source);
    const tgt = nodeMap.get(e.target);
    if (src && src.type === "vpc" && tgt && tgt.type === "subnet") {
      const subChildren = subnetChildren.get(e.target);
      if (subChildren && vpcChildren.has(e.source)) {
        subChildren.forEach((id) => vpcChildren.get(e.source).add(id));
      }
      // AZ grouping
      const az = tgt.availability_zone;
      if (az) {
        const azKey = `${e.source}:${az}`;
        if (!azGroups.has(azKey)) azGroups.set(azKey, new Set());
        azGroups.get(azKey).add(e.target);
        if (subChildren) subChildren.forEach((id) => azGroups.get(azKey).add(id));
      }
    }
  });

  const computeBounds = (nodeIds, padding) => {
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    let count = 0;
    nodeIds.forEach((id) => {
      const n = nodeMap.get(id);
      if (!n || !n.position) return;
      minX = Math.min(minX, n.position.x);
      maxX = Math.max(maxX, n.position.x);
      minY = Math.min(minY, n.position.y);
      maxY = Math.max(maxY, n.position.y);
      count++;
    });
    if (count === 0) return null;
    return { minX: minX - padding, maxX: maxX + padding, minY: minY - padding, maxY: maxY + padding };
  };

  // Subnet annotations (innermost)
  subnetChildren.forEach((children, subnetId) => {
    const allIds = new Set(children);
    allIds.add(subnetId);
    const bounds = computeBounds(allIds, 80);
    if (!bounds) return;
    const subnetNode = nodeMap.get(subnetId);
    const az = subnetNode?.availability_zone || "";
    annotations.push({
      id: `subnet-zone:${subnetId}`,
      title: subnetNode?.label || subnetId,
      subtitle: az ? `AZ: ${az}` : `${children.size} resource${children.size === 1 ? "" : "s"}`,
      ...bounds,
      tone: "subnet-container",
      rx: 20,
    });
  });

  // AZ annotations (middle tier)
  azGroups.forEach((memberIds, azKey) => {
    if (memberIds.size < 1) return;
    const allIds = new Set(memberIds);
    // Include the subnet nodes themselves in bounds
    memberIds.forEach((id) => allIds.add(id));
    const bounds = computeBounds(allIds, 100);
    if (!bounds) return;
    const az = azKey.split(":").pop();
    const subnetCount = [...memberIds].filter((id) => nodeMap.get(id)?.type === "subnet").length;
    annotations.push({
      id: `az-zone:${azKey}`,
      title: az,
      subtitle: `${subnetCount} subnet${subnetCount === 1 ? "" : "s"}`,
      ...bounds,
      tone: "az-container",
      rx: 16,
    });
  });

  // VPC annotations (outermost)
  vpcChildren.forEach((children, vpcId) => {
    const allIds = new Set(children);
    allIds.add(vpcId);
    const bounds = computeBounds(allIds, 120);
    if (!bounds) return;
    const vpcNode = nodeMap.get(vpcId);
    const subnetCount = [...children].filter((id) => nodeMap.get(id)?.type === "subnet").length;
    const resourceCount = children.size - subnetCount;
    const parts = [];
    if (subnetCount) parts.push(`${subnetCount} subnet${subnetCount === 1 ? "" : "s"}`);
    if (resourceCount) parts.push(`${resourceCount} resource${resourceCount === 1 ? "" : "s"}`);
    annotations.push({
      id: `vpc-zone:${vpcId}`,
      title: `VPC: ${vpcNode?.label || vpcId}`,
      subtitle: parts.join(", ") || "",
      ...bounds,
      tone: "vpc-container",
      rx: 12,
    });
  });

  // Z-order: VPC (back) → AZ → Subnet (front)
  return [
    ...annotations.filter((a) => a.tone === "vpc-container"),
    ...annotations.filter((a) => a.tone === "az-container"),
    ...annotations.filter((a) => a.tone === "subnet-container"),
  ];
}

export function findShortestPath(nodes, edges, sourceId, targetId) {
  if (sourceId === targetId) return [sourceId];
  const nodeIds = new Set(nodes.map((n) => n.id));
  if (!nodeIds.has(sourceId) || !nodeIds.has(targetId)) return [];

  const adj = new Map();
  nodeIds.forEach((id) => adj.set(id, []));
  edges.forEach((e) => {
    if (adj.has(e.source)) adj.get(e.source).push(e.target);
  });

  const parent = new Map();
  const queue = [sourceId];
  let head = 0;
  parent.set(sourceId, null);

  while (head < queue.length) {
    const current = queue[head++];
    if (current === targetId) {
      const path = [];
      let node = targetId;
      while (node !== null) {
        path.unshift(node);
        node = parent.get(node);
      }
      return path;
    }
    for (const next of adj.get(current) || []) {
      if (!parent.has(next)) {
        parent.set(next, current);
        queue.push(next);
      }
    }
  }
  return [];
}

// --- Blast radius (BFS upstream + downstream) ---

export function computeBlastRadius(nodes, edges, nodeId) {
  const nodeIds = new Set(nodes.map((n) => n.id));

  // Forward adjacency
  const fwd = new Map();
  // Reverse adjacency
  const rev = new Map();
  nodeIds.forEach((id) => { fwd.set(id, []); rev.set(id, []); });
  edges.forEach((e) => {
    if (fwd.has(e.source) && fwd.has(e.target)) {
      fwd.get(e.source).push(e.target);
      rev.get(e.target).push(e.source);
    }
  });

  function bfs(startAdj) {
    const visited = new Set();
    const queue = [...(startAdj.get(nodeId) || [])];
    let head = 0;
    queue.forEach((id) => visited.add(id));
    while (head < queue.length) {
      const cur = queue[head++];
      for (const next of startAdj.get(cur) || []) {
        if (!visited.has(next) && next !== nodeId) {
          visited.add(next);
          queue.push(next);
        }
      }
    }
    visited.delete(nodeId);
    return visited;
  }

  return { upstream: bfs(rev), downstream: bfs(fwd) };
}

// --- Pattern detection ---

export function detectPatterns(nodes, edges) {
  const patterns = [];
  const svcMap = new Map(nodes.map((n) => [n.id, String(n.service || "").toLowerCase()]));

  // Pre-build adjacency maps for O(1) lookups
  const fwdAdj = new Map();
  const revAdj = new Map();
  nodes.forEach((n) => { fwdAdj.set(n.id, []); revAdj.set(n.id, []); });
  edges.forEach((e) => {
    if (fwdAdj.has(e.source)) fwdAdj.get(e.source).push(e.target);
    if (revAdj.has(e.target)) revAdj.get(e.target).push(e.source);
  });
  const edgesFrom = (id) => fwdAdj.get(id) || [];
  const edgesTo = (id) => revAdj.get(id) || [];

  // API Backend: apigateway → lambda → (dynamodb|s3|rds)
  nodes.forEach((n) => {
    if (svcMap.get(n.id) !== "apigateway") return;
    edgesFrom(n.id).forEach((lambdaId) => {
      if (svcMap.get(lambdaId) !== "lambda") return;
      const dbTargets = edgesFrom(lambdaId).filter((t) =>
        ["dynamodb", "s3", "rds"].includes(svcMap.get(t))
      );
      if (dbTargets.length > 0) {
        patterns.push({
          id: `api-backend-${n.id}`,
          name: "API Backend",
          description: "HTTP API → Lambda function → data store",
          nodeIds: [n.id, lambdaId, ...dbTargets],
        });
      }
    });
  });

  // Event pipeline: eventbridge → lambda
  nodes.forEach((n) => {
    if (svcMap.get(n.id) !== "eventbridge") return;
    const lambdaTargets = edgesFrom(n.id).filter((t) => svcMap.get(t) === "lambda");
    if (lambdaTargets.length > 0) {
      patterns.push({
        id: `event-pipeline-${n.id}`,
        name: "Event-Driven Pipeline",
        description: "EventBridge rule triggers Lambda function",
        nodeIds: [n.id, ...lambdaTargets],
      });
    }
  });

  // Queue worker: sqs → lambda
  nodes.forEach((n) => {
    if (svcMap.get(n.id) !== "sqs") return;
    const lambdaTargets = edgesFrom(n.id).filter((t) => svcMap.get(t) === "lambda");
    if (lambdaTargets.length > 0) {
      patterns.push({
        id: `queue-worker-${n.id}`,
        name: "Queue Worker",
        description: "SQS queue drives Lambda consumer",
        nodeIds: [n.id, ...lambdaTargets],
      });
    }
  });

  // Fan-out: node with 3+ outgoing edges
  nodes.forEach((n) => {
    const targets = edgesFrom(n.id);
    if (targets.length >= 3) {
      patterns.push({
        id: `fan-out-${n.id}`,
        name: "Fan-out",
        description: `${svcMap.get(n.id) || "resource"} routes to ${targets.length} downstream services`,
        nodeIds: [n.id, ...targets],
      });
    }
  });

  return patterns;
}

// --- Architecture summary ---

export function generateArchitectureSummary(nodes, edges) {
  if (!nodes.length) return "No resources found. Run a scan to visualize your architecture.";

  const counts = {};
  nodes.forEach((n) => {
    const svc = String(n.service || "unknown").toLowerCase();
    counts[svc] = (counts[svc] || 0) + 1;
  });

  const parts = Object.entries(counts)
    .sort((a, b) => b[1] - a[1])
    .map(([svc, count]) => `${count} ${svc}${count > 1 ? "" : ""}`);
  const resourceList = parts.slice(0, 5).join(", ") + (parts.length > 5 ? ` and ${parts.length - 5} more` : "");

  const entryPoints = nodes.filter((n) => !edges.some((e) => e.target === n.id));
  const entryLine = entryPoints.length
    ? `Data enters through ${entryPoints.length} entry point${entryPoints.length > 1 ? "s" : ""} (${entryPoints.slice(0, 3).map((n) => n.label || n.service).join(", ")}${entryPoints.length > 3 ? "…" : ""}).`
    : "";

  return `This architecture has ${nodes.length} resources: ${resourceList}. ${entryLine} There are ${edges.length} connections across the graph.`.trim();
}
