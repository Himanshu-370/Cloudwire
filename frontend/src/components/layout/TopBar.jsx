import React, { useCallback, useEffect, useRef, useState } from "react";
import { AWS_REGIONS } from "../../lib/awsRegions";

// Service colours — kept in sync with serviceVisuals.jsx palette
const SERVICE_COLORS = {
  apigateway:    "#ff9900",
  eventbridge:   "#e05252",
  lambda:        "#ff9900",
  ec2:           "#e8855a",
  ecs:           "#e8855a",
  stepfunctions: "#00b4e0",
  glue:          "#00b4e0",
  sqs:           "#ff4f8b",
  sns:           "#ff4f8b",
  kinesis:       "#c766d4",
  dynamodb:      "#7b2d8b",
  s3:            "#3d9970",
  rds:           "#3d9970",
  elasticache:   "#3d9970",
  redshift:      "#7b2d8b",
  cloudfront:    "#00e7ff",
  route53:       "#00e7ff",
  appsync:       "#00b4e0",
  iam:           "#cc6633",
  cognito:       "#cc6633",
};

const AWS_SERVICE_GROUPS = [
  {
    label: "API & Integration",
    services: [
      { value: "apigateway", label: "API Gateway" },
      { value: "eventbridge", label: "EventBridge" },
    ],
  },
  {
    label: "Compute",
    services: [
      { value: "lambda", label: "Lambda" },
      { value: "ec2", label: "EC2" },
      { value: "ecs", label: "ECS" },
      { value: "stepfunctions", label: "Step Functions" },
      { value: "glue", label: "Glue" },
    ],
  },
  {
    label: "Queues & Streams",
    services: [
      { value: "sqs", label: "SQS" },
      { value: "sns", label: "SNS" },
      { value: "kinesis", label: "Kinesis" },
    ],
  },
  {
    label: "Database & Storage",
    services: [
      { value: "dynamodb", label: "DynamoDB" },
      { value: "s3", label: "S3" },
      { value: "rds", label: "RDS" },
      { value: "elasticache", label: "ElastiCache" },
      { value: "redshift", label: "Redshift" },
    ],
  },
  {
    label: "Networking",
    services: [
      { value: "cloudfront", label: "CloudFront" },
      { value: "route53", label: "Route 53" },
      { value: "appsync", label: "AppSync" },
    ],
  },
  {
    label: "Security & Identity",
    services: [
      { value: "iam", label: "IAM" },
      { value: "cognito", label: "Cognito" },
    ],
  },
];

const ALL_SERVICES = AWS_SERVICE_GROUPS.flatMap((g) => g.services);

function ServiceMultiSelect({ selectedServices, onChange }) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    function handleClick(e) {
      if (!containerRef.current?.contains(e.target)) setOpen(false);
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [open]);

  const toggle = useCallback((value) => {
    onChange(
      selectedServices.includes(value)
        ? selectedServices.filter((s) => s !== value)
        : [...selectedServices, value]
    );
  }, [selectedServices, onChange]);

  const selectAll = useCallback(() => onChange(ALL_SERVICES.map((s) => s.value)), [onChange]);
  const clearAll = useCallback(() => onChange([]), [onChange]);

  const count = selectedServices.length;
  const triggerDots = selectedServices.slice(0, 5);

  return (
    <div ref={containerRef} className="svc-select-wrap">
      <button
        className={`svc-select-trigger ${open ? "open" : ""}`}
        onClick={() => setOpen((v) => !v)}
        title="Select AWS services to scan"
      >
        {count > 0 && (
          <span className="svc-select-trigger-dots">
            {triggerDots.map((v) => (
              <span
                key={v}
                className="svc-select-dot"
                style={{ background: SERVICE_COLORS[v] || "#4a7a90" }}
              />
            ))}
          </span>
        )}
        <span className="svc-select-label">
          {count === 0
            ? "Select services…"
            : count <= 2
            ? selectedServices.map((v) => ALL_SERVICES.find((s) => s.value === v)?.label || v).join(", ")
            : `${count} services`}
        </span>
        <span className="svc-select-caret">{open ? "▲" : "▼"}</span>
      </button>

      {open && (
        <div className="svc-select-panel">
          <div className="svc-select-actions">
            <button className="svc-select-action-btn" onClick={selectAll}>All</button>
            <button className="svc-select-action-btn" onClick={clearAll}>None</button>
            <span className="svc-select-count">{count} / {ALL_SERVICES.length} selected</span>
          </div>

          <div className="svc-select-list">
            {AWS_SERVICE_GROUPS.map((group) => (
              <div key={group.label} className="svc-select-group">
                <div className="svc-select-group-label">{group.label}</div>
                {group.services.map((svc) => {
                  const checked = selectedServices.includes(svc.value);
                  const color = SERVICE_COLORS[svc.value] || "#4a7a90";
                  return (
                    <label key={svc.value} className={`svc-select-item ${checked ? "checked" : ""}`}>
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => toggle(svc.value)}
                        className="svc-select-checkbox"
                      />
                      <span className="svc-select-item-dot" style={{ background: color }} />
                      <span className="svc-select-item-check">
                        {checked && (
                          <svg className="svc-select-item-checkmark" viewBox="0 0 8 8" fill="none">
                            <path d="M1 4l2 2 4-4" stroke="#ff9900" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round"/>
                          </svg>
                        )}
                      </span>
                      <span>{svc.label}</span>
                    </label>
                  );
                })}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

const LAYOUT_OPTIONS = [
  { value: "circular",  label: "Circular",  icon: "⬡" },
  { value: "flow",      label: "Flow",      icon: "⇶" },
  { value: "swimlane",  label: "Swimlane",  icon: "☰" },
];

function LayoutDropdown({ layoutMode, onLayoutModeChange }) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    function handleClick(e) {
      if (!containerRef.current?.contains(e.target)) setOpen(false);
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [open]);

  const current = LAYOUT_OPTIONS.find((o) => o.value === layoutMode) || LAYOUT_OPTIONS[0];

  return (
    <div ref={containerRef} className="layout-select-wrap">
      <button
        className={`layout-select-trigger ${open ? "open" : ""}`}
        onClick={() => setOpen((v) => !v)}
        title="Choose graph layout"
      >
        <span className="layout-select-trigger-icon">{current.icon}</span>
        <span>{current.label}</span>
        <span className="layout-select-caret">{open ? "▲" : "▼"}</span>
      </button>

      {open && (
        <div className="layout-select-panel">
          {LAYOUT_OPTIONS.map((opt) => (
            <div
              key={opt.value}
              className={`layout-select-item ${layoutMode === opt.value ? "active" : ""}`}
              onClick={() => { onLayoutModeChange(opt.value); setOpen(false); }}
            >
              <span className="layout-select-item-icon">{opt.icon}</span>
              <span>{opt.label}</span>
              {layoutMode === opt.value && <span className="layout-select-item-check">✓</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function TopBar({
  region,
  onRegionChange,
  selectedServices,
  onServicesChange,
  scanMode,
  onScanModeChange,
  onRunScan,
  onStopScan,
  scanLoading,
  jobStatus,
  statusLabel,
  layoutMode,
  onLayoutModeChange,
  forceRefresh,
  onForceRefreshChange,
  warnings,
}) {
  return (
    <header className="topbar-shell">
      <div className="topbar-left">
        <div className="topbar-mark">
          <span className="topbar-mark-dot" aria-hidden="true">
            <svg width="22" height="22" viewBox="0 0 22 22" fill="none">
              <polygon points="11,2 20,7 20,15 11,20 2,15 2,7" stroke="#FF9900" strokeWidth="1.5" />
              <polygon points="11,6 16,9 16,13 11,16 6,13 6,9" fill="#FF9900" fillOpacity="0.15" />
              <circle cx="11" cy="11" r="2" fill="#FF9900" />
            </svg>
          </span>
          <span className="topbar-brand">CloudWire</span>
        </div>
        <div className="topbar-divider" />
        <span className="topbar-kicker">AWS RESOURCE VISUALIZER</span>
      </div>

      <div className="topbar-right">
        {scanLoading && (
          <div className="topbar-scan-inline">
            <div className="topbar-inline-progress-track">
              <div className="topbar-inline-progress-fill" style={{ width: `${jobStatus?.progress_percent ?? 0}%` }} />
            </div>
            <span>{statusLabel} {jobStatus?.progress_percent ?? 0}%</span>
          </div>
        )}

        {!scanLoading && jobStatus?.status === "completed" && (
          <span className="topbar-done">
            SCAN COMPLETE {jobStatus?.node_count ? `· ${jobStatus.node_count} RESOURCES` : ""}
            {warnings?.length > 0 && (
              <span className="topbar-warn-count"> · ⚠ {warnings.length} warnings</span>
            )}
          </span>
        )}

        <ServiceMultiSelect selectedServices={selectedServices} onChange={onServicesChange} />

        <select className="topbar-compact-select" value={scanMode} onChange={(event) => onScanModeChange(event.target.value)}>
          <option value="quick">Quick</option>
          <option value="deep">Deep</option>
        </select>

        <LayoutDropdown layoutMode={layoutMode} onLayoutModeChange={onLayoutModeChange} />

        <select className="topbar-compact-select topbar-region-select" value={region} onChange={(event) => onRegionChange(event.target.value)}>
          {AWS_REGIONS.map((awsRegion) => (
            <option key={awsRegion.value} value={awsRegion.value}>
              {awsRegion.value}
            </option>
          ))}
        </select>

        <label className="topbar-force-refresh-label">
          <input
            type="checkbox"
            checked={forceRefresh}
            onChange={(e) => onForceRefreshChange(e.target.checked)}
          />
          Force refresh
        </label>

        <button className="topbar-primary-btn" onClick={onRunScan} disabled={scanLoading || selectedServices.length === 0}>
          {scanLoading ? "SCANNING..." : "SCAN AWS"}
        </button>

        <button
          className="topbar-secondary-btn"
          onClick={onStopScan}
          disabled={!scanLoading || !jobStatus || Boolean(jobStatus?.cancellation_requested)}
        >
          STOP
        </button>
      </div>
    </header>
  );
}
