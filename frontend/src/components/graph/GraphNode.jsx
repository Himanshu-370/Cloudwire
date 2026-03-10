import React, { useContext, useState } from "react";
import { createServiceIcon, getServiceVisual } from "../../lib/serviceVisuals.jsx";
import { ViewportScaleContext } from "./GraphCanvas";

function compactText(value, maxLength = 22) {
  const text = String(value || "");
  if (text.length <= maxLength) return text;
  return `${text.slice(0, maxLength - 3)}...`;
}

export const NODE_DIMENSIONS = {
  regular: { width: 88, height: 88, radius: 26 },
  group: { width: 108, height: 108, radius: 34 },
  selected: { width: 96, height: 96, radius: 30 },
  cluster: { width: 112, height: 112, radius: 38 },
};

export function getNodeFrame(node, selected) {
  if (selected) return NODE_DIMENSIONS.selected;
  if (String(node.type || "").toLowerCase() === "cluster") return NODE_DIMENSIONS.cluster;
  if (String(node.type || "").toLowerCase() === "group") return NODE_DIMENSIONS.group;
  return NODE_DIMENSIONS.regular;
}

const ROLE_META = {
  trigger:   { color: "#ff9900", label: "TRIGGER",  width: 42 },
  processor: { color: "#00e7ff", label: "PROC",     width: 32 },
  storage:   { color: "#7b2d8b", label: "STORE",    width: 34 },
  queue:     { color: "#ff4f8b", label: "QUEUE",    width: 34 },
  unknown:   { color: "#6f8596", label: "?",        width: 14 },
};

export function GraphNode({ node, selected, highlighted, hovered, role, blastHighlight }) {
  const scale = useContext(ViewportScaleContext);
  const [tooltipVisible, setTooltipVisible] = useState(false);
  const visual = getServiceVisual(node.service);
  const frame = getNodeFrame(node, selected);
  const left = node.position.x - frame.width / 2;
  const top = node.position.y - frame.height / 2;
  const centerX = frame.width / 2;
  const centerY = frame.height / 2;
  const outerRadius = Math.min(frame.width, frame.height) / 2 - 6;
  const innerRadius = outerRadius * 0.46;
  const isCluster = String(node.type || "").toLowerCase() === "cluster";

  // Blast radius overrides opacity
  const effectiveHighlighted = blastHighlight ? true : highlighted;

  // Tiny dot LOD at very low zoom
  if (scale < 0.28 && !selected) {
    return (
      <g transform={`translate(${left}, ${top})`} opacity={effectiveHighlighted ? 0.85 : 0.12}>
        <circle cx={centerX} cy={centerY} r={isCluster ? 9 : 5} fill={visual.color} opacity={0.75} />
        {isCluster && (
          <text x={centerX} y={centerY + 4} textAnchor="middle" fontSize="7" fill={visual.color} fontWeight="600">
            {node.count || "?"}
          </text>
        )}
      </g>
    );
  }

  const nodeState = String(node.state || node.status || "").toLowerCase();
  const statusColor = ["active", "running", "available", "deployed", "enabled", "enable", "in service"].includes(nodeState)
    ? "#00ff88"
    : ["inactive", "failed", "error", "disabled", "deleting", "unavailable"].includes(nodeState)
    ? "#ff6677"
    : nodeState
    ? "#f0a500"
    : "#3a5a6a";

  const icon = createServiceIcon(node.service, visual.color);
  const showLabels = scale >= 0.45 || selected;
  const showRoleBadge = scale >= 0.55 && !isCluster && role && role !== "unknown";

  const roleMeta = ROLE_META[role] || ROLE_META.unknown;

  // Blast radius ring color
  let blastRingColor = null;
  if (blastHighlight === "upstream") blastRingColor = "#ff9900";
  if (blastHighlight === "downstream") blastRingColor = "#00e7ff";
  if (blastHighlight === "center") blastRingColor = "#ffffff";

  return (
    <g
      transform={`translate(${left}, ${top})`}
      className={`graph-node${selected ? " is-selected" : ""}`}
      opacity={effectiveHighlighted ? 1 : 0.18}
      onMouseEnter={() => setTooltipVisible(true)}
      onMouseLeave={() => setTooltipVisible(false)}
    >
      {/* Selection pulse */}
      {selected && (
        <circle cx={centerX} cy={centerY} r={outerRadius + 14} fill="none" stroke={visual.color} strokeOpacity="0.25">
          <animate attributeName="r" values={`${outerRadius + 10};${outerRadius + 18};${outerRadius + 10}`} dur="2.2s" repeatCount="indefinite" />
          <animate attributeName="stroke-opacity" values="0.25;0.08;0.25" dur="2.2s" repeatCount="indefinite" />
        </circle>
      )}

      {/* Blast radius ring */}
      {blastRingColor && (
        <circle
          cx={centerX}
          cy={centerY}
          r={outerRadius + 10}
          fill="none"
          stroke={blastRingColor}
          strokeWidth="1.8"
          strokeOpacity="0.75"
          strokeDasharray={blastHighlight === "center" ? "0" : "5,3"}
        />
      )}

      <circle cx={centerX} cy={centerY} r={outerRadius + 8} fill="none" stroke={visual.color} strokeOpacity={selected || hovered ? 0.42 : 0} strokeWidth="0.7" />

      {isCluster ? (
        <>
          <circle cx={centerX} cy={centerY} r={outerRadius} fill={`${visual.color}22`} stroke={visual.color} strokeOpacity={selected ? 0.95 : hovered ? 0.8 : 0.65} strokeWidth={selected ? 2 : 1.4} strokeDasharray="5,3" />
          <circle cx={centerX} cy={centerY} r={outerRadius - 10} fill={`${visual.color}12`} stroke={visual.color} strokeWidth="0.5" strokeOpacity="0.4" />
          <text x={centerX} y={centerY - 4} textAnchor="middle" fontSize="15" fontWeight="700" fill={visual.color} className="graph-node-cluster-count">{node.count || "?"}</text>
          <text x={centerX} y={centerY + 11} textAnchor="middle" fontSize="7" fill={visual.color} opacity="0.7" letterSpacing="0.05em">{String(node.service || "").toUpperCase()}</text>
        </>
      ) : (
        <>
          <circle cx={centerX} cy={centerY} r={outerRadius} fill={`${visual.color}15`} stroke={visual.color} strokeOpacity={selected ? 0.95 : hovered ? 0.7 : 0.46} strokeWidth={selected ? 1.8 : 1} />
          <circle cx={centerX} cy={centerY} r={innerRadius} fill={`${visual.color}30`} stroke={visual.color} strokeWidth="0.6" opacity="0.86" />
          <foreignObject x={centerX - 12} y={centerY - 12} width="24" height="24">
            <div xmlns="http://www.w3.org/1999/xhtml" className="graph-node-center-icon">{icon}</div>
          </foreignObject>
        </>
      )}

      {/* Status dot — only shown when the resource exposes state */}
      {nodeState && (
        <>
          <circle cx={frame.width - 14} cy="14" r="4" fill={statusColor} />
          {selected && (
            <circle cx={frame.width - 14} cy="14" r="4" fill="none" stroke={statusColor} strokeWidth="1.4">
              <animate attributeName="r" values="4;8;4" dur="1.8s" repeatCount="indefinite" />
              <animate attributeName="stroke-opacity" values="0.85;0;0.85" dur="1.8s" repeatCount="indefinite" />
            </circle>
          )}
        </>
      )}

      {/* Labels */}
      {showLabels && !isCluster && (
        <>
          <text x={centerX} y={frame.height + 16} textAnchor="middle" fontSize="11" className="graph-node-label">
            {compactText(node.label || node.id, 24)}
          </text>
          <text x={centerX} y={frame.height + 30} textAnchor="middle" fontSize="9" className="graph-node-kind" fill={visual.color} opacity="0.6">
            {visual.label}
          </text>
        </>
      )}

      {/* Role badge */}
      {showRoleBadge && (
        <g transform={`translate(${centerX - roleMeta.width / 2}, ${frame.height - 14})`}>
          <rect x="0" y="0" width={roleMeta.width} height="11" rx="2" fill={roleMeta.color} fillOpacity="0.18" stroke={roleMeta.color} strokeWidth="0.5" strokeOpacity="0.7" />
          <text x={roleMeta.width / 2} y="8.5" textAnchor="middle" fontSize="7" fill={roleMeta.color} letterSpacing="0.06em" fontWeight="600">
            {roleMeta.label}
          </text>
        </g>
      )}

      {/* Educational tooltip */}
      {tooltipVisible && showLabels && visual.description && (
        <g transform={`translate(${centerX}, ${-28})`}>
          <rect x={-90} y={-22} width="180" height="20" rx="3" fill="#07111a" stroke="#1a2a38" strokeWidth="0.8" />
          <text x="0" y="-8" textAnchor="middle" fontSize="9" fill="#8aacbe" letterSpacing="0.02em">
            {visual.description.length > 42 ? visual.description.slice(0, 42) + "…" : visual.description}
          </text>
        </g>
      )}
    </g>
  );
}
