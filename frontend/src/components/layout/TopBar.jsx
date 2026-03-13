import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AWS_REGIONS } from "../../lib/awsRegions";
import { getServiceVisual } from "../../lib/serviceVisuals.jsx";
import { useClickOutside } from "../../hooks/useClickOutside";
import { TagFilterBar } from "./TagFilterBar";

// Hardcoded fallback — used when the API is unreachable.
const FALLBACK_SERVICE_GROUPS = [
  {
    label: "API & Integration",
    services: [
      { value: "apigateway", label: "API Gateway" },
      { value: "eventbridge", label: "EventBridge" },
      { value: "appsync", label: "AppSync" },
      { value: "mq", label: "Amazon MQ" },
    ],
  },
  {
    label: "Compute",
    services: [
      { value: "lambda", label: "Lambda" },
      { value: "ec2", label: "EC2" },
      { value: "ecs", label: "ECS" },
      { value: "eks", label: "EKS" },
      { value: "stepfunctions", label: "Step Functions" },
      { value: "glue", label: "Glue" },
      { value: "emr", label: "EMR" },
      { value: "elasticbeanstalk", label: "Elastic Beanstalk" },
      { value: "batch", label: "Batch" },
    ],
  },
  {
    label: "Queues & Streams",
    services: [
      { value: "sqs", label: "SQS" },
      { value: "sns", label: "SNS" },
      { value: "kinesis", label: "Kinesis" },
      { value: "kafka", label: "MSK" },
      { value: "firehose", label: "Kinesis Firehose" },
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
      { value: "opensearch", label: "OpenSearch" },
      { value: "efs", label: "EFS" },
      { value: "ecr", label: "ECR" },
    ],
  },
  {
    label: "Networking",
    services: [
      { value: "vpc", label: "VPC Network" },
      { value: "cloudfront", label: "CloudFront" },
      { value: "route53", label: "Route 53" },
      { value: "elb", label: "ELB" },
      { value: "acm", label: "ACM" },
    ],
  },
  {
    label: "Security & Identity",
    services: [
      { value: "iam", label: "IAM" },
      { value: "cognito", label: "Cognito" },
      { value: "secretsmanager", label: "Secrets Manager" },
      { value: "kms", label: "KMS" },
      { value: "wafv2", label: "WAF" },
      { value: "guardduty", label: "GuardDuty" },
    ],
  },
  {
    label: "Monitoring & Mgmt",
    services: [
      { value: "cloudwatch", label: "CloudWatch" },
      { value: "cloudtrail", label: "CloudTrail" },
      { value: "cloudformation", label: "CloudFormation" },
    ],
  },
  {
    label: "Analytics & ML",
    services: [
      { value: "athena", label: "Athena" },
      { value: "sagemaker", label: "SageMaker" },
    ],
  },
  {
    label: "Developer Tools",
    services: [
      { value: "codepipeline", label: "CodePipeline" },
      { value: "codebuild", label: "CodeBuild" },
    ],
  },
];

/**
 * Convert the flat list returned by GET /api/services into the grouped
 * structure the ServiceMultiSelect component expects.
 */
function groupServicesPayload(payload) {
  const groupMap = {};
  for (const svc of payload.services) {
    if (!groupMap[svc.group]) groupMap[svc.group] = { label: svc.group, services: [] };
    groupMap[svc.group].services.push({ value: svc.id, label: svc.label });
  }
  return Object.values(groupMap);
}

function useServiceGroups() {
  const [groups, setGroups] = useState(FALLBACK_SERVICE_GROUPS);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/services")
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then((data) => {
        if (!cancelled && data?.services?.length) {
          setGroups(groupServicesPayload(data));
        }
      })
      .catch(() => {
        // Keep fallback on failure — no action needed.
      });
    return () => { cancelled = true; };
  }, []);

  return groups;
}

function useAllServices(groups) {
  return useMemo(() => groups.flatMap((g) => g.services), [groups]);
}

function ServiceMultiSelect({ selectedServices, onChange, serviceGroups, allServices }) {
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

  const selectAll = useCallback(() => onChange(allServices.map((s) => s.value)), [onChange, allServices]);
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
            ? selectedServices.map((v) => allServices.find((s) => s.value === v)?.label || v).join(", ")
            : `${count} services`}
        </span>
        <span className="svc-select-caret">{open ? "▲" : "▼"}</span>
      </button>

      {open && (
        <div className="svc-select-panel">
          <div className="svc-select-actions">
            <button className="svc-select-action-btn" onClick={selectAll}>All</button>
            <button className="svc-select-action-btn" onClick={clearAll}>None</button>
            <span className="svc-select-count">{count} / {allServices.length} selected</span>
          </div>

          <div className="svc-select-list">
            {serviceGroups.map((group) => (
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
  const serviceGroups = useServiceGroups();
  const allServices = useAllServices(serviceGroups);

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
          <ServiceMultiSelect selectedServices={selectedServices} onChange={onServicesChange} serviceGroups={serviceGroups} allServices={allServices} />
        )}

        {/* Scan depth selector — visible in both modes */}
        <select className="topbar-compact-select" value={scanMode} onChange={(event) => onScanModeChange(event.target.value)}>
          <option value="quick">Quick</option>
          <option value="deep">Deep</option>
        </select>

        {/* TAGS mode controls */}
        {scanFilterMode === "tags" && tagDiscovery && (
          <TagFilterBar
            tagKeys={tagDiscovery.tagKeys}
            tagKeysLoading={tagDiscovery.tagKeysLoading}
            tagKeysError={tagDiscovery.tagKeysError}
            selectedTagKeys={tagDiscovery.selectedTagKeys}
            onToggleTagKey={tagDiscovery.toggleTagKey}
            tagValuesByKey={tagDiscovery.tagValuesByKey}
            valuesLoadingKeys={tagDiscovery.valuesLoadingKeys}
            selectedValuesByKey={tagDiscovery.selectedValuesByKey}
            onToggleTagValue={tagDiscovery.toggleTagValue}
            hasSelectedValues={tagDiscovery.hasSelectedValues}
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
          <>
            {tagDiscovery?.discoveredServices?.length > 0 && !tagScanLoading && (
              <span className="topbar-discovered-hint" title={tagDiscovery.discoveredServices.join(", ")}>
                {tagDiscovery.discoveredServices.length} services found
              </span>
            )}
            <button
              className="topbar-primary-btn topbar-primary-btn--tags"
              onClick={onScanByTags}
              disabled={!tagDiscovery || tagDiscovery.activeTagFilters.length === 0 || tagScanLoading}
            >
              {tagScanLoading ? "DISCOVERING..." : "SCAN BY TAGS"}
            </button>
          </>
        ) : tagScanLoading ? (
          <button className="topbar-primary-btn" disabled>DISCOVERING...</button>
        ) : (
          <button className="topbar-primary-btn" onClick={onRunScan} disabled={selectedServices.length === 0}>
            SCAN AWS
          </button>
        )}
      </div>
    </header>
  );
}
