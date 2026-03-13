import React, { useCallback, useRef, useState } from "react";
import { AWS_REGIONS } from "../../lib/awsRegions";
import { getServiceVisual } from "../../lib/serviceVisuals.jsx";
import { useClickOutside } from "../../hooks/useClickOutside";
import { TagFilterBar } from "./TagFilterBar";

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
      { value: "vpc", label: "VPC Network" },
      { value: "cloudfront", label: "CloudFront" },
      { value: "route53", label: "Route 53" },
      { value: "elb", label: "ELB" },
      { value: "appsync", label: "AppSync" },
    ],
  },
  {
    label: "Security & Identity",
    services: [
      { value: "iam", label: "IAM" },
      { value: "cognito", label: "Cognito" },
      { value: "secretsmanager", label: "Secrets Manager" },
      { value: "kms", label: "KMS" },
    ],
  },
];

const ALL_SERVICES = AWS_SERVICE_GROUPS.flatMap((g) => g.services);

function ServiceMultiSelect({ selectedServices, onChange }) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef(null);
  const close = useCallback(() => setOpen(false), []);
  useClickOutside(containerRef, close, open);

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
                style={{ background: getServiceVisual(v).color }}
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
                  const color = getServiceVisual(svc.value).color;
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
  forceRefresh,
  onForceRefreshChange,
  warnings,
  // Tag-based filtering props
  scanFilterMode,
  onScanFilterModeChange,
  tagDiscovery,
  onScanByTags,
  tagScanLoading,
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

        {/* Scan filter mode toggle */}
        {onScanFilterModeChange && (
          <>
            <div className="topbar-divider" />
            <div className="topbar-view-toggle">
              <button
                className={`topbar-view-btn ${scanFilterMode === "services" ? "active" : ""}`}
                onClick={() => onScanFilterModeChange("services")}
                title="Select services manually to scan"
              >
                SERVICES
              </button>
              <button
                className={`topbar-view-btn ${scanFilterMode === "tags" ? "active" : ""}`}
                onClick={() => onScanFilterModeChange("tags")}
                title="Discover resources by AWS tags (e.g. Team, Environment)"
              >
                TAGS
              </button>
            </div>
          </>
        )}
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
              <span className="topbar-warn-count"> · {warnings.length} warnings</span>
            )}
          </span>
        )}

        {/* SERVICES mode controls */}
        {scanFilterMode !== "tags" && (
          <>
            <ServiceMultiSelect selectedServices={selectedServices} onChange={onServicesChange} />

            <select className="topbar-compact-select" value={scanMode} onChange={(event) => onScanModeChange(event.target.value)}>
              <option value="quick">Quick</option>
              <option value="deep">Deep</option>
            </select>
          </>
        )}

        {/* TAGS mode controls */}
        {scanFilterMode === "tags" && tagDiscovery && (
          <TagFilterBar
            tagKeys={tagDiscovery.tagKeys}
            tagKeysLoading={tagDiscovery.tagKeysLoading}
            tagKeysError={tagDiscovery.tagKeysError}
            selectedTagKeys={tagDiscovery.selectedTagKeys}
            onToggleTagKey={tagDiscovery.toggleTagKey}
            tagValues={tagDiscovery.tagValues}
            tagValuesLoading={tagDiscovery.tagValuesLoading}
            selectedTagValues={tagDiscovery.selectedTagValues}
            onToggleTagValue={tagDiscovery.toggleTagValue}
            onApplyTagFilter={tagDiscovery.addTagFilter}
            activeTagFilters={tagDiscovery.activeTagFilters}
            onRemoveTagFilter={tagDiscovery.removeTagFilter}
            onClearAllTagFilters={tagDiscovery.clearAllTagFilters}
            onRefreshTagKeys={tagDiscovery.refreshTagKeys}
          />
        )}

        <select className="topbar-compact-select topbar-region-select" value={region} onChange={(event) => onRegionChange(event.target.value)}>
          {AWS_REGIONS.map((awsRegion) => (
            <option key={awsRegion.value} value={awsRegion.value}>
              {awsRegion.value}
            </option>
          ))}
        </select>

        <label className="topbar-force-refresh-label" title="Bypass cache and re-scan all resources">
          <input
            type="checkbox"
            checked={forceRefresh}
            onChange={(e) => onForceRefreshChange(e.target.checked)}
          />
          <svg width="12" height="12" viewBox="0 0 12 12" fill="none"><path d="M10 2L10 5H7M2 10L2 7H5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round"/><path d="M2.5 4.5A4 4 0 0 1 9.5 3.5M9.5 7.5A4 4 0 0 1 2.5 8.5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/></svg>
        </label>

        {/* Action buttons */}
        {scanLoading ? (
          <button
            className="topbar-secondary-btn"
            onClick={onStopScan}
            disabled={!jobStatus || Boolean(jobStatus?.cancellation_requested)}
          >
            {jobStatus?.cancellation_requested ? "STOPPING..." : "STOP SCAN"}
          </button>
        ) : scanFilterMode === "tags" ? (
          <button
            className="topbar-primary-btn topbar-primary-btn--tags"
            onClick={onScanByTags}
            disabled={!tagDiscovery || tagDiscovery.activeTagFilters.length === 0 || tagScanLoading}
          >
            {tagScanLoading ? "DISCOVERING..." : "SCAN BY TAGS"}
          </button>
        ) : (
          <button className="topbar-primary-btn" onClick={onRunScan} disabled={selectedServices.length === 0}>
            SCAN AWS
          </button>
        )}
      </div>
    </header>
  );
}
