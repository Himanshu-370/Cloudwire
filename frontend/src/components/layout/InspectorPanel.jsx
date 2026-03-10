import React from "react";
import { createServiceIcon, getServiceVisual } from "../../lib/serviceVisuals.jsx";

export function InspectorPanel({ resourceDetails, onClose, onJumpToNode }) {
  if (!resourceDetails) return null;

  const visual = getServiceVisual(resourceDetails.node.service);
  const rawState = String(resourceDetails.node.state || resourceDetails.node.status || "").toLowerCase();
  const statusLabel = rawState.toUpperCase() || "UNKNOWN";
  const statusColor = ["active", "running", "available", "deployed", "enabled"].includes(rawState)
    ? "#00FF88"
    : rawState
    ? "#FF4444"
    : "#5a7a8a"; // neutral for unknown

  const SHOWN_IN_HEADER = new Set(["id", "service", "type", "label", "region"]);
  const metaEntries = Object.entries(resourceDetails.node).filter(
    ([key, value]) => !SHOWN_IN_HEADER.has(key) && value !== null && value !== undefined
  );

  return (
    <aside className="inspector-shell">
      <div className="inspector-header">
        <div>
          <div className="inspector-kicker">{resourceDetails.node.type || visual.label}</div>
          <h2>{resourceDetails.node.label || resourceDetails.node.id}</h2>
        </div>
        <button onClick={onClose}>CLOSE PANEL</button>
      </div>

      <div className="inspector-status-row">
        <div className="inspector-status-pill" style={{ borderColor: `${statusColor}44` }}>
          <span className="inspector-status-dot" style={{ background: statusColor }} />
          <span style={{ color: statusColor }}>{statusLabel}</span>
        </div>
      </div>

      <div className="inspector-identity">
        <span className="inspector-icon" style={{ color: visual.color, borderColor: `${visual.color}55` }}>
          {createServiceIcon(resourceDetails.node.service, visual.color)}
        </span>
        <div>
          <div>{resourceDetails.node.region || "global"}</div>
          <div className="inspector-id">{resourceDetails.node.id}</div>
        </div>
      </div>

      <div className="inspector-chip-row">
        <span className="inspector-chip">{resourceDetails.node.region || "global"}</span>
        <span className="inspector-chip">{visual.label}</span>
        <span className="inspector-chip">{resourceDetails.node.id}</span>
      </div>

      <div className="inspector-accent" style={{ background: `linear-gradient(90deg, ${visual.color}, transparent)` }} />

      <section>
        <div className="sidebar-section-title">Resource Metadata</div>
        <div className="inspector-meta-list">
          {metaEntries.map(([key, value]) => (
            <div key={key} className="inspector-meta-row">
              <span>{key}</span>
              <strong>{typeof value === "object" ? JSON.stringify(value) : String(value)}</strong>
            </div>
          ))}
        </div>
      </section>

      <section>
        <div className="sidebar-section-title">Connections ({resourceDetails.outgoing.length + resourceDetails.incoming.length})</div>
        <div className="inspector-connection-list">
          {resourceDetails.outgoing.map((edge) => (
              <button key={edge.id} className="inspector-connection-row" onClick={() => onJumpToNode(edge.target)}>
                <span className="inspector-connection-arrow" style={{ color: visual.color }}>→</span>
                <div>
                  <strong>{edge.target}</strong>
                  <span>{edge.relationship || "depends_on"}</span>
                </div>
              </button>
            ))}
          {resourceDetails.incoming.map((edge) => (
              <button key={edge.id} className="inspector-connection-row" onClick={() => onJumpToNode(edge.source)}>
                <span className="inspector-connection-arrow" style={{ color: visual.color }}>←</span>
                <div>
                  <strong>{edge.source}</strong>
                  <span>{edge.relationship || "depends_on"}</span>
                </div>
              </button>
            ))}
          {resourceDetails.outgoing.length === 0 && resourceDetails.incoming.length === 0 && (
            <div className="inspector-empty">No relationships.</div>
          )}
        </div>
      </section>
    </aside>
  );
}
