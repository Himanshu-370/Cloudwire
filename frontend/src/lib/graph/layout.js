/**
 * Graph layout algorithms: flow, circular, swimlane, and hybrid orchestration.
 */

function stableLabel(node) {
  return String(node.label || node.id || "");
}

// ---------------------------------------------------------------------------
// Level computation (topological sort)
// ---------------------------------------------------------------------------

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

  let maxLevel = 0;
  for (const v of levels.values()) if (v > maxLevel) maxLevel = v;
  const fallback = maxLevel + 1;
  nodes.forEach((node) => {
    if (!levels.has(node.id)) levels.set(node.id, fallback);
  });

  return levels;
}

// ---------------------------------------------------------------------------
// Connectivity helpers
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// Layout constants
// ---------------------------------------------------------------------------

const LAYOUT = {
  xSpacing: 340,
  ySpacing: 180,
  maxRowsPerColumn: 6,
  laneOffset: 170,
  circularBaseRadius: 180,
  circularLevelSpacing: 200,
  circularRingSpacing: 160,
  circularMinArc: 240,
};

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

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
    title, subtitle, tone,
    minX: bounds.minX - paddingX,
    maxX: bounds.maxX + paddingX,
    minY: bounds.minY - paddingY,
    maxY: bounds.maxY + paddingY,
  };
}

// ---------------------------------------------------------------------------
// Flow layout
// ---------------------------------------------------------------------------

function layoutFlowGroup(nodes, edges, offsetX, offsetY) {
  const levels = buildLevels(nodes, edges);
  const buckets = new Map();
  nodes.forEach((node) => {
    const level = levels.get(node.id) || 0;
    if (!buckets.has(level)) buckets.set(level, []);
    buckets.get(level).push(node);
  });

  const positioned = [];
  const sortedLevels = Array.from(buckets.keys()).sort((a, b) => a - b);

  sortedLevels.forEach((level) => {
    const bucket = orderByConnectivity(buckets.get(level) || [], edges);
    const totalInBucket = bucket.length;
    const rowsPerColumn = Math.min(totalInBucket, LAYOUT.maxRowsPerColumn);

    bucket.forEach((node, index) => {
      const lane = Math.floor(index / rowsPerColumn);
      const row = index % rowsPerColumn;
      const colSize = Math.min(rowsPerColumn, totalInBucket - lane * rowsPerColumn);
      const yCenter = ((colSize - 1) * LAYOUT.ySpacing) / 2;
      positioned.push({
        ...node,
        position: {
          x: offsetX + level * LAYOUT.xSpacing + lane * LAYOUT.laneOffset,
          y: offsetY + row * LAYOUT.ySpacing - yCenter,
        },
      });
    });
  });
  return positioned;
}

// ---------------------------------------------------------------------------
// Circular layout
// ---------------------------------------------------------------------------

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
        const radius = LAYOUT.circularBaseRadius + level * LAYOUT.circularLevelSpacing + ring * LAYOUT.circularRingSpacing;
        const capacity = Math.max(4, Math.floor((2 * Math.PI * radius) / LAYOUT.circularMinArc));
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

// ---------------------------------------------------------------------------
// Hybrid layout (groups components and isolates)
// ---------------------------------------------------------------------------

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
    computeBounds(connectedNodes), 160, 150,
    "Connected Flows",
    `${components.length} grouped system${components.length === 1 ? "" : "s"}`,
    "primary"
  );
  const isolatedAnnotation = annotate(
    computeBounds(isolatedNodes), 120, 110,
    "Unconnected Resources",
    `${isolatedNodes.length} isolated node${isolatedNodes.length === 1 ? "" : "s"}`,
    "muted"
  );

  if (connectedAnnotation) annotations.push(connectedAnnotation);
  if (isolatedAnnotation) annotations.push(isolatedAnnotation);

  return {
    nodes: laidOut, annotations, connectedIds,
    componentCount: components.length,
    isolatedCount: isolatedNodes.length,
  };
}

// ---------------------------------------------------------------------------
// Swimlane layout
// ---------------------------------------------------------------------------

export function classifyNodeRole(node, allEdges) {
  if (node.type === "internet") return "trigger";
  const svc = String(node.service || "").toLowerCase();
  if (["apigateway", "eventbridge", "cloudfront", "route53", "appsync", "cognito", "elb"].includes(svc)) return "trigger";
  if (["lambda", "ec2", "ecs", "stepfunctions", "glue"].includes(svc)) return "processor";
  if (["dynamodb", "s3", "rds", "elasticache", "aurora", "redshift"].includes(svc)) return "storage";
  if (["sqs", "sns", "kinesis"].includes(svc)) return "queue";
  if (svc === "vpc") return "network";
  if (["iam", "secretsmanager", "kms"].includes(svc)) return "unknown";
  const hasIn = allEdges.some((e) => e.target === node.id);
  const hasOut = allEdges.some((e) => e.source === node.id);
  if (hasIn && !hasOut) return "storage";
  if (!hasIn && hasOut) return "trigger";
  return "unknown";
}

const LANE_ORDER = ["trigger", "queue", "processor", "storage", "network", "unknown"];
const LANE_Y_BASE = 160;
const LANE_SPACING = 300;
const NODE_X_SPACING = 260;

export function layoutSwimlane(nodes, edges) {
  if (!nodes.length) return { nodes: [], edges, annotations: [], componentCount: 1 };

  const lanes = {};
  LANE_ORDER.forEach((r) => { lanes[r] = []; });
  nodes.forEach((n) => {
    const role = classifyNodeRole(n, edges);
    (lanes[role] || lanes["unknown"]).push(n);
  });

  const degreeMap = new Map();
  edges.forEach((e) => {
    degreeMap.set(e.source, (degreeMap.get(e.source) || 0) + 1);
    degreeMap.set(e.target, (degreeMap.get(e.target) || 0) + 1);
  });
  const connectionCount = (id) => degreeMap.get(id) || 0;
  Object.values(lanes).forEach((group) =>
    group.sort((a, b) => connectionCount(b.id) - connectionCount(a.id))
  );

  const positionedNodes = [];
  const annotations = [];
  let laneIndex = 0;
  const LANE_LABELS = {
    trigger: "TRIGGERS & ENTRY POINTS", queue: "EVENTS & QUEUES",
    processor: "PROCESSORS & FUNCTIONS", storage: "DATA STORES",
    network: "NETWORK TOPOLOGY", unknown: "OTHER RESOURCES",
  };
  const LANE_TONES = {
    trigger: "lane-trigger", queue: "lane-queue", processor: "lane-processor",
    storage: "lane-storage", network: "lane-network", unknown: "lane-unknown",
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

    const padding = 80;
    annotations.push({
      id: `lane-${role}`,
      title: LANE_LABELS[role],
      subtitle: `${group.length} resource${group.length === 1 ? "" : "s"}`,
      minX: startX - padding, maxX: startX + totalWidth + padding,
      minY: laneY - 80, maxY: laneY + 80,
      tone: LANE_TONES[role],
    });

    laneIndex += 1;
  });

  return { nodes: positionedNodes, edges, annotations, componentCount: 1 };
}
