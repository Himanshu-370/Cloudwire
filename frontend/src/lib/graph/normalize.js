import { normalizeServiceName } from "../serviceVisuals";

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
