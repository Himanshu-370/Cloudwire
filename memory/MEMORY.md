# CloudWire Project Memory

## Project Overview
**CloudWire** is an AWS resource visualization tool. It scans an AWS account and renders an interactive graph of services and their relationships.

## Architecture
- **Backend**: Python FastAPI (`backend/app/`) — scans AWS using boto3, builds a directed graph, exposes REST API
- **Frontend**: React + Vite (`frontend/src/`) — SVG canvas graph visualization, no external graph library

## Backend Structure

### Files
- `main.py` — FastAPI app, all REST endpoints, error handling, AWS credential resolution
- `models.py` — Pydantic models for request/response shapes
- `scanner.py` — `AWSGraphScanner`: per-service scanners, parallel execution with `ThreadPoolExecutor`
- `scan_jobs.py` — `ScanJobStore`: job lifecycle management (queued → running → completed/failed/cancelled), caching
- `graph_store.py` — `GraphStore`: wraps `networkx.DiGraph`, thread-safe add/query, serializes to `{nodes, edges, metadata}`

### API Endpoints
- `GET /` — health check
- `POST /scan` — start async scan job (202 Accepted), returns `job_id`
- `GET /scan/{job_id}` — poll job status + progress
- `GET /scan/{job_id}/graph` — get current graph for a job
- `POST /scan/{job_id}/stop` — request cancellation
- `GET /graph` — latest completed graph
- `GET /resource/{resource_id}?job_id=` — single resource with incoming/outgoing edges

### Scan Modes
- **quick**: no IAM inference, no resource describe enrichment; 300s cache TTL
- **deep**: includes IAM policy parsing and resource describe calls; 1800s cache TTL

### Supported Services (specialized scanners)
- `apigateway` — HTTP APIs (v2) and REST APIs; follows integrations to Lambda
- `lambda` — list functions, event source mappings; optional IAM role policy inference
- `sqs` — list queues; optional `get_queue_attributes` in deep mode
- `eventbridge` — list rules + targets; edges from rules to target ARNs
- `dynamodb` — list tables; optional `describe_table` in deep mode
- Generic fallback: uses `resourcegroupstaggingapi`

### Caching & Job Store
- Cache key = `account_id|region|sorted_services|mode|iam=0/1|describe=0/1`
- In-flight deduplication: reuses running job if same cache key
- Job results cached for 300s (quick) or 1800s (deep) after completion

## Frontend Structure

### Files
- `pages/CloudWirePage.jsx` — top-level page; owns all state, computes layout, orchestrates subcomponents
- `hooks/useScanPolling.js` — scan lifecycle: POST /scan → poll /scan/{id} with adaptive delays (1s/2s/3s)
- `hooks/useGraphViewport.js` — pan/zoom viewport state; fit-to-nodes, center-node, zoom-at-point
- `lib/graphTransforms.js` — layout algorithms: `layoutHybridGraph` (circular/flow), `splitByConnectivity`, `buildLevels`, `filterGraphByRegion`, `countServices`
- `lib/serviceVisuals.jsx` — service → color/label/icon mappings; 9 known services + unknown fallback
- `lib/awsRegions.js` — list of 29 AWS regions; default = `us-west-2`
- `components/graph/GraphCanvas.jsx` — SVG canvas with pan/drag/zoom; `forwardRef` exposing `fitGraph`, `zoomIn/Out`, `focusNode`, `resetView`
- `components/graph/GraphNode.jsx` — renders a node as circle/hexagon with icon, label, status dot
- `components/graph/GraphEdge.jsx` — curved quadratic bezier path with arrow marker
- `components/graph/GraphLegend.jsx` — legend overlay
- `components/graph/CanvasControls.jsx` — canvas control buttons
- `components/layout/TopBar.jsx` — region select, services input, scan mode, scan/stop buttons, progress bar
- `components/layout/ServiceSidebar.jsx` — service filter pills, stats, view controls, resource search list
- `components/layout/InspectorPanel.jsx` — right panel: selected node metadata + incoming/outgoing connections

### Layout Pipeline (CloudWirePage)
1. Raw graph from API → `normalizeGraph` (service name aliases)
2. `filterGraphByRegion` — filter by selected region
3. Filter by hidden services → `visibleNodes` / `visibleEdges`
4. `layoutHybridGraph` → positions nodes in circular groups + isolated column
5. Search filter on `graphNodes` (limit 120)
6. `fitKey` memo triggers re-fit on graph change

### API Communication
- Base URL: `VITE_API_BASE_URL` env var || `http://localhost:8000`
- Adaptive poll delays: 1s for first 30s, 2s up to 60s, then 3s
- Token-based cancellation of stale requests (ref counter pattern)

## Dependencies
### Backend
- `fastapi==0.115.12`, `uvicorn[standard]==0.34.0`
- `boto3==1.37.12`, `networkx==3.4.2`, `pydantic==2.11.1`
### Frontend
- React + Vite, Tailwind CSS, PostCSS
- No external graph visualization library (pure SVG)

## Recent Changes (all fixes implemented)
- Backend: lifespan shutdown for ThreadPoolExecutor; job pruning (max 50 terminal jobs); `get_status_payload` no longer leaks `graph_store` in snapshot dict; `_event` → `event` in progress callback
- Backend scanner: `_node()` helper adds `region=self._region` to every node; wildcard `*` IAM resources filtered (no phantom nodes); API Gateway v2 integrations parallelized
- Frontend hooks: `useGraphViewport.screenToGraph` is stable (reads from ref, empty deps); `useScanPolling` exports `bootstrapLoading`, accepts `forceRefresh` param
- Frontend graph: edge arrows terminate at node circumference not center; status dot uses `state||status`, neutral when absent; drag vs click separated (4px threshold); GraphLegend shows all services
- Frontend lib: `buildLevels` groups unreachable nodes at same fallback level (not unique columns)
- Frontend UI: layout mode toggle (circular↔flow); force refresh checkbox; scan warnings banner; bootstrap loading overlay; region+services persisted to localStorage; truncation count in sidebar; ErrorBoundary wraps app; annotation rects visible; inspector is overlay at <1200px (not hidden); CanvasControls.jsx deleted

## Architecture Understanding Features (Round 3 — all implemented)
- `graphTransforms.js`: `classifyNodeRole`, `layoutSwimlane`, `findShortestPath`, `computeBlastRadius`, `detectPatterns`, `generateArchitectureSummary`
- `serviceVisuals.jsx`: added `description` + `role` to every service; `getServiceRole()` export
- `GraphEdge.jsx`: `animated` (animateMotion pulse), `pathHighlight` (bright white path), `blastEdge` ('up'=orange/'down'=cyan)
- `GraphNode.jsx`: `role` badge (TRIGGER/PROC/STORE/QUEUE at scale≥0.55), `blastHighlight` ring (orange=upstream, cyan=downstream), educational tooltip on hover
- `GraphCanvas.jsx`: accepts `animated`, `pathNodeIds`, `pathEdgeIds`, `blastRadius`; `nodeRoles` memo; `exportSvg()` in imperative handle
- `CloudWirePage.jsx`: pathFinderMode, pathSource, foundPath, blastRadiusMode, showFlowAnimation, showSummary state; swimlane layout mode; summary panel; patterns panel; graph toolbar (SUMMARY / ▶ FLOW / PATH FINDER / EXPORT SVG); focus bar adds BLAST RADIUS button
- `TopBar.jsx`: layout cycles circular → flow → swimlane
- `graph.css`: all font sizes increased ~2px; swimlane lane tones; graph toolbar; summary panel; path finder styles
- **Font sizes**: 9px→11px, 10px→12px, 11px→13px, 12px→14px, 13px→15px throughout

## Declutter Features (Round 2 — all implemented)
- `partitionByConnectivity` / `buildClusteredGraph` / `computeFocusSubgraph` added to `graphTransforms.js`
- `Minimap.jsx` created — 180×110 thumbnail with service-colored dots + orange viewport rect; click-to-pan
- `GraphCanvas.jsx`: exports `ViewportScaleContext`; viewport virtualization (300px buffer culling); Minimap wired via `handleMinimapPan`
- `GraphNode.jsx`: LOD (scale<0.28 → tiny dot; scale<0.45 → hide labels); cluster node renders dashed circle with count + service name
- `CloudWirePage.jsx`: full pipeline — partitionByConnectivity → showIsolated filter → buildClusteredGraph → computeFocusSubgraph → layoutHybridGraph; auto-collapse >8 nodes per service; focus mode bar (1/2/3 hop depth); FOCUS/EXIT FOCUS toggle
- `ServiceSidebar.jsx`: cluster toggle `⊟`/`⊞` on each service pill; isolated nodes toggle section
- `graph.css`: minimap styles, focus mode bar + depth/toggle buttons, cluster count text, sidebar cluster/isolated toggle styles

## Key Patterns
- All AWS boto3 calls use adaptive retry config (max 10 attempts)
- Thread safety: `Lock` in `GraphStore`, `ScanJobStore`
- Cancellation: cooperative via `should_cancel` lambda + `ScanCancelledError`
- IAM policy dependency inference maps `dynamodb:`, `sqs:`, `events:`, `lambda:` actions to service edges
