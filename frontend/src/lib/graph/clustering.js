/**
 * Service-level clustering and VPC/subnet container collapse.
 */

export function buildClusteredGraph(nodes, edges, collapsedServices) {
  if (!collapsedServices || collapsedServices.size === 0) return { nodes, edges };

  const nodeToCluster = new Map();
  const clusterData = new Map();

  nodes.forEach((node) => {
    if (collapsedServices.has(node.service)) {
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
    label: `${data.count} ${service}`,
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
    const key = `${src}\u2192${tgt}`;
    if (edgeSet.has(key)) return;
    edgeSet.add(key);
    outEdges.push({ ...edge, id: key, source: src, target: tgt });
  });

  return { nodes: outNodes, edges: outEdges };
}

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

export function collapseContainerNodes(nodes, edges, collapsedContainers) {
  if (!collapsedContainers || collapsedContainers.size === 0) return { nodes, edges };

  const nodeMap = new Map(nodes.map((n) => [n.id, n]));
  const containerChildren = new Map();

  edges.forEach((e) => {
    if (e.relationship !== "contains") return;
    const src = nodeMap.get(e.source);
    if (!src || src.service !== "vpc") return;
    if (!containerChildren.has(e.source)) containerChildren.set(e.source, new Set());
    containerChildren.get(e.source).add(e.target);
  });

  const annotationToContainer = new Map();
  containerChildren.forEach((_, containerId) => {
    const node = nodeMap.get(containerId);
    if (!node) return;
    if (node.type === "vpc") annotationToContainer.set(`vpc-zone:${containerId}`, containerId);
    else if (node.type === "subnet") annotationToContainer.set(`subnet-zone:${containerId}`, containerId);
  });

  collapsedContainers.forEach((annId) => {
    if (!annId.startsWith("az-zone:")) return;
    const rest = annId.slice("az-zone:".length);
    const lastColon = rest.lastIndexOf(":");
    if (lastColon < 0) return;
    const vpcNodeId = rest.slice(0, lastColon);
    const az = rest.slice(lastColon + 1);
    const vpcKids = containerChildren.get(vpcNodeId);
    if (!vpcKids) return;
    const azChildren = new Set();
    vpcKids.forEach((childId) => {
      const child = nodeMap.get(childId);
      if (child && child.type === "subnet" && child.availability_zone === az) {
        azChildren.add(childId);
        const subKids = containerChildren.get(childId);
        if (subKids) subKids.forEach((id) => azChildren.add(id));
      }
    });
    if (azChildren.size > 0) {
      annotationToContainer.set(annId, `__az__:${annId}`);
      containerChildren.set(`__az__:${annId}`, azChildren);
    }
  });

  const removedIds = new Set();
  const syntheticNodes = [];
  const idMapping = new Map();

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
    allChildren.delete(containerId);
    const containerNode = nodeMap.get(containerId);
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
