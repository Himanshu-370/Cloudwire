import React, {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from "react";
import { useGraphViewport } from "../../hooks/useGraphViewport";
import { getServiceVisual } from "../../lib/serviceVisuals.jsx";
import { classifyNodeRole } from "../../lib/graphTransforms";
import { GraphEdge } from "./GraphEdge";
import { getNodeFrame, GraphNode } from "./GraphNode";
import { GraphLegend } from "./GraphLegend";
import { Minimap } from "./Minimap";

export const ViewportScaleContext = React.createContext(1);

function buildNodeMap(nodes) {
  return new Map(nodes.map((node) => [node.id, node]));
}

function highlightedNodeIds(selectedNodeId, hoveredNodeId, edges) {
  const ids = new Set();
  if (selectedNodeId) ids.add(selectedNodeId);
  if (hoveredNodeId) ids.add(hoveredNodeId);
  edges.forEach((edge) => {
    if (selectedNodeId && (edge.source === selectedNodeId || edge.target === selectedNodeId)) {
      ids.add(edge.source);
      ids.add(edge.target);
    }
    if (hoveredNodeId && (edge.source === hoveredNodeId || edge.target === hoveredNodeId)) {
      ids.add(edge.source);
      ids.add(edge.target);
    }
  });
  return ids;
}

export const GraphCanvas = forwardRef(function GraphCanvas(
  {
    nodes,
    edges,
    annotations,
    selectedNodeId,
    onSelectNode,
    onHoverNode,
    onClearSelection,
    fitKey,
    animated,
    pathNodeIds,
    pathEdgeIds,
    blastRadius,
  },
  ref
) {
  const containerRef = useRef(null);
  const svgRef = useRef(null);
  const dragRef = useRef(null);
  const panRef = useRef(null);
  const fitTimersRef = useRef([]);
  const [hoveredNodeId, setHoveredNodeId] = useState(null);
  const [hoveredEdgeId, setHoveredEdgeId] = useState(null);
  const [localPositions, setLocalPositions] = useState({});
  const { viewport, setViewport, screenToGraph, zoomAtPoint, fitToNodes, centerNode, resetView } = useGraphViewport();

  useEffect(() => {
    setLocalPositions((previous) => {
      const next = {};
      nodes.forEach((node) => {
        next[node.id] = previous[node.id] || node.position;
      });
      return next;
    });
  }, [nodes]);

  const nodesWithPositions = useMemo(
    () =>
      nodes.map((node) => {
        const frame = getNodeFrame(node, selectedNodeId === node.id);
        return {
          ...node,
          position: localPositions[node.id] || node.position,
          width: frame.width,
          height: frame.height,
        };
      }),
    [localPositions, nodes, selectedNodeId]
  );

  const nodeMap = useMemo(() => buildNodeMap(nodesWithPositions), [nodesWithPositions]);

  const nodeRoles = useMemo(() => {
    const map = {};
    nodesWithPositions.forEach((n) => { map[n.id] = classifyNodeRole(n, edges); });
    return map;
  }, [nodesWithPositions, edges]);

  const visibleNodeSet = useMemo(() => {
    if (!containerRef.current) return new Set(nodesWithPositions.map((n) => n.id));
    const { clientWidth, clientHeight } = containerRef.current;
    const buffer = 300;
    const minX = -viewport.x / viewport.scale - buffer;
    const maxX = (clientWidth - viewport.x) / viewport.scale + buffer;
    const minY = -viewport.y / viewport.scale - buffer;
    const maxY = (clientHeight - viewport.y) / viewport.scale + buffer;
    return new Set(
      nodesWithPositions
        .filter((n) => n.position.x >= minX && n.position.x <= maxX && n.position.y >= minY && n.position.y <= maxY)
        .map((n) => n.id)
    );
  }, [nodesWithPositions, viewport]);

  const renderNodes = useMemo(
    () => nodesWithPositions.filter((n) => visibleNodeSet.has(n.id)),
    [nodesWithPositions, visibleNodeSet]
  );

  const renderEdges = useMemo(
    () => edges.filter((e) => visibleNodeSet.has(e.source) || visibleNodeSet.has(e.target)),
    [edges, visibleNodeSet]
  );

  const emphasizedIds = useMemo(
    () => highlightedNodeIds(selectedNodeId, hoveredNodeId, edges),
    [selectedNodeId, hoveredNodeId, edges]
  );

  const clearFitTimers = useCallback(() => {
    fitTimersRef.current.forEach((timer) => window.clearTimeout(timer));
    fitTimersRef.current = [];
  }, []);

  const fitGraph = useCallback(() => {
    if (!containerRef.current || !nodesWithPositions.length) return;
    clearFitTimers();
    const run = () => fitToNodes(containerRef.current, nodesWithPositions);
    window.requestAnimationFrame(run);
    fitTimersRef.current.push(window.setTimeout(run, 80));
    fitTimersRef.current.push(window.setTimeout(run, 220));
  }, [clearFitTimers, fitToNodes, nodesWithPositions]);

  // Keep a stable ref so the fit effect can always call the latest fitGraph
  // without listing fitGraph as a dependency (which would re-trigger on every
  // node selection, resetting the user's zoom/pan).
  const fitGraphRef = useRef(fitGraph);
  fitGraphRef.current = fitGraph;

  const handleMinimapPan = useCallback(
    (graphX, graphY) => {
      if (!containerRef.current) return;
      setViewport((prev) => ({
        ...prev,
        x: containerRef.current.clientWidth / 2 - graphX * prev.scale,
        y: containerRef.current.clientHeight / 2 - graphY * prev.scale,
      }));
    },
    [setViewport]
  );

  const exportSvg = useCallback(() => {
    if (!svgRef.current || !containerRef.current) return;
    const { clientWidth, clientHeight } = containerRef.current;
    const clone = svgRef.current.cloneNode(true);
    clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
    clone.setAttribute("width", String(clientWidth));
    clone.setAttribute("height", String(clientHeight));
    const bg = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    bg.setAttribute("width", String(clientWidth));
    bg.setAttribute("height", String(clientHeight));
    bg.setAttribute("fill", "#060a0f");
    clone.insertBefore(bg, clone.firstChild);
    const serializer = new XMLSerializer();
    const svgStr = serializer.serializeToString(clone);
    const blob = new Blob([svgStr], { type: "image/svg+xml" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "cloudgraph.svg";
    a.click();
    URL.revokeObjectURL(url);
  }, []);

  // Only re-fit when the graph data itself changes (fitKey), not on every
  // selectedNodeId change that would update nodesWithPositions / fitGraph.
  useEffect(() => {
    if (!nodesWithPositions.length) return undefined;
    fitGraphRef.current();
    return () => clearFitTimers();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fitKey, clearFitTimers]);

  useImperativeHandle(
    ref,
    () => ({
      fitGraph,
      resetView,
      zoomIn: () => zoomAtPoint(1.18, { x: containerRef.current?.clientWidth / 2 || 0, y: containerRef.current?.clientHeight / 2 || 0 }),
      zoomOut: () => zoomAtPoint(0.84, { x: containerRef.current?.clientWidth / 2 || 0, y: containerRef.current?.clientHeight / 2 || 0 }),
      focusNode: (nodeId) => {
        const node = nodeMap.get(nodeId);
        if (containerRef.current && node) centerNode(containerRef.current, node, node.width, node.height, 1.02);
      },
      exportSvg,
    }),
    [centerNode, exportSvg, fitGraph, nodeMap, resetView, zoomAtPoint]
  );

  useEffect(() => {
    function onMouseMove(event) {
      if (dragRef.current && svgRef.current) {
        const { nodeId, offset, startClient } = dragRef.current;
        if (startClient) {
          const dx = event.clientX - startClient.x;
          const dy = event.clientY - startClient.y;
          if (Math.abs(dx) > 4 || Math.abs(dy) > 4) {
            dragRef.current.hasDragged = true;
          }
        }
        const point = screenToGraph(event.clientX, event.clientY, svgRef.current.getBoundingClientRect());
        setLocalPositions((previous) => ({
          ...previous,
          [nodeId]: { x: point.x - offset.x, y: point.y - offset.y },
        }));
      } else if (panRef.current) {
        const dx = event.clientX - panRef.current.startX;
        const dy = event.clientY - panRef.current.startY;
        setViewport({
          ...panRef.current.startViewport,
          x: panRef.current.startViewport.x + dx,
          y: panRef.current.startViewport.y + dy,
        });
      }
    }

    function onMouseUp() {
      dragRef.current = null;
      panRef.current = null;
    }

    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup", onMouseUp);
    return () => {
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup", onMouseUp);
    };
  }, [screenToGraph, setViewport]);

  const handleCanvasMouseDown = useCallback(
    (event) => {
      if (event.target.closest("[data-node-card='true']")) return;
      panRef.current = {
        startX: event.clientX,
        startY: event.clientY,
        startViewport: viewport,
      };
      onClearSelection?.();
    },
    [onClearSelection, viewport]
  );

  const handleWheel = useCallback(
    (event) => {
      event.preventDefault();
      if (!containerRef.current) return;
      const rect = containerRef.current.getBoundingClientRect();
      zoomAtPoint(event.deltaY < 0 ? 1.1 : 0.9, {
        x: event.clientX - rect.left,
        y: event.clientY - rect.top,
      });
    },
    [zoomAtPoint]
  );

  // Attach wheel listener imperatively with { passive: false } so
  // event.preventDefault() works and the page doesn't scroll while zooming.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return undefined;
    el.addEventListener("wheel", handleWheel, { passive: false });
    return () => el.removeEventListener("wheel", handleWheel);
  }, [handleWheel]);

  return (
    <div ref={containerRef} className="graph-canvas">
      <div className="graph-canvas-backdrop" />
      <div className="graph-canvas-grid" />
      <div className="graph-canvas-footer">ZOOM {Math.round(viewport.scale * 100)}% · DRAG TO PAN · SCROLL TO ZOOM · CLICK NODE TO INSPECT</div>
      <svg ref={svgRef} className="graph-svg" onMouseDown={handleCanvasMouseDown} onDoubleClick={fitGraph}>
        <defs>
          {nodesWithPositions.map((node) => {
            const visual = getServiceVisual(node.service);
            return (
              <marker key={node.id} id={`arrow-${node.id}`} markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto">
                <path d="M0,0 L0,7 L7,3.5 z" fill={visual.color} fillOpacity="0.7" />
              </marker>
            );
          })}
        </defs>
        <ViewportScaleContext.Provider value={viewport.scale}>
        <g transform={`translate(${viewport.x},${viewport.y}) scale(${viewport.scale})`}>
          {annotations.map((annotation) => (
            <g key={annotation.id} className={`graph-annotation ${annotation.tone}`}>
              <rect
                x={annotation.minX}
                y={annotation.minY}
                width={annotation.maxX - annotation.minX}
                height={annotation.maxY - annotation.minY}
                rx="28"
              />
              <text x={annotation.minX + 18} y={annotation.minY + 26}>{annotation.title}</text>
              <text x={annotation.minX + 18} y={annotation.minY + 44} className="graph-annotation-subtitle">{annotation.subtitle}</text>
            </g>
          ))}

          {renderEdges.map((edge) => {
            const sourceNode = nodeMap.get(edge.source);
            const targetNode = nodeMap.get(edge.target);
            if (!sourceNode || !targetNode) return null;
            const isPathMode = pathNodeIds && pathNodeIds.size > 0;
            const edgeHighlighted = isPathMode
              ? (pathEdgeIds ? pathEdgeIds.has(edge.id) : false)
              : (!selectedNodeId && !hoveredNodeId ? true : emphasizedIds.has(edge.source) && emphasizedIds.has(edge.target));
            const edgeBlast = blastRadius
              ? (blastRadius.upstream.has(edge.source) && blastRadius.upstream.has(edge.target) ? "up"
                : blastRadius.downstream.has(edge.source) && blastRadius.downstream.has(edge.target) ? "down"
                : null)
              : null;
            return (
              <g key={edge.id} onMouseEnter={() => setHoveredEdgeId(edge.id)} onMouseLeave={() => setHoveredEdgeId(null)}>
                <GraphEdge
                  edge={edge}
                  sourceNode={sourceNode}
                  targetNode={targetNode}
                  highlighted={edgeHighlighted}
                  hovered={hoveredEdgeId === edge.id}
                  showLabel={hoveredEdgeId === edge.id && edgeHighlighted}
                  animated={animated}
                  pathHighlight={isPathMode && pathEdgeIds ? pathEdgeIds.has(edge.id) : false}
                  blastEdge={edgeBlast}
                />
              </g>
            );
          })}

          {renderNodes.map((node) => {
            const isPathMode = pathNodeIds && pathNodeIds.size > 0;
            const isBlastMode = blastRadius && (blastRadius.upstream.size > 0 || blastRadius.downstream.size > 0);
            const highlighted = isPathMode
              ? pathNodeIds.has(node.id)
              : isBlastMode
              ? (blastRadius.upstream.has(node.id) || blastRadius.downstream.has(node.id) || selectedNodeId === node.id)
              : (!selectedNodeId && !hoveredNodeId ? true : emphasizedIds.has(node.id));
            const blastHighlight = isBlastMode
              ? (selectedNodeId === node.id ? "center" : blastRadius.upstream.has(node.id) ? "upstream" : blastRadius.downstream.has(node.id) ? "downstream" : null)
              : null;
            const hovered = hoveredNodeId === node.id;
            return (
              <g
                key={node.id}
                data-node-card="true"
                onMouseEnter={() => {
                  setHoveredNodeId(node.id);
                  onHoverNode?.(node.id);
                }}
                onMouseLeave={() => {
                  setHoveredNodeId(null);
                  onHoverNode?.(null);
                }}
                onMouseDown={(event) => {
                  event.stopPropagation();
                  if (!svgRef.current) return;
                  const point = screenToGraph(event.clientX, event.clientY, svgRef.current.getBoundingClientRect());
                  dragRef.current = {
                    nodeId: node.id,
                    offset: {
                      x: point.x - node.position.x,
                      y: point.y - node.position.y,
                    },
                    hasDragged: false,
                    startClient: { x: event.clientX, y: event.clientY },
                  };
                }}
                onClick={(event) => {
                  event.stopPropagation();
                  if (dragRef.current?.hasDragged) return;
                  onSelectNode?.(node.id);
                }}
                onDoubleClick={(event) => {
                  event.stopPropagation();
                  onSelectNode?.(node.id);
                  if (containerRef.current) centerNode(containerRef.current, node, node.width, node.height, 1.06);
                }}
                style={{ cursor: "grab" }}
              >
                <GraphNode
                  node={node}
                  selected={selectedNodeId === node.id}
                  highlighted={highlighted}
                  hovered={hovered}
                  role={nodeRoles[node.id]}
                  blastHighlight={blastHighlight}
                />
              </g>
            );
          })}
        </g>
        </ViewportScaleContext.Provider>
      </svg>
      <div className="graph-canvas-corner">
        <Minimap
          nodes={nodesWithPositions}
          viewport={viewport}
          containerRef={containerRef}
          onPan={handleMinimapPan}
        />
        <GraphLegend />
      </div>
    </div>
  );
});
