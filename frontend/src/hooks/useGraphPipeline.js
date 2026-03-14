import { useMemo } from "react";
import {
  buildClusteredGraph,
  collapseContainerNodes,
  computeFocusSubgraph,
  computeNetworkAnnotations,
  countServices,
  filterGraphByRegion,
  layoutHybridGraph,
  layoutSwimlane,
  partitionByConnectivity,
} from "../lib/graphTransforms";

export function useGraphPipeline({
  graphData,
  region,
  hiddenServices,
  showIsolated,
  collapsedServices,
  collapsedContainers,
  focusModeActive,
  selectedNodeId,
  focusDepth,
  layoutMode,
  skipRegionFilter,
}) {
  // Region filter
  const regionFilteredGraph = useMemo(
    () => {
      if (skipRegionFilter) {
        return { nodes: graphData.nodes, edges: graphData.edges };
      }
      return filterGraphByRegion(graphData.nodes, graphData.edges, region);
    },
    [graphData.edges, graphData.nodes, region, skipRegionFilter]
  );

  // Service visibility filter
  const visibleNodes = useMemo(
    () => regionFilteredGraph.nodes.filter((node) => !hiddenServices.includes(node.service || "unknown")),
    [hiddenServices, regionFilteredGraph.nodes]
  );
  const visibleIds = useMemo(() => new Set(visibleNodes.map((node) => node.id)), [visibleNodes]);
  const visibleEdges = useMemo(
    () => regionFilteredGraph.edges.filter((edge) => visibleIds.has(edge.source) && visibleIds.has(edge.target)),
    [regionFilteredGraph.edges, visibleIds]
  );

  // Service counts from all visible nodes (before clustering/focus)
  const serviceCounts = useMemo(() => countServices(visibleNodes), [visibleNodes]);

  // Partition into connected vs isolated
  const { connected: connectedNodes, isolated: isolatedNodes } = useMemo(
    () => partitionByConnectivity(visibleNodes, visibleEdges),
    [visibleNodes, visibleEdges]
  );

  // Apply isolated filter
  const allIsolated = connectedNodes.length === 0 && visibleNodes.length > 0;
  const preClusterNodes = useMemo(
    () => (showIsolated || allIsolated ? visibleNodes : connectedNodes),
    [showIsolated, allIsolated, visibleNodes, connectedNodes]
  );
  const preClusterNodeIds = useMemo(() => new Set(preClusterNodes.map((n) => n.id)), [preClusterNodes]);
  const preClusterEdges = useMemo(
    () => visibleEdges.filter((e) => preClusterNodeIds.has(e.source) && preClusterNodeIds.has(e.target)),
    [visibleEdges, preClusterNodeIds]
  );

  // Apply clustering
  const { nodes: clusteredNodes, edges: clusteredEdges } = useMemo(
    () => buildClusteredGraph(preClusterNodes, preClusterEdges, collapsedServices),
    [preClusterNodes, preClusterEdges, collapsedServices]
  );

  // Apply container collapse (VPC/AZ/subnet)
  const { nodes: containerNodes, edges: containerEdges } = useMemo(
    () => collapseContainerNodes(clusteredNodes, clusteredEdges, collapsedContainers),
    [clusteredNodes, clusteredEdges, collapsedContainers]
  );

  // Apply focus mode
  const focusSubgraph = useMemo(() => {
    if (!focusModeActive || !selectedNodeId) return { nodes: containerNodes, edges: containerEdges };
    return computeFocusSubgraph(containerNodes, containerEdges, selectedNodeId, focusDepth);
  }, [focusModeActive, selectedNodeId, containerNodes, containerEdges, focusDepth]);

  // Layout
  const laidOutGraph = useMemo(() => {
    const result = layoutMode === "swimlane"
      ? layoutSwimlane(focusSubgraph.nodes, focusSubgraph.edges)
      : layoutHybridGraph(focusSubgraph.nodes, focusSubgraph.edges, layoutMode);
    // Add VPC/subnet container annotations
    const networkAnnotations = computeNetworkAnnotations(result.nodes, focusSubgraph.edges);
    if (networkAnnotations.length > 0) {
      result.annotations = [...networkAnnotations, ...(result.annotations || [])];
    }
    return result;
  }, [focusSubgraph, layoutMode]);

  const graphNodes = laidOutGraph.nodes;
  const graphNodeIds = useMemo(() => new Set(graphNodes.map((node) => node.id)), [graphNodes]);
  const graphEdges = useMemo(
    () => focusSubgraph.edges.filter((edge) => graphNodeIds.has(edge.source) && graphNodeIds.has(edge.target)),
    [graphNodeIds, focusSubgraph.edges]
  );

  return {
    visibleNodes,
    visibleIds,
    visibleEdges,
    serviceCounts,
    connectedNodes,
    isolatedNodes,
    allIsolated,
    graphNodes,
    graphEdges,
    laidOutGraph,
  };
}
