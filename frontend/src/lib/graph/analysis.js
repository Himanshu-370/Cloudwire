/**
 * Graph analysis: shortest path, blast radius, pattern detection, architecture summary.
 */

// --- Shortest path (BFS, directed) ---

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
  const fwd = new Map();
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

  const fwdAdj = new Map();
  const revAdj = new Map();
  nodes.forEach((n) => { fwdAdj.set(n.id, []); revAdj.set(n.id, []); });
  edges.forEach((e) => {
    if (fwdAdj.has(e.source)) fwdAdj.get(e.source).push(e.target);
    if (revAdj.has(e.target)) revAdj.get(e.target).push(e.source);
  });
  const edgesFrom = (id) => fwdAdj.get(id) || [];

  // API Backend: apigateway -> lambda -> (dynamodb|s3|rds)
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
          description: "HTTP API \u2192 Lambda function \u2192 data store",
          nodeIds: [n.id, lambdaId, ...dbTargets],
        });
      }
    });
  });

  // Event pipeline: eventbridge -> lambda
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

  // Queue worker: sqs -> lambda
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
    .map(([svc, count]) => `${count} ${svc}`);
  const resourceList = parts.slice(0, 5).join(", ") + (parts.length > 5 ? ` and ${parts.length - 5} more` : "");

  const entryPoints = nodes.filter((n) => !edges.some((e) => e.target === n.id));
  const entryLine = entryPoints.length
    ? `Data enters through ${entryPoints.length} entry point${entryPoints.length > 1 ? "s" : ""} (${entryPoints.slice(0, 3).map((n) => n.label || n.service).join(", ")}${entryPoints.length > 3 ? "\u2026" : ""}).`
    : "";

  return `This architecture has ${nodes.length} resources: ${resourceList}. ${entryLine} There are ${edges.length} connections across the graph.`.trim();
}
