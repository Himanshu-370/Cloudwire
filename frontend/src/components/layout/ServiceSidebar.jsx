import React from "react";
import { createServiceIcon, getServiceVisual } from "../../lib/serviceVisuals.jsx";

export function ServiceSidebar({
  serviceCounts,
  hiddenServices,
  onShowAllServices,
  onToggleService,
  collapsedServices,
  onToggleCluster,
  showIsolated,
  onToggleIsolated,
  isolatedCount,
  stats,
  query,
  onQueryChange,
  filteredNodes,
  selectedNodeId,
  onSelectNode,
  totalNodes,
  searchTruncated,
  layoutMode,
  onLayoutModeChange,
}) {
  const totalResources = Object.values(serviceCounts).reduce((total, value) => total + value, 0);

  return (
    <aside className="sidebar-shell">
      <section className="sidebar-block">
        <div className="sidebar-section-title">Services</div>
        <div className="sidebar-services-list">
          <button className={`sidebar-filter-pill ${hiddenServices.length === 0 ? "active" : ""}`} onClick={onShowAllServices}>
            <span className="sidebar-service-main">
              <span>All Services</span>
            </span>
            <span className="sidebar-row-count">{totalResources}</span>
          </button>
          {Object.entries(serviceCounts).map(([service, count]) => {
            const visual = getServiceVisual(service);
            const hidden = hiddenServices.includes(service);
            const collapsed = collapsedServices?.has(service);
            return (
              <div key={service} className="sidebar-pill-row">
                <button
                  className={`sidebar-filter-pill sidebar-filter-pill-inline ${hidden ? "" : "active"}`}
                  onClick={() => onToggleService(service)}
                >
                  <span className="sidebar-service-main">
                    <span className="sidebar-service-icon" style={{ color: visual.color }}>
                      {createServiceIcon(service, visual.color)}
                    </span>
                    <span>{visual.label}</span>
                  </span>
                  <span className="sidebar-row-count">{count}</span>
                </button>
                {onToggleCluster && (
                  <button
                    className={`sidebar-cluster-btn ${collapsed ? "active" : ""}`}
                    onClick={() => onToggleCluster(service)}
                    title={collapsed ? `Expand ${visual.label} cluster` : `Collapse ${visual.label} into cluster`}
                  >
                    {collapsed ? "⊞" : "⊟"}
                  </button>
                )}
              </div>
            );
          })}
        </div>
      </section>

      {isolatedCount > 0 && (
        <section className="sidebar-block">
          <button
            className={`sidebar-isolated-toggle ${showIsolated ? "active" : ""}`}
            onClick={onToggleIsolated}
          >
            <span>Disconnected</span>
            <span className="sidebar-row-count">{isolatedCount} {showIsolated ? "shown" : "hidden"}</span>
          </button>
        </section>
      )}

      <section className="sidebar-block">
        <div className="sidebar-section-title">Stats</div>
        <div className="sidebar-stats-list">
          {Object.entries(stats).map(([label, value]) => (
            <div key={label} className="sidebar-stat-row">
              <span>{label}</span>
              <strong>{value}</strong>
            </div>
          ))}
        </div>
      </section>

      <section className="sidebar-block">
        <div className="sidebar-section-title">Layout</div>
        <div className="sidebar-layout-list">
          {[
            { value: "circular", label: "Circular", icon: "⬡" },
            { value: "flow", label: "Flow", icon: "⇶" },
            { value: "swimlane", label: "Swimlane", icon: "☰" },
          ].map((opt) => (
            <button
              key={opt.value}
              className={`sidebar-control-link ${layoutMode === opt.value ? "active" : ""}`}
              onClick={() => onLayoutModeChange(opt.value)}
            >
              <span className="sidebar-layout-icon">{opt.icon}</span> {opt.label}
            </button>
          ))}
        </div>
      </section>

      <section className="sidebar-search-section sidebar-block sidebar-block-grow">
        <div className="sidebar-section-title">Resources</div>
        <input
          id="resource-search-input"
          className="sidebar-search-input"
          value={query}
          onChange={(event) => onQueryChange(event.target.value)}
          placeholder="Search resources..."
        />
        {searchTruncated > 0 && (
          <div className="sidebar-search-note">
            Showing 120 of {searchTruncated} — refine your search
          </div>
        )}
        <div className="sidebar-results-list">
          {filteredNodes.length === 0 ? (
            <div className="sidebar-empty-state">No matching resources.</div>
          ) : (
            filteredNodes.map((node) => (
              <button
                key={node.id}
                className={`sidebar-result-row ${selectedNodeId === node.id ? "active" : ""}`}
                onClick={() => onSelectNode(node.id)}
              >
                <strong>{node.label || node.id}</strong>
                <span>{node.service || "resource"}</span>
              </button>
            ))
          )}
        </div>
      </section>
    </aside>
  );
}
