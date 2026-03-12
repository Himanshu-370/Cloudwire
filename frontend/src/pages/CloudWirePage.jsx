import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { GraphCanvas } from "../components/graph/GraphCanvas";
import { InspectorPanel } from "../components/layout/InspectorPanel";
import { ServiceSidebar } from "../components/layout/ServiceSidebar";
import { TopBar } from "../components/layout/TopBar";
import { useScanPolling, formatJobStatusLabel } from "../hooks/useScanPolling";
import { DEFAULT_REGION } from "../lib/awsRegions";
import {
  buildClusteredGraph,
  computeBlastRadius,
  computeFocusSubgraph,
  countServices,
  detectPatterns,
  filterGraphByRegion,
  findShortestPath,
  generateArchitectureSummary,
  layoutHybridGraph,
  layoutSwimlane,
  partitionByConnectivity,
} from "../lib/graphTransforms";

const DEFAULT_SERVICES = ["apigateway", "lambda", "sqs", "eventbridge", "dynamodb"];

function loadStoredServices() {
  try {
    const raw = localStorage.getItem("cloudwire_services");
    if (!raw) return DEFAULT_SERVICES;
    // New format: JSON array
    if (raw.trim().startsWith("[")) return JSON.parse(raw);
    // Legacy format: comma-separated string
    return raw.split(",").map((s) => s.trim().toLowerCase()).filter(Boolean);
  } catch (err) {
    console.warn("Failed to restore services from localStorage:", err);
    return DEFAULT_SERVICES;
  }
}

function WarningsPanel({ warnings }) {
  const [expanded, setExpanded] = useState(false);
  const permissionWarnings = warnings.filter((w) => w.startsWith("[permission]"));
  const otherWarnings = warnings.filter((w) => !w.startsWith("[permission]"));

  return (
    <div className="graph-stage-warnings">
      <button className="graph-warnings-toggle" onClick={() => setExpanded((v) => !v)}>
        <span>
          {permissionWarnings.length > 0 && (
            <span className="graph-warnings-perm-badge">{permissionWarnings.length} permission error{permissionWarnings.length === 1 ? "" : "s"}</span>
          )}
          {otherWarnings.length > 0 && (
            <span className="graph-warnings-other-badge">{otherWarnings.length} warning{otherWarnings.length === 1 ? "" : "s"}</span>
          )}
        </span>
        <span className="graph-warnings-caret">{expanded ? "▼" : "▲"}</span>
      </button>
      {expanded && (
        <ul className="graph-warnings-list">
          {permissionWarnings.map((w, i) => (
            <li key={`p-${i}`} className="graph-warnings-item graph-warnings-item--perm">
              {w.replace("[permission] ", "")}
            </li>
          ))}
          {otherWarnings.map((w, i) => (
            <li key={`o-${i}`} className="graph-warnings-item">
              {w}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function CloudWirePage() {
  const graphRef = useRef(null);
  const resourceRequestTokenRef = useRef(0);
  const hasAutoCollapsed = useRef(false);
  const layoutTimerRef = useRef(null);

  const {
    graphData,
    jobStatus,
    currentJobId,
    scanLoading,
    bootstrapLoading,
    error,
    setError,
    runScan,
    stopScan,
    fetchResource,
  } = useScanPolling();

  const [selectedNodeId, setSelectedNodeId] = useState(null);
  const [resourceDetails, setResourceDetails] = useState(null);
  const [region, setRegion] = useState(() => localStorage.getItem("cloudwire_region") || DEFAULT_REGION);
  const [selectedServices, setSelectedServices] = useState(loadStoredServices);
  const [scanMode, setScanMode] = useState("quick");
  const [query, setQuery] = useState("");
  const [hiddenServices, setHiddenServices] = useState([]);
  const [layoutMode, setLayoutMode] = useState("flow");
  const [layoutLoading, setLayoutLoading] = useState(false);
  const [forceRefresh, setForceRefresh] = useState(false);
  // FIX #24: separate resource-fetch errors from scan errors so they don't overwrite each other
  const [resourceError, setResourceError] = useState("");

  // New state
  const [showIsolated, setShowIsolated] = useState(false);
  const [collapsedServices, setCollapsedServices] = useState(new Set());
  const [focusModeActive, setFocusModeActive] = useState(false);
  const [focusDepth, setFocusDepth] = useState(1);
  const [pathFinderMode, setPathFinderMode] = useState(false);
  const [pathSource, setPathSource] = useState(null);
  const [foundPath, setFoundPath] = useState([]);
  const [blastRadiusMode, setBlastRadiusMode] = useState(false);
  const [showFlowAnimation, setShowFlowAnimation] = useState(true);
  const [showSummary, setShowSummary] = useState(false);

  // FIX #5/#10: deferred layout change with proper timer cleanup
  const changeLayout = useCallback((newMode) => {
    if (newMode === layoutMode) return;
    if (layoutTimerRef.current) window.clearTimeout(layoutTimerRef.current);
    setLayoutLoading(true);
    layoutTimerRef.current = window.setTimeout(() => {
      layoutTimerRef.current = null;
      setLayoutMode(newMode);
    }, 60);
  }, [layoutMode]);

  // Cleanup layout timer on unmount
  useEffect(() => () => { if (layoutTimerRef.current) window.clearTimeout(layoutTimerRef.current); }, []);

  // Clear loading indicator after the new layout has rendered
  useEffect(() => { setLayoutLoading(false); }, [layoutMode]);

  // Persist region/services to localStorage
  useEffect(() => { localStorage.setItem("cloudwire_region", region); }, [region]);
  useEffect(() => { localStorage.setItem("cloudwire_services", JSON.stringify(selectedServices)); }, [selectedServices]);

  // --- Data pipeline ---

  // Region filter
  const regionFilteredGraph = useMemo(
    () => filterGraphByRegion(graphData.nodes, graphData.edges, region),
    [graphData.edges, graphData.nodes, region]
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
  // If there are no connected nodes at all, always show everything so the
  // canvas isn't blank after a successful scan (e.g. SNS+SQS with no edges).
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

  // Apply focus mode
  const focusSubgraph = useMemo(() => {
    if (!focusModeActive || !selectedNodeId) return { nodes: clusteredNodes, edges: clusteredEdges };
    return computeFocusSubgraph(clusteredNodes, clusteredEdges, selectedNodeId, focusDepth);
  }, [focusModeActive, selectedNodeId, clusteredNodes, clusteredEdges, focusDepth]);

  // Layout
  const laidOutGraph = useMemo(() => {
    if (layoutMode === "swimlane") {
      return layoutSwimlane(focusSubgraph.nodes, focusSubgraph.edges);
    }
    return layoutHybridGraph(focusSubgraph.nodes, focusSubgraph.edges, layoutMode);
  }, [focusSubgraph, layoutMode]);

  const graphNodes = laidOutGraph.nodes;
  const graphNodeIds = useMemo(() => new Set(graphNodes.map((node) => node.id)), [graphNodes]);
  const graphEdges = useMemo(
    () => focusSubgraph.edges.filter((edge) => graphNodeIds.has(edge.source) && graphNodeIds.has(edge.target)),
    [graphNodeIds, focusSubgraph.edges]
  );

  // --- Auto-collapse effect ---
  const AUTO_COLLAPSE_THRESHOLD = 8;
  useEffect(() => {
    if (hasAutoCollapsed.current || Object.keys(serviceCounts).length === 0) return;
    hasAutoCollapsed.current = true;
    const toCollapse = new Set();
    Object.entries(serviceCounts).forEach(([svc, count]) => {
      if (count > AUTO_COLLAPSE_THRESHOLD) toCollapse.add(svc);
    });
    if (toCollapse.size > 0) setCollapsedServices(toCollapse);
  }, [serviceCounts]);

  // Deactivate focus mode when selection clears
  useEffect(() => {
    if (!selectedNodeId) setFocusModeActive(false);
  }, [selectedNodeId]);

  // Search across all visible nodes (before clustering)
  const SEARCH_LIMIT = 120;
  const { filteredSearchNodes, searchTruncated } = useMemo(() => {
    const q = query.trim().toLowerCase();
    const matched = q
      ? visibleNodes.filter((node) => `${node.id} ${node.label || ""} ${node.service || ""}`.toLowerCase().includes(q))
      : visibleNodes;
    return {
      filteredSearchNodes: matched.slice(0, SEARCH_LIMIT),
      searchTruncated: matched.length > SEARCH_LIMIT ? matched.length : 0,
    };
  }, [visibleNodes, query]);

  const stats = useMemo(
    () => ({
      Resources: graphNodes.length,
      Connections: graphEdges.length,
      Groups: laidOutGraph.componentCount,
    }),
    [graphEdges.length, graphNodes.length, laidOutGraph.componentCount]
  );

  useEffect(() => {
    setHiddenServices((previous) => previous.filter((service) => serviceCounts[service]));
  }, [serviceCounts]);

  // FIX #6: use visibleIds (post-region+service filter) not graphNodeIds (post-layout)
  // so a node hidden by filters properly clears the selection
  useEffect(() => {
    if (selectedNodeId && !visibleIds.has(selectedNodeId)) {
      resourceRequestTokenRef.current += 1;
      setSelectedNodeId(null);
      setResourceDetails(null);
    }
  }, [visibleIds, selectedNodeId]);

  useEffect(() => {
    resourceRequestTokenRef.current += 1;
    const requestToken = resourceRequestTokenRef.current;
    if (!selectedNodeId) {
      setResourceDetails(null);
      setResourceError("");
      return undefined;
    }

    setResourceDetails(null);
    setResourceError("");
    fetchResource(selectedNodeId, currentJobId)
      .then((payload) => {
        if (resourceRequestTokenRef.current !== requestToken) return;
        setResourceDetails(payload);
        setResourceError("");
      })
      .catch((fetchError) => {
        if (resourceRequestTokenRef.current !== requestToken) return;
        setResourceDetails(null);
        setResourceError(fetchError instanceof Error ? fetchError.message : String(fetchError));
      });
    return () => {
      resourceRequestTokenRef.current += 1;
    };
  }, [currentJobId, fetchResource, selectedNodeId, setError]);

  // Clear path when exiting path finder mode
  useEffect(() => {
    if (!pathFinderMode) {
      setPathSource(null);
      setFoundPath([]);
    }
  }, [pathFinderMode]);

  // FIX #15: path-not-found message state with cleanup (replaces setTimeout)
  const [pathNotFound, setPathNotFound] = useState(false);
  useEffect(() => {
    if (!pathNotFound) return undefined;
    const t = window.setTimeout(() => setPathNotFound(false), 3000);
    return () => window.clearTimeout(t);
  }, [pathNotFound]);

  const blastRadius = useMemo(() => {
    if (!blastRadiusMode || !selectedNodeId) return null;
    return computeBlastRadius(graphNodes, graphEdges, selectedNodeId);
  }, [blastRadiusMode, selectedNodeId, graphNodes, graphEdges]);

  const pathNodeIds = useMemo(() => {
    if (!foundPath.length) return null;
    return new Set(foundPath);
  }, [foundPath]);

  const pathEdgeIds = useMemo(() => {
    if (foundPath.length < 2) return null;
    const ids = new Set();
    for (let i = 0; i < foundPath.length - 1; i++) {
      ids.add(`${foundPath[i]}→${foundPath[i + 1]}`);
      // Also try edge IDs that might exist in the data
      graphEdges.forEach((e) => {
        if (e.source === foundPath[i] && e.target === foundPath[i + 1]) ids.add(e.id);
      });
    }
    return ids;
  }, [foundPath, graphEdges]);

  const architectureSummary = useMemo(
    () => generateArchitectureSummary(visibleNodes, visibleEdges),
    [visibleNodes, visibleEdges]
  );

  const detectedPatterns = useMemo(
    () => detectPatterns(visibleNodes, visibleEdges),
    [visibleNodes, visibleEdges]
  );

  const fitKey = useMemo(
    () => `${region}|${graphNodes.length}|${graphEdges.length}|${graphNodes[0]?.id || ""}|${graphNodes[graphNodes.length - 1]?.id || ""}`,
    [region, graphNodes, graphEdges]
  );

  const handleNodeSelect = useCallback((nodeId) => {
    if (pathFinderMode) {
      if (!pathSource) {
        setPathSource(nodeId);
        setSelectedNodeId(nodeId);
      } else if (pathSource === nodeId) {
        setPathSource(null);
        setFoundPath([]);
        setSelectedNodeId(null);
      } else {
        const path = findShortestPath(graphNodes, graphEdges, pathSource, nodeId);
        setFoundPath(path);
        setSelectedNodeId(nodeId);
        if (path.length === 0) setPathNotFound(true);
      }
    } else {
      setSelectedNodeId(nodeId);
    }
  }, [pathFinderMode, pathSource, graphNodes, graphEdges]);

  return (
    <div className="cloudwire-page">
      <TopBar
        region={region}
        onRegionChange={setRegion}
        selectedServices={selectedServices}
        onServicesChange={setSelectedServices}
        scanMode={scanMode}
        onScanModeChange={setScanMode}
        onRunScan={() => {
          hasAutoCollapsed.current = false;
          runScan({ region, services: selectedServices, mode: scanMode, forceRefresh }).catch(() => {});
        }}
        onStopScan={() => stopScan().catch((scanError) => setError(scanError instanceof Error ? scanError.message : String(scanError)))}
        scanLoading={scanLoading}
        jobStatus={jobStatus}
        statusLabel={formatJobStatusLabel(jobStatus)}
        forceRefresh={forceRefresh}
        onForceRefreshChange={setForceRefresh}
        warnings={jobStatus?.warnings || []}
      />

      <div className="cloudwire-layout">
        <ServiceSidebar
          serviceCounts={serviceCounts}
          hiddenServices={hiddenServices}
          onShowAllServices={() => setHiddenServices([])}
          onToggleService={(service) =>
            setHiddenServices((previous) =>
              previous.includes(service) ? previous.filter((value) => value !== service) : [...previous, service]
            )
          }
          collapsedServices={collapsedServices}
          onToggleCluster={(service) =>
            setCollapsedServices((prev) => {
              const next = new Set(prev);
              if (next.has(service)) next.delete(service);
              else next.add(service);
              return next;
            })
          }
          showIsolated={showIsolated}
          onToggleIsolated={() => setShowIsolated((v) => !v)}
          isolatedCount={isolatedNodes.length}
          stats={stats}
          layoutMode={layoutMode}
          onLayoutModeChange={changeLayout}
          query={query}
          onQueryChange={setQuery}
          filteredNodes={filteredSearchNodes}
          totalNodes={visibleNodes.length}
          searchTruncated={searchTruncated}
          selectedNodeId={selectedNodeId}
          onSelectNode={(nodeId) => {
            const node = visibleNodes.find((n) => n.id === nodeId);
            if (node && collapsedServices.has(node.service)) {
              setCollapsedServices((prev) => {
                const next = new Set(prev);
                next.delete(node.service);
                return next;
              });
            }
            setSelectedNodeId(nodeId);
            graphRef.current?.focusNode(nodeId);
          }}
        />

        <main className="graph-stage-shell">
          {bootstrapLoading && graphNodes.length === 0 && (
            <div className="graph-stage-loading">Connecting to backend...</div>
          )}

          {!bootstrapLoading && !scanLoading && graphNodes.length === 0 && visibleNodes.length === 0 && jobStatus?.status === "completed" && (
            <div className="graph-empty-state">
              <div className="graph-empty-title">No resources found</div>
              <div className="graph-empty-hint">
                {selectedServices.length === 0
                  ? "Select at least one service and run a scan."
                  : "The selected services returned no resources in this region. Try a different region or check your AWS credentials."}
              </div>
            </div>
          )}

          {!bootstrapLoading && !scanLoading && graphNodes.length === 0 && isolatedNodes.length > 0 && !showIsolated && !allIsolated && (
            <div className="graph-empty-state">
              <div className="graph-empty-title">{isolatedNodes.length} resources have no connections</div>
              <div className="graph-empty-hint">
                Click <strong>Disconnected</strong> in the sidebar to display them, or scan related services together (e.g. SNS + Lambda) to see edges.
              </div>
            </div>
          )}

          {layoutLoading && (
            <div className="layout-loading-overlay">
              <div className="layout-loading-spinner" />
              <span>Computing layout...</span>
            </div>
          )}

          <div className={`focus-mode-bar ${!selectedNodeId && !pathFinderMode ? "focus-mode-bar--idle" : ""}`}>
            <span className="focus-mode-label">
              {pathFinderMode
                ? (pathSource ? `PATH: select destination node` : "PATH FINDER: select source node")
                : !selectedNodeId
                ? "Select a node to inspect, focus, or trace paths"
                : focusModeActive
                ? `Focus: ${focusDepth}-hop view`
                : "Select a mode"}
            </span>
            <div className="focus-mode-controls">
              {selectedNodeId && focusModeActive && (
                <>
                  <button className={`focus-depth-btn ${focusDepth === 1 ? "active" : ""}`} onClick={() => setFocusDepth(1)}>1 hop</button>
                  <button className={`focus-depth-btn ${focusDepth === 2 ? "active" : ""}`} onClick={() => setFocusDepth(2)}>2 hops</button>
                  <button className={`focus-depth-btn ${focusDepth === 3 ? "active" : ""}`} onClick={() => setFocusDepth(3)}>3 hops</button>
                </>
              )}
              <button
                className={`focus-toggle-btn ${blastRadiusMode ? "active" : ""}`}
                onClick={() => setBlastRadiusMode((v) => !v)}
                title="Show what this resource affects (blast radius)"
                disabled={!selectedNodeId}
              >
                {blastRadiusMode ? "EXIT BLAST" : "BLAST RADIUS"}
              </button>
              <button
                className={`focus-toggle-btn ${focusModeActive ? "active" : ""}`}
                onClick={() => setFocusModeActive((v) => !v)}
                disabled={!selectedNodeId}
              >
                {focusModeActive ? "EXIT FOCUS" : "FOCUS"}
              </button>
              <button
                className={`focus-toggle-btn ${pathFinderMode ? "active" : ""}`}
                onClick={() => {
                  const next = !pathFinderMode;
                  setPathFinderMode(next);
                  if (next && selectedNodeId) setPathSource(selectedNodeId);
                }}
                title="Find shortest path between two nodes"
              >
                {pathFinderMode ? "EXIT PATH" : "PATH FINDER"}
              </button>
            </div>
          </div>

          {showSummary && (
            <div className="summary-panel">
              <div className="summary-panel-header">
                <span className="summary-panel-title">ARCHITECTURE SUMMARY</span>
                <button className="summary-panel-close" onClick={() => setShowSummary(false)}>✕</button>
              </div>
              <p className="summary-panel-text">{architectureSummary}</p>
              {detectedPatterns.length > 0 && (
                <div className="summary-patterns">
                  <div className="summary-patterns-title">DETECTED PATTERNS</div>
                  {detectedPatterns.map((p) => (
                    <div key={p.id} className="summary-pattern-row">
                      <span className="summary-pattern-name">{p.name}</span>
                      <span className="summary-pattern-desc">{p.description}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          <div className="graph-toolbar">
            <button
              className={`graph-toolbar-btn ${showSummary ? "active" : ""}`}
              onClick={() => setShowSummary((v) => !v)}
              title="Show architecture summary"
            >
              SUMMARY
            </button>
            <button
              className={`graph-toolbar-btn ${showFlowAnimation ? "active" : ""}`}
              onClick={() => setShowFlowAnimation((v) => !v)}
              title="Animate data flow along edges"
            >
              ▶ FLOW
            </button>
            <button
              className="graph-toolbar-btn"
              onClick={() => graphRef.current?.exportSvg()}
              title="Export graph as SVG"
            >
              EXPORT SVG
            </button>
          </div>

          <GraphCanvas
            ref={graphRef}
            nodes={graphNodes}
            edges={graphEdges}
            annotations={laidOutGraph.annotations}
            selectedNodeId={selectedNodeId}
            onSelectNode={handleNodeSelect}
            onClearSelection={() => {
              resourceRequestTokenRef.current += 1;
              setSelectedNodeId(null);
              setResourceDetails(null);
              if (pathFinderMode) { setPathSource(null); setFoundPath([]); }
            }}
            fitKey={fitKey}
            animated={showFlowAnimation}
            pathNodeIds={pathNodeIds}
            pathEdgeIds={pathEdgeIds}
            blastRadius={blastRadius}
          />

          {error && <div className="graph-stage-error">{error}</div>}
          {resourceError && <div className="graph-stage-error graph-stage-error--resource">{resourceError}</div>}
          {pathNotFound && <div className="graph-stage-error graph-stage-error--info">No directed path found between these nodes.</div>}

        </main>

        {resourceDetails && (
          <InspectorPanel
            resourceDetails={resourceDetails}
            allNodes={visibleNodes}
            onClose={() => {
              setSelectedNodeId(null);
              setResourceDetails(null);
            }}
            onJumpToNode={(nodeId) => {
              setSelectedNodeId(nodeId);
              graphRef.current?.focusNode(nodeId);
            }}
          />
        )}
      </div>

      {jobStatus?.warnings?.length > 0 && (
        <WarningsPanel key={currentJobId} warnings={jobStatus.warnings} />
      )}
    </div>
  );
}
