import React from "react";

const ICON_SIZE = 18;

function wrapIcon(children, color) {
  return (
    <svg width={ICON_SIZE} height={ICON_SIZE} viewBox="0 0 18 18" fill="none" aria-hidden="true">
      <g stroke={color} strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
        {children}
      </g>
    </svg>
  );
}

export function normalizeServiceName(service) {
  const key = String(service || "unknown").toLowerCase().trim();
  const aliases = {
    "api-gateway": "apigateway",
    apigw: "apigateway",
    events: "eventbridge",
    "event-bridge": "eventbridge",
    "cognito-idp": "cognito",
    "elasticloadbalancing": "elb",
  };
  return aliases[key] || key;
}

export function createServiceIcon(service, color) {
  switch (normalizeServiceName(service)) {
    case "apigateway":
      return wrapIcon(
        <>
          <path d="M4 4h10v10H4z" />
          <path d="M4 9h10" />
          <path d="M9 4v10" />
        </>,
        color
      );
    case "lambda":
      return wrapIcon(
        <>
          <path d="M5 3l4 6-3 6" />
          <path d="M10 3l3 12" />
        </>,
        color
      );
    case "sqs":
      return wrapIcon(
        <>
          <rect x="3.5" y="4" width="11" height="10" rx="1.8" />
          <path d="M6 7.2h6" />
          <path d="M6 10h6" />
        </>,
        color
      );
    case "eventbridge":
      return wrapIcon(
        <>
          <circle cx="9" cy="9" r="5.5" />
          <path d="M9 3v3" />
          <path d="M14 9h-3" />
          <path d="M9 15v-3" />
          <path d="M4 9h3" />
        </>,
        color
      );
    case "dynamodb":
      return wrapIcon(
        <>
          <ellipse cx="9" cy="5" rx="5" ry="2.2" />
          <path d="M4 5v6c0 1.2 2.2 2.2 5 2.2s5-1 5-2.2V5" />
          <path d="M4 8c0 1.2 2.2 2.2 5 2.2s5-1 5-2.2" />
        </>,
        color
      );
    case "s3":
      return wrapIcon(
        <>
          <path d="M6 4h6l2.5 4L9 14 3.5 8 6 4z" />
        </>,
        color
      );
    case "ec2":
      return wrapIcon(
        <>
          <path d="M9 2.8l5.4 3.1v6.2L9 15.2l-5.4-3.1V5.9L9 2.8z" />
          <path d="M9 2.8v12.4" />
        </>,
        color
      );
    case "rds":
      return wrapIcon(
        <>
          <ellipse cx="9" cy="4.8" rx="4.6" ry="2" />
          <path d="M4.4 4.8v7.5c0 1.1 2.1 2 4.6 2s4.6-.9 4.6-2V4.8" />
        </>,
        color
      );
    case "iam":
      return wrapIcon(
        <>
          <circle cx="7" cy="7" r="2.4" />
          <path d="M4.5 13c.8-1.6 2-2.4 3.5-2.4s2.7.8 3.5 2.4" />
          <path d="M12.5 6.5l1.7 1.7" />
          <path d="M14.2 6.5l-1.7 1.7" />
        </>,
        color
      );
    case "sns":
      return wrapIcon(
        <>
          <path d="M9 3v3" />
          <path d="M5 9l-2 2" />
          <path d="M13 9l2 2" />
          <circle cx="9" cy="9" r="4" />
          <circle cx="9" cy="9" r="1.5" fill={color} />
        </>,
        color
      );
    case "ecs":
      return wrapIcon(
        <>
          <rect x="3" y="3" width="12" height="12" rx="2" />
          <rect x="5.5" y="5.5" width="3" height="3" rx="0.5" />
          <rect x="9.5" y="5.5" width="3" height="3" rx="0.5" />
          <rect x="5.5" y="9.5" width="3" height="3" rx="0.5" />
        </>,
        color
      );
    case "stepfunctions":
      return wrapIcon(
        <>
          <circle cx="5" cy="5" r="2" />
          <circle cx="13" cy="9" r="2" />
          <circle cx="5" cy="13" r="2" />
          <path d="M7 5h4l2 4" />
          <path d="M11 9l-4 4" />
        </>,
        color
      );
    case "kinesis":
      return wrapIcon(
        <>
          <path d="M4 5h10" />
          <path d="M4 9h10" />
          <path d="M4 13h10" />
          <path d="M11 3l3 2-3 2" />
          <path d="M11 7l3 2-3 2" />
        </>,
        color
      );
    case "cognito":
      return wrapIcon(
        <>
          <circle cx="9" cy="6" r="3" />
          <path d="M4 15c0-2.8 2.2-5 5-5s5 2.2 5 5" />
        </>,
        color
      );
    case "cloudfront":
      return wrapIcon(
        <>
          <circle cx="9" cy="9" r="6" />
          <ellipse cx="9" cy="9" rx="3" ry="6" />
          <path d="M3 9h12" />
        </>,
        color
      );
    case "elasticache":
      return wrapIcon(
        <>
          <path d="M3 6h12v7H3z" />
          <path d="M5 6V4h8v2" />
          <path d="M6 9h6" />
        </>,
        color
      );
    case "glue":
      return wrapIcon(
        <>
          <circle cx="5" cy="5" r="2.5" />
          <circle cx="13" cy="13" r="2.5" />
          <path d="M7 7l4 4" />
        </>,
        color
      );
    case "appsync":
      return wrapIcon(
        <>
          <circle cx="9" cy="9" r="5.5" />
          <path d="M6 7l3 2-3 2" />
          <path d="M10 7h2" />
          <path d="M10 11h2" />
        </>,
        color
      );
    case "redshift":
      return wrapIcon(
        <>
          <ellipse cx="9" cy="5" rx="5" ry="2" />
          <path d="M4 5v8c0 1.1 2.2 2 5 2s5-.9 5-2V5" />
          <path d="M4 9c0 1.1 2.2 2 5 2s5-.9 5-2" />
        </>,
        color
      );
    case "route53":
      return wrapIcon(
        <>
          <circle cx="9" cy="9" r="6" />
          <path d="M9 3v12" />
          <path d="M3 9h12" />
          <path d="M4.5 5l9 8" />
        </>,
        color
      );
    case "elb":
      return wrapIcon(
        <>
          <path d="M3 9h12" />
          <path d="M9 4v10" />
          <circle cx="4" cy="5" r="1.5" />
          <circle cx="14" cy="5" r="1.5" />
          <circle cx="4" cy="13" r="1.5" />
          <circle cx="14" cy="13" r="1.5" />
        </>,
        color
      );
    case "secretsmanager":
      return wrapIcon(
        <>
          <rect x="5" y="8" width="8" height="7" rx="1" />
          <path d="M7 8V6a2 2 0 014 0v2" />
          <circle cx="9" cy="11.5" r="1" fill={color} />
        </>,
        color
      );
    case "kms":
      return wrapIcon(
        <>
          <circle cx="7" cy="8" r="3.5" />
          <path d="M10 8h5" />
          <path d="M12 8v2" />
          <path d="M14 8v2" />
        </>,
        color
      );
    default:
      return wrapIcon(
        <>
          <path d="M9 2.8l5.4 3.1v6.2L9 15.2l-5.4-3.1V5.9L9 2.8z" />
        </>,
        color
      );
  }
}

export const SERVICE_VISUALS = {
  apigateway: { label: "API Gateway", color: "#FF9900", accent: "#ffb84d", role: "trigger", description: "HTTP API entry point — routes incoming requests to backend services" },
  lambda: { label: "Lambda", color: "#FF9900", accent: "#ffd27a", role: "processor", description: "Serverless function — runs your code on demand without managing servers" },
  sqs: { label: "SQS", color: "#FF4F8B", accent: "#ff8ab1", role: "queue", description: "Message queue — decouples producers and consumers, guarantees delivery" },
  eventbridge: { label: "EventBridge", color: "#E7157B", accent: "#ff63b3", role: "trigger", description: "Event bus — routes events from AWS services and custom applications" },
  dynamodb: { label: "DynamoDB", color: "#4053D6", accent: "#8f9fff", role: "storage", description: "NoSQL database — fast key-value and document storage at any scale" },
  s3: { label: "S3", color: "#7B2D8B", accent: "#c885da", role: "storage", description: "Object storage — stores files, backups, static assets and data lakes" },
  ec2: { label: "EC2", color: "#FF9900", accent: "#ffd27a", role: "processor", description: "Virtual machine — configurable compute instance running your workload" },
  rds: { label: "RDS", color: "#3F8624", accent: "#8ed66d", role: "storage", description: "Relational database — managed SQL database (MySQL, Postgres, Aurora, etc.)" },
  sns: { label: "SNS", color: "#FF4F8B", accent: "#ff8ab1", role: "queue", description: "Notification service — pub/sub messaging for fan-out and alerts" },
  ecs: { label: "ECS", color: "#FF9900", accent: "#ffd27a", role: "processor", description: "Container service — runs Docker containers on managed clusters" },
  stepfunctions: { label: "Step Functions", color: "#00B4E0", accent: "#66d4f0", role: "processor", description: "Workflow orchestrator — coordinates multi-step serverless workflows" },
  kinesis: { label: "Kinesis", color: "#C766D4", accent: "#dfa0e8", role: "queue", description: "Data stream — real-time ingestion and processing of streaming data" },
  cognito: { label: "Cognito", color: "#DD344C", accent: "#ff8394", role: "trigger", description: "User authentication — manages sign-up, sign-in, and access control" },
  cloudfront: { label: "CloudFront", color: "#00E7FF", accent: "#66f0ff", role: "trigger", description: "CDN — delivers content globally with low latency via edge locations" },
  elasticache: { label: "ElastiCache", color: "#3F8624", accent: "#8ed66d", role: "storage", description: "In-memory cache — managed Redis or Memcached for sub-millisecond reads" },
  glue: { label: "Glue", color: "#00B4E0", accent: "#66d4f0", role: "processor", description: "ETL service — discovers, transforms, and loads data between stores" },
  appsync: { label: "AppSync", color: "#00B4E0", accent: "#66d4f0", role: "trigger", description: "GraphQL API — managed real-time data queries and mutations" },
  redshift: { label: "Redshift", color: "#7B2D8B", accent: "#c885da", role: "storage", description: "Data warehouse — petabyte-scale analytics with columnar storage" },
  route53: { label: "Route 53", color: "#00E7FF", accent: "#66f0ff", role: "trigger", description: "DNS service — routes users to applications via domain name resolution" },
  elb: { label: "ELB", color: "#FF9900", accent: "#ffd27a", role: "trigger", description: "Load balancer — distributes traffic across targets for high availability" },
  secretsmanager: { label: "Secrets Manager", color: "#DD344C", accent: "#ff8394", role: "unknown", description: "Secrets store — securely manages API keys, passwords, and credentials" },
  kms: { label: "KMS", color: "#DD344C", accent: "#ff8394", role: "unknown", description: "Key management — creates and controls encryption keys for your data" },
  iam: { label: "IAM", color: "#DD344C", accent: "#ff8394", role: "unknown", description: "Identity and access management — controls who can access what resources" },
  unknown: { label: "AWS Resource", color: "#6f8596", accent: "#a8bac7", role: "unknown", description: "AWS resource — part of your cloud architecture" },
};

export function getServiceVisual(service) {
  return SERVICE_VISUALS[normalizeServiceName(service)] || SERVICE_VISUALS.unknown;
}

export function getServiceRole(service) {
  return getServiceVisual(service).role || "unknown";
}
