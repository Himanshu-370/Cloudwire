/**
 * Graph transforms barrel — re-exports from split modules for backward compatibility.
 */

export {
  normalizeGraph,
  countServices,
  filterGraphByRegion,
  partitionByConnectivity,
} from "./graph/normalize";

export {
  buildClusteredGraph,
  computeFocusSubgraph,
  collapseContainerNodes,
} from "./graph/clustering";

export {
  buildLevels,
  splitByConnectivity,
  layoutHybridGraph,
  layoutSwimlane,
  classifyNodeRole,
} from "./graph/layout";

export {
  findShortestPath,
  computeBlastRadius,
  detectPatterns,
  generateArchitectureSummary,
} from "./graph/analysis";

export { computeNetworkAnnotations, computeCostAnnotations } from "./graph/annotations";
