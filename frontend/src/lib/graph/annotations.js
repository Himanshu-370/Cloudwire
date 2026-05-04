/**
 * Cost service-level annotations — banners over service groups showing total spend.
 */
export function computeCostAnnotations(positionedNodes, costServiceTotals) {
  if (!costServiceTotals || typeof costServiceTotals !== "object") return [];

  const annotations = [];

  // Group all nodes by service (regardless of whether they have per-resource costs)
  const allNodesByService = new Map();
  positionedNodes.forEach((n) => {
    if (!n.position || !n.service) return;
    if (!allNodesByService.has(n.service)) allNodesByService.set(n.service, []);
    allNodesByService.get(n.service).push(n);
  });

  // Sum up per-resource costs already assigned to nodes, per service
  const resourceCostByService = new Map();
  positionedNodes.forEach((n) => {
    if (n.cost_usd != null && n.service) {
      resourceCostByService.set(
        n.service,
        (resourceCostByService.get(n.service) || 0) + n.cost_usd,
      );
    }
  });

  for (const [service, total] of Object.entries(costServiceTotals)) {
    if (total <= 0) continue;

    const serviceNodes = allNodesByService.get(service);
    if (!serviceNodes || serviceNodes.length === 0) continue;

    // If some nodes have per-resource costs, show only the unmatched remainder
    const matchedCost = resourceCostByService.get(service) || 0;
    const remainder = total - matchedCost;
    if (remainder <= 0.01) continue; // All costs accounted for by per-resource matches

    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    serviceNodes.forEach((n) => {
      minX = Math.min(minX, n.position.x);
      maxX = Math.max(maxX, n.position.x);
      minY = Math.min(minY, n.position.y);
      maxY = Math.max(maxY, n.position.y);
    });

    const pad = 70;
    const isPartial = matchedCost > 0;
    const displayAmount = isPartial ? remainder : total;
    const formatted = displayAmount < 100
      ? `$${displayAmount.toFixed(2)}`
      : `$${Math.round(displayAmount).toLocaleString()}`;

    const subtitle = isPartial
      ? `${serviceNodes.length} resource${serviceNodes.length === 1 ? "" : "s"} (unmatched service costs)`
      : `${serviceNodes.length} resource${serviceNodes.length === 1 ? "" : "s"} (service-level total)`;

    annotations.push({
      id: `cost-svc:${service}`,
      title: `${service.toUpperCase()}: ${formatted} MTD`,
      subtitle,
      minX: minX - pad, maxX: maxX + pad, minY: minY - pad, maxY: maxY + pad,
      tone: "cost-service", rx: 10,
    });
  }

  return annotations;
}

/**
 * Network topology container annotations (VPC, AZ, subnet zones).
 */

export function computeNetworkAnnotations(positionedNodes, edges) {
  const annotations = [];
  const nodeMap = new Map(positionedNodes.map((n) => [n.id, n]));

  const vpcChildren = new Map();
  const subnetChildren = new Map();

  edges.forEach((e) => {
    if (e.relationship !== "contains") return;
    const src = nodeMap.get(e.source);
    if (!src || src.service !== "vpc") return;
    if (src.type === "vpc") {
      if (!vpcChildren.has(e.source)) vpcChildren.set(e.source, new Set());
      vpcChildren.get(e.source).add(e.target);
    } else if (src.type === "subnet") {
      if (!subnetChildren.has(e.source)) subnetChildren.set(e.source, new Set());
      subnetChildren.get(e.source).add(e.target);
    }
  });

  const azGroups = new Map();

  edges.forEach((e) => {
    if (e.relationship !== "contains") return;
    const src = nodeMap.get(e.source);
    const tgt = nodeMap.get(e.target);
    if (src && src.type === "vpc" && tgt && tgt.type === "subnet") {
      const subChildren = subnetChildren.get(e.target);
      if (subChildren && vpcChildren.has(e.source)) {
        subChildren.forEach((id) => vpcChildren.get(e.source).add(id));
      }
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
      ...bounds, tone: "subnet-container", rx: 20,
    });
  });

  // AZ annotations (middle tier)
  azGroups.forEach((memberIds, azKey) => {
    if (memberIds.size < 1) return;
    const allIds = new Set(memberIds);
    memberIds.forEach((id) => allIds.add(id));
    const bounds = computeBounds(allIds, 100);
    if (!bounds) return;
    const az = azKey.split(":").pop();
    const subnetCount = [...memberIds].filter((id) => nodeMap.get(id)?.type === "subnet").length;
    annotations.push({
      id: `az-zone:${azKey}`,
      title: az,
      subtitle: `${subnetCount} subnet${subnetCount === 1 ? "" : "s"}`,
      ...bounds, tone: "az-container", rx: 16,
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
      ...bounds, tone: "vpc-container", rx: 12,
    });
  });

  return [
    ...annotations.filter((a) => a.tone === "vpc-container"),
    ...annotations.filter((a) => a.tone === "az-container"),
    ...annotations.filter((a) => a.tone === "subnet-container"),
  ];
}
