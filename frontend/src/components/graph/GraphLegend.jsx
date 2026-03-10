import React from "react";
import { createServiceIcon, SERVICE_VISUALS } from "../../lib/serviceVisuals.jsx";

export function GraphLegend() {
  return (
    <div className="graph-legend">
      {Object.entries(SERVICE_VISUALS)
        .filter(([service]) => service !== "unknown")
        .map(([service, visual]) => (
          <div key={service} className="graph-legend-item">
            <span className="graph-legend-icon" style={{ color: visual.color }}>
              {createServiceIcon(service, visual.color)}
            </span>
            <span>{visual.label}</span>
          </div>
        ))}
    </div>
  );
}
