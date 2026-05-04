import React, { useState } from "react";
import { createServiceIcon, getServiceVisual } from "../../lib/serviceVisuals.jsx";

const STATUS_HEALTHY = new Set(["active", "running", "available", "deployed", "enabled", "in-service", "ready"]);
const STATUS_TRANSITIONAL = new Set(["updating", "pending", "creating", "modifying", "backing-up", "starting", "stopping", "deleting", "inactive", "provisioning"]);

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

function formatValue(value) {
  if (typeof value === "object" && value !== null) {
    const str = JSON.stringify(value);
    return str.length > 80 ? str.slice(0, 78) + "…" : str;
  }
  const str = String(value);
  return str.length > 120 ? str.slice(0, 118) + "…" : str;
}

function ConnectionGroup({ label, edges, nodeMap, currentVisual, onJumpToNode }) {
  if (edges.length === 0) return null;
  const isOutgoing = label === "Out";
  return (
    <div className="inspector-conn-group">
      <div className="inspector-conn-group-label">{label} ({edges.length})</div>
      {edges.map((edge) => {
        const peerId = isOutgoing ? edge.target : edge.source;
        const peer = nodeMap.get(peerId);
        const peerVisual = peer ? getServiceVisual(peer.service) : null;
        const rel = edge.relationship || "depends_on";
        return (
          <button
            key={edge.id}
            className="inspector-connection-row"
            onClick={() => onJumpToNode(peerId)}
            title={rel}
          >
            <span className="inspector-connection-arrow" style={{ color: isOutgoing ? currentVisual.color : "#5a7a8a" }}>
              {isOutgoing ? "→" : "←"}
            </span>
            {peerVisual && (
              <span className="inspector-connection-icon" style={{ color: peerVisual.color }}>
                {createServiceIcon(peer.service, peerVisual.color)}
              </span>
            )}
            <span className="inspector-conn-peer-name">{peer?.label || peerId}</span>
          </button>
        );
      })}
    </div>
  );
}

export function InspectorPanel({ resourceDetails, allNodes, onClose, onJumpToNode }) {
  const [metaExpanded, setMetaExpanded] = useState(false);

  if (!resourceDetails) return null;

  const { node, outgoing, incoming } = resourceDetails;
  const visual = getServiceVisual(node.service);
  const rawState = String(node.state || node.status || "").toLowerCase();
  const statusLabel = rawState || "unknown";
  const statusColor = STATUS_HEALTHY.has(rawState)
    ? "#00CC6A"
    : STATUS_TRANSITIONAL.has(rawState)
    ? "#FF9900"
    : rawState
    ? "#FF4444"
    : "#3a5a6a";

  const nodeMap = new Map((allNodes || []).map((n) => [n.id, n]));
  const metaEntries = Object.entries(node).filter(
    ([key, value]) => !SHOWN_IN_HEADER.has(key) && value !== null && value !== undefined
  );

  const PREVIEW_COUNT = 5;
  const visibleMeta = metaExpanded ? metaEntries : metaEntries.slice(0, PREVIEW_COUNT);
  const hiddenCount = metaEntries.length - PREVIEW_COUNT;

  const totalConnections = outgoing.length + incoming.length;

  return (
    <aside className="inspector-shell">
      {/* ── Header ── */}
      <div className="inspector-header">
        <div className="inspector-header-identity">
          <span className="inspector-header-icon" style={{ color: visual.color }}>
            {createServiceIcon(node.service, visual.color)}
          </span>
          <div className="inspector-header-text">
            <div className="inspector-kicker">{node.type || visual.label}</div>
            <h2 className="inspector-title">{node.label || node.id}</h2>
          </div>
        </div>
        <button className="inspector-close-btn" onClick={onClose} aria-label="Close panel">✕</button>
      </div>

      {/* ── Status + Region row ── */}
      <div className="inspector-meta-strip">
        <span className="inspector-status-pill" style={{ borderColor: `${statusColor}55`, color: statusColor }}>
          <span className="inspector-status-dot" style={{ background: statusColor }} />
          {statusLabel}
        </span>
        <span className="inspector-meta-strip-region">{node.region || "global"}</span>
      </div>

      {/* ── Resource ID ── */}
      <div className="inspector-resource-id" title={node.id}>{node.id}</div>

      {/* ── Color accent ── */}
      <div className="inspector-accent" style={{ background: `linear-gradient(90deg, ${visual.color}66, transparent)` }} />

      {/* ── Cost ── */}
      {node.cost_usd != null && (
        <section className="inspector-section inspector-section-cost">
          <div className="inspector-section-title" style={{ color: "#f0a500" }}>Cost</div>
          <div className="inspector-meta-list">
            <div className="inspector-meta-row">
              <span className="inspector-meta-key">Month-to-date</span>
              <span className="inspector-meta-val" style={{ color: "#f0a500", fontWeight: 600 }}>
                {new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(node.cost_usd)}
              </span>
            </div>
            {node.cost_period && (
              <div className="inspector-meta-row">
                <span className="inspector-meta-key">Period</span>
                <span className="inspector-meta-val">{node.cost_period}</span>
              </div>
            )}
          </div>
        </section>
      )}

      {/* ── Metadata ── */}
      {metaEntries.length > 0 && (
        <section className="inspector-section">
          <div className="inspector-section-title">Details</div>
          <div className="inspector-meta-list">
            {visibleMeta.map(([key, value]) => (
              <div key={key} className="inspector-meta-row">
                <span className="inspector-meta-key">{friendlyKey(key)}</span>
                <span className="inspector-meta-val">{formatValue(value)}</span>
              </div>
            ))}
          </div>
          {metaEntries.length > PREVIEW_COUNT && (
            <button className="inspector-expand-btn" onClick={() => setMetaExpanded((v) => !v)}>
              {metaExpanded ? "Show less" : `+${hiddenCount} more`}
            </button>
          )}
        </section>
      )}

      {/* ── Connections ── */}
      <section className="inspector-section inspector-section-connections">
        <div className="inspector-section-title">
          Connections
          {totalConnections > 0 && <span className="inspector-section-count">{totalConnections}</span>}
        </div>
        {totalConnections === 0 ? (
          <div className="inspector-empty">No connections.</div>
        ) : (
          <div className="inspector-connection-list">
            <ConnectionGroup label="Out" edges={outgoing} nodeMap={nodeMap} currentVisual={visual} onJumpToNode={onJumpToNode} />
            <ConnectionGroup label="In" edges={incoming} nodeMap={nodeMap} currentVisual={visual} onJumpToNode={onJumpToNode} />
          </div>
        )}
      </section>
    </aside>
  );
}
