import React, { useContext, useMemo, useState } from "react";
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

// Build resource-specific tooltip rows from the node's actual attributes.
function getTooltipRows(node) {
  const rows = [];
  const svc = String(node.service || "").toLowerCase();

  if (node.region) rows.push({ key: "region", val: node.region });

  const state = node.state || node.status;
  if (state) rows.push({ key: "state", val: state });

  if (svc === "lambda") {
    if (node.runtime)     rows.push({ key: "runtime",  val: node.runtime });
    if (node.memory_size) rows.push({ key: "memory",   val: `${node.memory_size} MB` });
    if (node.timeout)     rows.push({ key: "timeout",  val: `${node.timeout}s` });
    if (node.handler)     rows.push({ key: "handler",  val: node.handler });
  } else if (svc === "ec2") {
    if (node.instance_type) rows.push({ key: "type",   val: node.instance_type });
    if (node.vpc_id)        rows.push({ key: "vpc",    val: node.vpc_id });
  } else if (svc === "rds") {
    if (node.engine)         rows.push({ key: "engine",   val: node.engine });
    if (node.instance_class) rows.push({ key: "class",    val: node.instance_class });
    if (node.multi_az != null) rows.push({ key: "multi-az", val: node.multi_az ? "yes" : "no" });
  } else if (svc === "dynamodb") {
    if (node.billing_mode)    rows.push({ key: "billing", val: node.billing_mode });
    if (node.item_count != null) rows.push({ key: "items", val: String(node.item_count) });
  } else if (svc === "sqs") {
    if (node.visibility_timeout) rows.push({ key: "visibility", val: `${node.visibility_timeout}s` });
  } else if (svc === "eventbridge") {
    if (node.schedule_expression) rows.push({ key: "schedule", val: node.schedule_expression });
    if (node.event_pattern)       rows.push({ key: "pattern",  val: "custom" });
  } else if (svc === "elasticache") {
    const ev = [node.engine, node.engine_version].filter(Boolean).join(" ");
    if (ev)           rows.push({ key: "engine",    val: ev });
    if (node.node_type) rows.push({ key: "node type", val: node.node_type });
  } else if (svc === "cloudfront") {
    if (node.domain) rows.push({ key: "domain", val: node.domain });
  } else if (svc === "stepfunctions") {
    if (node.sm_type)       rows.push({ key: "type",    val: node.sm_type });
    if (node.creation_date) rows.push({ key: "created", val: String(node.creation_date).slice(0, 10) });
  } else if (svc === "iam") {
    if (node.created) rows.push({ key: "created", val: String(node.created).slice(0, 10) });
  } else if (svc === "s3") {
    if (node.creation_date) rows.push({ key: "created", val: String(node.creation_date).slice(0, 10) });
  } else if (svc === "apigateway") {
    if (node.protocol) rows.push({ key: "protocol", val: node.protocol });
  } else if (svc === "kinesis") {
    if (node.shard_count != null) rows.push({ key: "shards", val: String(node.shard_count) });
  } else if (svc === "redshift") {
    if (node.node_type)  rows.push({ key: "node type", val: node.node_type });
    if (node.num_nodes != null) rows.push({ key: "nodes", val: String(node.num_nodes) });
    if (node.db_name)    rows.push({ key: "database", val: node.db_name });
    if (node.vpc_id)     rows.push({ key: "vpc", val: node.vpc_id });
  } else if (svc === "route53") {
    if (node.private_zone != null) rows.push({ key: "type", val: node.private_zone ? "private" : "public" });
    if (node.record_count != null) rows.push({ key: "records", val: String(node.record_count) });
  } else if (svc === "glue") {
    if (node.type) rows.push({ key: "type", val: node.type });
  } else if (svc === "appsync") {
    if (node.auth_type) rows.push({ key: "auth", val: node.auth_type });
  } else if (svc === "ecs") {
    if (node.launch_type) rows.push({ key: "launch", val: node.launch_type });
    if (node.task_count != null) rows.push({ key: "tasks", val: String(node.task_count) });
  } else if (svc === "sns") {
    if (node.subscription_count != null) rows.push({ key: "subs", val: String(node.subscription_count) });
  } else if (svc === "cognito") {
    if (node.user_count != null) rows.push({ key: "users", val: String(node.user_count) });
  } else if (svc === "elb") {
    if (node.scheme) rows.push({ key: "scheme", val: node.scheme });
    if (node.lb_type) rows.push({ key: "type", val: node.lb_type });
    if (node.vpc_id) rows.push({ key: "vpc", val: node.vpc_id });
  } else if (svc === "secretsmanager") {
    if (node.rotation_enabled != null) rows.push({ key: "rotation", val: node.rotation_enabled ? "enabled" : "disabled" });
  } else if (svc === "kms") {
    if (node.key_state) rows.push({ key: "state", val: node.key_state });
    if (node.key_usage) rows.push({ key: "usage", val: node.key_usage });
  }

  // ARN — always last, truncated
  const arn = node.arn;
  if (arn && typeof arn === "string" && arn.startsWith("arn:")) {
    rows.push({ key: "arn", val: arn.split(":").slice(-1)[0] || arn });
  }

  return rows;
}

const TOOLTIP_W = 224;
const ROW_H = 15;
const HEADER_H = 22;
const V_PAD = 8;

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

  const effectiveHighlighted = blastHighlight ? true : highlighted;

  const tooltipRows = useMemo(() => getTooltipRows(node), [node]);
  const tooltipH = HEADER_H + V_PAD + tooltipRows.length * ROW_H + V_PAD;

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

  let blastRingColor = null;
  if (blastHighlight === "upstream")   blastRingColor = "#ff9900";
  if (blastHighlight === "downstream") blastRingColor = "#00e7ff";
  if (blastHighlight === "center")     blastRingColor = "#ffffff";

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

      {/* Resource-specific tooltip — only shown on hover for non-cluster nodes */}
      {tooltipVisible && showLabels && !isCluster && (
        <g transform={`translate(${centerX - TOOLTIP_W / 2}, ${-tooltipH - 10})`}>
          {/* Drop shadow */}
          <rect x="2" y="2" width={TOOLTIP_W} height={tooltipH} rx="5" fill="#000000" opacity="0.35" />
          {/* Background */}
          <rect x="0" y="0" width={TOOLTIP_W} height={tooltipH} rx="5" fill="#060e16" stroke={`${visual.color}50`} strokeWidth="0.8" />
          {/* Header band */}
          <rect x="0" y="0" width={TOOLTIP_W} height={HEADER_H} rx="5" fill={`${visual.color}20`} />
          <rect x="0" y={HEADER_H - 4} width={TOOLTIP_W} height="4" fill={`${visual.color}20`} />
          {/* Service type pill */}
          <rect x="8" y="5" width={visual.label.length * 5.5 + 8} height="12" rx="2" fill={`${visual.color}30`} />
          <text x="12" y="14.5" fontSize="8" fontWeight="700" fill={visual.color} letterSpacing="0.08em">
            {visual.label.toUpperCase()}
          </text>
          {/* Resource name */}
          <text
            x={TOOLTIP_W - 10}
            y="14.5"
            textAnchor="end"
            fontSize="9"
            fontWeight="600"
            fill="#ddeeff"
          >
            {compactText(node.label || node.id, 22)}
          </text>
          {/* Divider */}
          <line x1="8" y1={HEADER_H + 0.5} x2={TOOLTIP_W - 8} y2={HEADER_H + 0.5} stroke={`${visual.color}25`} strokeWidth="0.6" />
          {/* Attribute rows */}
          {tooltipRows.map(({ key, val }, i) => (
            <g key={key} transform={`translate(0, ${HEADER_H + V_PAD + i * ROW_H})`}>
              <text x="12" y="9" fontSize="9" fill="#4a7080" letterSpacing="0.03em">{key}</text>
              <text x={TOOLTIP_W - 12} y="9" textAnchor="end" fontSize="9" fill="#a8c8d8">
                {compactText(String(val), 22)}
              </text>
            </g>
          ))}
          {/* Empty state — no extra attributes available */}
          {tooltipRows.length === 0 && (
            <text x={TOOLTIP_W / 2} y={HEADER_H + V_PAD + 9} textAnchor="middle" fontSize="9" fill="#304050">
              no attributes available
            </text>
          )}
        </g>
      )}
    </g>
  );
}
