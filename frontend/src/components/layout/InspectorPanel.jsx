import React from "react";
import { createServiceIcon, getServiceVisual } from "../../lib/serviceVisuals.jsx";

export function InspectorPanel({ resourceDetails, allNodes, onClose, onJumpToNode }) {
  if (!resourceDetails) return null;

  const visual = getServiceVisual(resourceDetails.node.service);
  const rawState = String(resourceDetails.node.state || resourceDetails.node.status || "").toLowerCase();
  const statusLabel = rawState.toUpperCase() || "UNKNOWN";
  const STATUS_HEALTHY = ["active", "running", "available", "deployed", "enabled", "in-service", "ready"];
  const STATUS_TRANSITIONAL = ["updating", "pending", "creating", "modifying", "backing-up", "starting", "stopping", "deleting", "inactive", "provisioning"];
  const statusColor = STATUS_HEALTHY.includes(rawState)
    ? "#00FF88"
    : STATUS_TRANSITIONAL.includes(rawState)
    ? "#FFB84D"
    : rawState
    ? "#FF4444"
    : "#5a7a8a"; // neutral for unknown

  const SHOWN_IN_HEADER = new Set(["id", "service", "type", "label", "region"]);
  const FRIENDLY_LABELS = {
    arn: "ARN", state: "State", status: "Status", runtime: "Runtime",
    memory_size: "Memory (MB)", timeout: "Timeout (s)", handler: "Handler",
    code_size: "Code Size", last_modified: "Last Modified",
    engine: "Engine", engine_version: "Engine Version", node_type: "Node Type",
    table_size_bytes: "Table Size", item_count: "Item Count",
    billing_mode: "Billing Mode", domain: "Domain", vpc_id: "VPC",
    subnet_id: "Subnet", instance_type: "Instance Type", db_name: "Database",
    num_nodes: "Node Count", private_zone: "Private Zone", record_count: "Records",
    trigger_type: "Trigger Type", event_pattern: "Event Pattern",
    schedule_expression: "Schedule", phantom: "Discovered via",
  };
  function friendlyKey(key) {
    if (FRIENDLY_LABELS[key]) return FRIENDLY_LABELS[key];
    return key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  }
  const nodeMap = new Map((allNodes || []).map((n) => [n.id, n]));
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

      <div className="inspector-accent" style={{ background: `linear-gradient(90deg, ${visual.color}, transparent)` }} />

      <section>
        <div className="sidebar-section-title">Resource Metadata</div>
        <div className="inspector-meta-list">
          {metaEntries.map(([key, value]) => (
            <div key={key} className="inspector-meta-row">
              <span>{friendlyKey(key)}</span>
              <strong>{typeof value === "object" ? JSON.stringify(value) : String(value)}</strong>
            </div>
          ))}
        </div>
      </section>

      <section>
        <div className="sidebar-section-title">Connections ({resourceDetails.outgoing.length + resourceDetails.incoming.length})</div>
        <div className="inspector-connection-list">
          {resourceDetails.outgoing.map((edge) => {
              const target = nodeMap.get(edge.target);
              const targetVisual = target ? getServiceVisual(target.service) : null;
              return (
                <button key={edge.id} className="inspector-connection-row" onClick={() => onJumpToNode(edge.target)}>
                  <span className="inspector-connection-arrow" style={{ color: visual.color }}>→</span>
                  {targetVisual && (
                    <span className="inspector-connection-icon" style={{ color: targetVisual.color }}>
                      {createServiceIcon(target.service, targetVisual.color)}
                    </span>
                  )}
                  <div>
                    <strong>{target?.label || edge.target}</strong>
                    <span>{edge.relationship || "depends_on"}</span>
                  </div>
                </button>
              );
            })}
          {resourceDetails.incoming.map((edge) => {
              const source = nodeMap.get(edge.source);
              const sourceVisual = source ? getServiceVisual(source.service) : null;
              return (
                <button key={edge.id} className="inspector-connection-row" onClick={() => onJumpToNode(edge.source)}>
                  <span className="inspector-connection-arrow" style={{ color: visual.color }}>←</span>
                  {sourceVisual && (
                    <span className="inspector-connection-icon" style={{ color: sourceVisual.color }}>
                      {createServiceIcon(source.service, sourceVisual.color)}
                    </span>
                  )}
                  <div>
                    <strong>{source?.label || edge.source}</strong>
                    <span>{edge.relationship || "depends_on"}</span>
                  </div>
                </button>
              );
            })}
          {resourceDetails.outgoing.length === 0 && resourceDetails.incoming.length === 0 && (
            <div className="inspector-empty">No relationships.</div>
          )}
        </div>
      </section>
    </aside>
  );
}
