import React from "react";
import { getServiceVisual } from "../../lib/serviceVisuals.jsx";

function nodeRadius(node) {
  const size = Math.min(node.width || 88, node.height || 88);
  return size / 2 - 6;
}

function boundaryPoint(from, to, radius) {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const dist = Math.sqrt(dx * dx + dy * dy);
  if (dist < 1) return from;
  return { x: from.x + (dx / dist) * radius, y: from.y + (dy / dist) * radius };
}

function curvedPath(source, target) {
  const dx = target.x - source.x;
  const dy = target.y - source.y;
  const dist = Math.sqrt(dx * dx + dy * dy);
  const absDx = Math.abs(dx);
  const absDy = Math.abs(dy);

  // For mostly-horizontal edges (left→right flow), use smooth horizontal bezier
  if (absDx > absDy * 0.6 && absDx > 60) {
    const cx1 = source.x + dx * 0.4;
    const cy1 = source.y;
    const cx2 = target.x - dx * 0.4;
    const cy2 = target.y;
    const labelX = source.x + dx * 0.5;
    const labelY = source.y + dy * 0.5 - 4;
    return { path: `M${source.x},${source.y} C${cx1},${cy1} ${cx2},${cy2} ${target.x},${target.y}`, labelX, labelY };
  }

  // For short or vertical edges, use a gentle curve offset
  const curvature = Math.min(0.2, 30 / (dist || 1));
  const mx = source.x + dx * 0.5;
  const my = source.y + dy * 0.5;
  const cx = mx - dy * curvature;
  const cy = my + dx * curvature;
  return { path: `M${source.x},${source.y} Q${cx},${cy} ${target.x},${target.y}`, labelX: cx, labelY: cy };
}

export function GraphEdge({ edge, sourceNode, targetNode, highlighted, hovered, showLabel, animated, pathHighlight, blastEdge }) {
  const sourceVisual = getServiceVisual(sourceNode?.service);

  // Color override based on mode
  let color = sourceVisual.color;
  if (pathHighlight) color = "#ffffff";
  else if (blastEdge === "up") color = "#ff9900";
  else if (blastEdge === "down") color = "#00e7ff";

  const sourceRadius = nodeRadius(sourceNode);
  const targetRadius = nodeRadius(targetNode);
  const sourcePos = boundaryPoint(sourceNode.position, targetNode.position, sourceRadius);
  const targetPos = boundaryPoint(targetNode.position, sourceNode.position, targetRadius);

  const { path, labelX, labelY } = curvedPath(sourcePos, targetPos);

  const pathId = `edge-path-${edge.id}`;
  const strokeWidth = pathHighlight ? 2.5 : hovered ? 2 : 1;
  const opacity = pathHighlight ? 1 : (highlighted ? (hovered ? 0.9 : 0.45) : 0.08);
  const dashArray = pathHighlight || hovered ? "0" : "4,4";
  const animDuration = 1.4 + (Math.abs(edge.id?.charCodeAt(0) || 0) % 10) * 0.08;

  return (
    <g>
      <path
        id={pathId}
        d={path}
        fill="none"
        stroke={color}
        strokeWidth={strokeWidth}
        opacity={opacity}
        strokeDasharray={dashArray}
        markerEnd={`url(#arrow-${sourceNode.service})`}
        className="graph-edge-path"
      />
      {animated && highlighted && (
        <circle r="3.5" fill={color} opacity="0.8">
          <animateMotion dur={`${animDuration}s`} repeatCount="indefinite">
            <mpath href={`#${pathId}`} />
          </animateMotion>
        </circle>
      )}
      {(showLabel || pathHighlight) && edge.relationship && (
        <text
          x={labelX}
          y={labelY - 8}
          textAnchor="middle"
          fontSize="10"
          fill={color}
          opacity="0.9"
          letterSpacing="0.04em"
        >
          {String(edge.relationship || edge.label || "")}
        </text>
      )}
    </g>
  );
}
