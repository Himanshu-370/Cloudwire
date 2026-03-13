import { useCallback, useRef, useState } from "react";

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

export function useGraphViewport() {
  const [viewport, setViewportState] = useState({ x: 0, y: 0, scale: 1 });
  const viewportRef = useRef({ x: 0, y: 0, scale: 1 });

  const setViewport = useCallback((next) => {
    const value = typeof next === "function" ? next(viewportRef.current) : next;
    viewportRef.current = value;
    setViewportState(value);
  }, []);

  // Stable - reads from ref, no viewport state dependency
  const screenToGraph = useCallback((clientX, clientY, bounds) => ({
    x: (clientX - bounds.left - viewportRef.current.x) / viewportRef.current.scale,
    y: (clientY - bounds.top - viewportRef.current.y) / viewportRef.current.scale,
  }), []);

  const zoomAtPoint = useCallback((delta, point) => {
    setViewport((previous) => {
      const nextScale = clamp(previous.scale * delta, 0.18, 2.8);
      return {
        scale: nextScale,
        x: point.x - (point.x - previous.x) * (nextScale / previous.scale),
        y: point.y - (point.y - previous.y) * (nextScale / previous.scale),
      };
    });
  }, [setViewport]);

  const panBy = useCallback((deltaX, deltaY) => {
    setViewport((previous) => ({
      ...previous,
      x: previous.x + deltaX,
      y: previous.y + deltaY,
    }));
  }, [setViewport]);

  const fitToNodes = useCallback((container, nodes) => {
    if (!container || !nodes.length) return;
    const width = container.clientWidth;
    const height = container.clientHeight;
    if (!width || !height) return;

    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const node of nodes) {
      const hw = (node.width || 120) / 2;
      const hh = (node.height || 80) / 2;
      const left = node.position.x - hw;
      const right = node.position.x + hw;
      const top = node.position.y - hh;
      const bottom = node.position.y + hh;
      if (left < minX) minX = left;
      if (right > maxX) maxX = right;
      if (top < minY) minY = top;
      if (bottom > maxY) maxY = bottom;
    }

    const graphWidth = Math.max(1, maxX - minX);
    const graphHeight = Math.max(1, maxY - minY);
    const fitScale = Math.min(width / (graphWidth + 180), height / (graphHeight + 180));

    // If everything fits at readable size (>=0.55), use fit-to-graph.
    // Otherwise, center on the graph at a readable scale so nodes aren't tiny.
    const MIN_READABLE_SCALE = 0.55;
    const scale = clamp(Math.max(fitScale, MIN_READABLE_SCALE), 0.18, 1.45);
    const centeredX = width / 2 - ((minX + maxX) / 2) * scale;
    const centeredY = height / 2 - ((minY + maxY) / 2) * scale;

    setViewport({ x: centeredX, y: centeredY, scale });
  }, [setViewport]);

  const centerNode = useCallback((container, node, _width, _height, zoom = 1.08) => {
    if (!container || !node) return;
    const nextScale = clamp(zoom, 0.18, 2.8);
    // node.position is the center of the node; place it at the screen center.
    setViewport({
      scale: nextScale,
      x: container.clientWidth / 2 - node.position.x * nextScale,
      y: container.clientHeight / 2 - node.position.y * nextScale,
    });
  }, [setViewport]);

  const resetView = useCallback(() => {
    setViewport({ x: 0, y: 0, scale: 1 });
  }, [setViewport]);

  return {
    viewport,
    setViewport,
    screenToGraph,
    zoomAtPoint,
    panBy,
    fitToNodes,
    centerNode,
    resetView,
  };
}
