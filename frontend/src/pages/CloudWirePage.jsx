import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ErrorBoundary } from "../components/ErrorBoundary";
import { GraphCanvas } from "../components/graph/GraphCanvas";
import { InspectorPanel } from "../components/layout/InspectorPanel";
import { LayoutDropdown } from "../components/layout/LayoutDropdown";
import { ServiceSidebar } from "../components/layout/ServiceSidebar";
import { TopBar } from "../components/layout/TopBar";
import { WarningsPanel } from "../components/layout/WarningsPanel";
import { useGraphPipeline } from "../hooks/useGraphPipeline";
import { usePathFinder } from "../hooks/usePathFinder";
import { useScanPolling, formatJobStatusLabel } from "../hooks/useScanPolling";
import { useTagDiscovery } from "../hooks/useTagDiscovery";
import { useTerraformUpload } from "../hooks/useTerraformUpload";
import { TerraformDropZone } from "../components/layout/TerraformDropZone";
import { TerraformFilePanel } from "../components/layout/TerraformFilePanel";
import { DEFAULT_REGION } from "../lib/awsRegions";
import {
  computeBlastRadius,
  detectPatterns,
  generateArchitectureSummary,
} from "../lib/graphTransforms";

const DEFAULT_SERVICES = ["apigateway", "lambda", "sqs", "eventbridge", "dynamodb", "vpc"];

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
    injectGraph,
  } = useScanPolling();

  const [selectedNodeId, setSelectedNodeId] = useState(null);
  const [resourceDetails, setResourceDetails] = useState(null);
  const [region, setRegion] = useState(() => localStorage.getItem("cloudwire_region") || DEFAULT_REGION);
  const [selectedServices, setSelectedServices] = useState(loadStoredServices);
  const [scanMode, setScanMode] = useState("quick");
  const [query, setQuery] = useState("");
  const [hiddenServices, setHiddenServices] = useState([]);
  const [layoutMode, setLayoutMode] = useState("circular");
  const [layoutLoading, setLayoutLoading] = useState(false);
  const [forceRefresh, setForceRefresh] = useState(false);
  // FIX #24: separate resource-fetch errors from scan errors so they don't overwrite each other
  const [resourceError, setResourceError] = useState("");

  // New state
  const [showIsolated, setShowIsolated] = useState(false);
  const [collapsedServices, setCollapsedServices] = useState(new Set());
  const [focusModeActive, setFocusModeActive] = useState(false);
  const [focusDepth, setFocusDepth] = useState(1);
  const [blastRadiusMode, setBlastRadiusMode] = useState(false);
  const [showFlowAnimation, setShowFlowAnimation] = useState(true);
  const [showSummary, setShowSummary] = useState(false);
  const [scanFilterMode, setScanFilterMode] = useState("services"); // "services" | "tags" | "terraform"
  const [showTerraformFilePanel, setShowTerraformFilePanel] = useState(false);
  const [tagScanLoading, setTagScanLoading] = useState(false);
  const [collapsedContainers, setCollapsedContainers] = useState(new Set());
  const [hoveredExposedPath, setHoveredExposedPath] = useState(null);
  const [costOverlayEnabled, setCostOverlayEnabled] = useState(false);

  const tagDiscovery = useTagDiscovery(region, scanFilterMode === "tags");
  const terraform = useTerraformUpload();

  // --- Data pipeline ---
  const {
    visibleNodes,
    visibleIds,
    visibleEdges,
    serviceCounts,
    isolatedNodes,
    allIsolated,
    graphNodes,
    graphEdges,
    laidOutGraph,
  } = useGraphPipeline({
    graphData,
    region,
    hiddenServices,
    showIsolated: showIsolated || scanFilterMode === "terraform",
    collapsedServices,
    collapsedContainers,
    focusModeActive,
    selectedNodeId,
    focusDepth,
    layoutMode,
    skipRegionFilter: scanFilterMode === "terraform",
    costOverlayEnabled,
  });

  // --- Path finder ---
  const {
    pathFinderMode,
    pathSource,
    foundPath,
    pathNotFound,
    handleNodeSelect: pathFinderHandleNodeSelect,
    resetPathFinder,
    togglePathFinderMode,
  } = usePathFinder(graphNodes, graphEdges);

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

  const blastRadius = useMemo(() => {
    if (!blastRadiusMode || !selectedNodeId) return null;
    return computeBlastRadius(graphNodes, graphEdges, selectedNodeId);
  }, [blastRadiusMode, selectedNodeId, graphNodes, graphEdges]);

  const pathNodeIds = useMemo(() => {
    // Path-finder path takes priority; fallback to hovered exposed path
    if (foundPath.length) return new Set(foundPath);
    if (hoveredExposedPath) return new Set(hoveredExposedPath);
    return null;
  }, [foundPath, hoveredExposedPath]);

  const edgeLookup = useMemo(() => {
    const map = new Map();
    graphEdges.forEach((e) => {
      const key = `${e.source}\u2192${e.target}`;
      const arr = map.get(key);
      if (arr) arr.push(e.id);
      else map.set(key, [e.id]);
    });
    return map;
  }, [graphEdges]);

  const pathEdgeIds = useMemo(() => {
    const chain = foundPath.length ? foundPath : hoveredExposedPath;
    if (!chain || chain.length < 2) return null;
    const ids = new Set();
    for (let i = 0; i < chain.length - 1; i++) {
      const key = `${chain[i]}\u2192${chain[i + 1]}`;
      ids.add(key);
      const edgeIds = edgeLookup.get(key);
      if (edgeIds) edgeIds.forEach((id) => ids.add(id));
    }
    return ids;
  }, [foundPath, hoveredExposedPath, edgeLookup]);

  const architectureSummary = useMemo(
    () => generateArchitectureSummary(visibleNodes, visibleEdges),
    [visibleNodes, visibleEdges]
  );

  const detectedPatterns = useMemo(
    () => detectPatterns(visibleNodes, visibleEdges),
    [visibleNodes, visibleEdges]
  );

  const scanCompleteCount = useRef(0);
  useEffect(() => {
    if (!scanLoading && graphNodes.length > 0) scanCompleteCount.current += 1;
  }, [scanLoading, graphNodes.length]);

  const fitKey = useMemo(
    () => `${region}|${graphNodes.length}|${graphEdges.length}|${graphNodes[0]?.id || ""}|${graphNodes[graphNodes.length - 1]?.id || ""}|${scanCompleteCount.current}`,
    [region, graphNodes, graphEdges]
  );

  const handleScanFilterModeChange = useCallback((mode) => {
    const prev = scanFilterMode;
    setScanFilterMode(mode);
    if (mode === "terraform" && !terraform.lastResult) {
      setShowTerraformFilePanel(true);
    }
    // Clear terraform graph when switching away from terraform mode
    if (prev === "terraform" && mode !== "terraform") {
      injectGraph(`tf-switch-${Date.now()}`, { nodes: [], edges: [], metadata: {} });
    }
  }, [terraform.lastResult, scanFilterMode, injectGraph]);

  const handleTerraformParsed = useCallback(async () => {
    const result = await terraform.uploadAndParse();
    if (!result) return;
    hasAutoCollapsed.current = false;
    injectGraph(result.job_id, result.graph, {
      mode: "terraform",
      region: "terraform",
    });
    setShowTerraformFilePanel(false);
  }, [terraform, injectGraph]);

  const handleTerraformFilesAccepted = useCallback((fileList) => {
    terraform.addFiles(fileList);
    setShowTerraformFilePanel(true);
  }, [terraform]);

  const handleAnnotationClick = useCallback((annotationId) => {
    setCollapsedContainers((prev) => {
      const next = new Set(prev);
      if (next.has(annotationId)) next.delete(annotationId);
      else next.add(annotationId);
      return next;
    });
  }, []);

  // Exposed internet path highlighting on hover
  const handleHoverNode = useCallback((nodeId) => {
    if (!nodeId) { setHoveredExposedPath(null); return; }
    const node = graphNodes.find((n) => n.id === nodeId);
    if (node?.exposed_internet && Array.isArray(node.internet_path_nodes) && node.internet_path_nodes.length > 1) {
      setHoveredExposedPath(node.internet_path_nodes);
    } else {
      setHoveredExposedPath(null);
    }
  }, [graphNodes]);

  // Tag-based scan flow: discover resources, then trigger a normal scan
  const handleScanByTags = useCallback(async () => {
    hasAutoCollapsed.current = false;
    setTagScanLoading(true);
    setError("");

    // Phase 1: discover resources matching tags
    let result;
    try {
      result = await tagDiscovery.discoverResources();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setTagScanLoading(false);
      return;
    }
    setTagScanLoading(false);

    if (!result || result.arns.length === 0) {
      setError("No resources found matching the selected tags.");
      return;
    }

    // Phase 2: scan the discovered services (scanLoading from useScanPolling takes over)
    const discoveredServices = result.services;
    try {
      await runScan({
        region,
        services: discoveredServices,
        mode: scanMode,
        forceRefresh,
        includeCosts: costOverlayEnabled,
        tagArns: result.arns,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [tagDiscovery, region, scanMode, forceRefresh, costOverlayEnabled, runScan, setError]);

  const handleNodeSelect = useCallback((nodeId) => {
    const handled = pathFinderHandleNodeSelect(nodeId, setSelectedNodeId);
    if (!handled) {
      setSelectedNodeId(nodeId);
    }
  }, [pathFinderHandleNodeSelect]);

  return (
    <div className="cloudwire-page">
      <TopBar
        region={region}
        onRegionChange={setRegion}
        selectedServices={selectedServices}
        onServicesChange={setSelectedServices}
        scanMode={scanMode}
        onScanModeChange={setScanMode}
        costOverlayEnabled={costOverlayEnabled}
        onCostOverlayChange={setCostOverlayEnabled}
        onRunScan={() => {
          hasAutoCollapsed.current = false;
          runScan({ region, services: selectedServices, mode: scanMode, forceRefresh, includeCosts: costOverlayEnabled }).catch(() => {});
        }}
        onStopScan={() => stopScan().catch((scanError) => setError(scanError instanceof Error ? scanError.message : String(scanError)))}
        scanLoading={scanLoading}
        jobStatus={jobStatus}
        statusLabel={formatJobStatusLabel(jobStatus)}
        forceRefresh={forceRefresh}
        onForceRefreshChange={setForceRefresh}
        warnings={jobStatus?.warnings || []}
        scanFilterMode={scanFilterMode}
        onScanFilterModeChange={handleScanFilterModeChange}
        tagDiscovery={tagDiscovery}
        onScanByTags={handleScanByTags}
        tagScanLoading={tagScanLoading}
        onOpenTerraformFilePanel={() => setShowTerraformFilePanel((v) => !v)}
        terraformLoaded={terraform.lastResult != null}
        terraformResourceCount={terraform.lastResult?.resource_count ?? 0}
        terraformFileCount={terraform.files.length}
      />

      <TerraformFilePanel
        open={showTerraformFilePanel}
        onClose={() => setShowTerraformFilePanel(false)}
        files={terraform.files}
        onAddFiles={terraform.addFiles}
        onRemoveFile={terraform.removeFile}
        onClearFiles={() => {
          terraform.clearFiles();
          injectGraph(`tf-cleared-${Date.now()}`, { nodes: [], edges: [], metadata: {} });
        }}
        onParse={handleTerraformParsed}
        loading={terraform.loading}
        error={terraform.error}
      />

      <div className="cloudwire-layout">
        <ErrorBoundary name="Sidebar">
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
        </ErrorBoundary>

        <main className="graph-stage-shell">
          {/* Terraform full-canvas drop zone — use lastResult (not graphNodes.length)
              to avoid a brief flash where loading=false but graphData hasn't rendered yet */}
          {scanFilterMode === "terraform" && !terraform.lastResult && !terraform.loading && (
            <TerraformDropZone onFilesAccepted={handleTerraformFilesAccepted} />
          )}

          {bootstrapLoading && graphNodes.length === 0 && scanFilterMode !== "terraform" && (
            <div className="graph-stage-loading">Connecting to backend...</div>
          )}

          {(scanLoading || tagScanLoading || terraform.loading) && (
            <div className="graph-scan-loading-overlay">
              <div className="graph-scan-loading-spinner" />
              <span className="graph-scan-loading-label">
                {terraform.loading ? "Parsing Terraform state..." : tagScanLoading ? "Discovering resources..." : jobStatus?.current_service ? `Scanning ${jobStatus.current_service}...` : "Scanning..."}
              </span>
              {jobStatus?.progress_percent > 0 && (
                <div className="graph-scan-loading-progress">
                  <div className="graph-scan-loading-progress-fill" style={{ width: `${jobStatus.progress_percent}%` }} />
                </div>
              )}
            </div>
          )}

          {!bootstrapLoading && !scanLoading && graphNodes.length === 0 && visibleNodes.length === 0 && jobStatus?.status === "completed" && (
            <div className="graph-empty-state">
              <div className="graph-empty-title">No resources found</div>
              <div className="graph-empty-hint">
                {scanFilterMode === "terraform"
                  ? "The uploaded Terraform state files contained no recognizable AWS resources."
                  : scanFilterMode === "tags"
                  ? "No resources matched the selected tags in this region. Try different tag filters or check that your resources are tagged."
                  : selectedServices.length === 0
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
                onClick={() => togglePathFinderMode(selectedNodeId)}
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
            <LayoutDropdown layoutMode={layoutMode} onLayoutModeChange={changeLayout} />
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
              ▶ ANIMATE
            </button>
            <button
              className="graph-toolbar-btn"
              onClick={() => graphRef.current?.exportSvg()}
              title="Export graph as SVG"
            >
              EXPORT SVG
            </button>
          </div>

          <ErrorBoundary name="Graph" resetKey={fitKey}>
            <GraphCanvas
              ref={graphRef}
              nodes={graphNodes}
              edges={graphEdges}
              annotations={laidOutGraph.annotations}
              selectedNodeId={selectedNodeId}
              onSelectNode={handleNodeSelect}
              costOverlayEnabled={costOverlayEnabled}
              onClearSelection={() => {
                resourceRequestTokenRef.current += 1;
                setSelectedNodeId(null);
                setResourceDetails(null);
                if (pathFinderMode) { resetPathFinder(); }
              }}
              fitKey={fitKey}
              animated={showFlowAnimation}
              pathNodeIds={pathNodeIds}
              pathEdgeIds={pathEdgeIds}
              blastRadius={blastRadius}
              onAnnotationClick={handleAnnotationClick}
              collapsedContainers={collapsedContainers}
              onHoverNode={handleHoverNode}
            />
          </ErrorBoundary>

          {error && <div className="graph-stage-error">{error}</div>}
          {resourceError && <div className="graph-stage-error graph-stage-error--resource">{resourceError}</div>}
          {pathNotFound && <div className="graph-stage-error graph-stage-error--info">No directed path found between these nodes.</div>}

        </main>

        {resourceDetails && (
          <ErrorBoundary name="Inspector">
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
          </ErrorBoundary>
        )}
      </div>

      {jobStatus?.warnings?.length > 0 && (
        <WarningsPanel key={currentJobId} warnings={jobStatus.warnings} />
      )}
    </div>
  );
}
