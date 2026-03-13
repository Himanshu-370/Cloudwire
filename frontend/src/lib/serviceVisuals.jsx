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
    "execute-api": "apigateway",
    events: "eventbridge",
    "event-bridge": "eventbridge",
    states: "stepfunctions",
    "cognito-idp": "cognito",
    "elasticloadbalancing": "elb",
    "rds-data": "rds",
    "redshift-data": "redshift",
    monitoring: "cloudwatch",
    es: "opensearch",
    aoss: "opensearch",
    waf: "wafv2",
    "waf-regional": "wafv2",
    neptune: "neptune-db",
    inspector: "inspector2",
    msk: "kafka",
    "emr-serverless": "emr",
    "elastic-beanstalk": "elasticbeanstalk",
    "elasticfilesystem": "efs",
    amazonmq: "mq",
    "certificate-manager": "acm",
  };
  return aliases[key] || key;
}

export function createServiceIcon(service, color, type) {
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
    case "vpc":
      // Internet anchor node gets a globe icon
      if (type === "internet") {
        return wrapIcon(
          <>
            <circle cx="9" cy="9" r="6.5" />
            <ellipse cx="9" cy="9" rx="3" ry="6.5" />
            <line x1="2.5" y1="9" x2="15.5" y2="9" />
            <line x1="3.5" y1="5.5" x2="14.5" y2="5.5" />
            <line x1="3.5" y1="12.5" x2="14.5" y2="12.5" />
          </>,
          color
        );
      }
      return wrapIcon(
        <>
          <rect x="3" y="3.5" width="12" height="11" rx="2" />
          <line x1="3" y1="7.5" x2="15" y2="7.5" />
          <line x1="9" y1="7.5" x2="9" y2="14.5" />
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
    case "emr":
      return wrapIcon(
        <>
          <circle cx="9" cy="9" r="6" />
          <circle cx="9" cy="9" r="2.5" />
          <path d="M6 4.5L12 13.5" />
          <path d="M12 4.5L6 13.5" />
        </>,
        color
      );
    case "opensearch":
      return wrapIcon(
        <>
          <circle cx="8" cy="8" r="4.5" />
          <path d="M11.5 11.5l3 3" />
        </>,
        color
      );
    case "efs":
      return wrapIcon(
        <>
          <path d="M3 5h12v8H3z" />
          <path d="M3 9h12" />
          <path d="M6 5v8" />
        </>,
        color
      );
    case "mq":
      return wrapIcon(
        <>
          <rect x="3" y="4" width="12" height="10" rx="1.5" />
          <path d="M6 7v4" />
          <path d="M9 7v4" />
          <path d="M12 7v4" />
        </>,
        color
      );
    case "eks":
      return wrapIcon(
        <>
          <circle cx="9" cy="9" r="6" />
          <path d="M9 3v12" />
          <path d="M3.5 6l11 6" />
          <path d="M3.5 12l11-6" />
        </>,
        color
      );
    case "ecr":
      return wrapIcon(
        <>
          <rect x="3" y="4" width="12" height="10" rx="2" />
          <path d="M7 7l-2 2 2 2" />
          <path d="M11 7l2 2-2 2" />
        </>,
        color
      );
    case "batch":
      return wrapIcon(
        <>
          <rect x="3" y="3" width="5" height="5" rx="0.8" />
          <rect x="10" y="3" width="5" height="5" rx="0.8" />
          <rect x="3" y="10" width="5" height="5" rx="0.8" />
          <rect x="10" y="10" width="5" height="5" rx="0.8" />
        </>,
        color
      );
    case "elasticbeanstalk":
      return wrapIcon(
        <>
          <path d="M9 3v12" />
          <path d="M5 6h8" />
          <path d="M4 9h10" />
          <path d="M5 12h8" />
        </>,
        color
      );
    case "kafka":
      return wrapIcon(
        <>
          <circle cx="5" cy="5" r="2" />
          <circle cx="13" cy="5" r="2" />
          <circle cx="9" cy="13" r="2" />
          <path d="M7 5h4" />
          <path d="M6.5 6.5L8 11.5" />
          <path d="M11.5 6.5L10 11.5" />
        </>,
        color
      );
    case "firehose":
      return wrapIcon(
        <>
          <path d="M4 5h10l-3 4 3 4H4" />
        </>,
        color
      );
    case "wafv2":
      return wrapIcon(
        <>
          <path d="M9 2L3 5v5c0 3.3 2.6 6.2 6 7 3.4-.8 6-3.7 6-7V5L9 2z" />
          <path d="M7 9l2 2 3-4" />
        </>,
        color
      );
    case "guardduty":
      return wrapIcon(
        <>
          <circle cx="9" cy="8" r="5" />
          <path d="M9 5v4" />
          <circle cx="9" cy="11" r="0.8" fill={color} />
        </>,
        color
      );
    case "cloudwatch":
      return wrapIcon(
        <>
          <circle cx="9" cy="9" r="6" />
          <path d="M9 5v4h3" />
        </>,
        color
      );
    case "cloudtrail":
      return wrapIcon(
        <>
          <path d="M3 13l4-5 3 3 4-8" />
          <circle cx="14" cy="3" r="1.5" />
        </>,
        color
      );
    case "cloudformation":
      return wrapIcon(
        <>
          <rect x="3" y="3" width="12" height="12" rx="1.5" />
          <path d="M6 7l3 2-3 2" />
          <path d="M10 11h3" />
        </>,
        color
      );
    case "athena":
      return wrapIcon(
        <>
          <path d="M4 3h10l-2 12H6L4 3z" />
          <path d="M5 7h8" />
        </>,
        color
      );
    case "sagemaker":
      return wrapIcon(
        <>
          <circle cx="9" cy="4" r="2.5" />
          <path d="M4 14c0-2.8 2.2-5 5-5s5 2.2 5 5" />
          <circle cx="14" cy="8" r="1.5" />
          <path d="M11 4.5l1.5 2.5" />
        </>,
        color
      );
    case "codepipeline":
      return wrapIcon(
        <>
          <circle cx="5" cy="4" r="2" />
          <circle cx="13" cy="9" r="2" />
          <circle cx="5" cy="14" r="2" />
          <path d="M7 4h4l2 5" />
          <path d="M11 9l-4 5" />
        </>,
        color
      );
    case "codebuild":
      return wrapIcon(
        <>
          <rect x="3" y="3" width="12" height="12" rx="1.5" />
          <path d="M6 7l2 2-2 2" />
          <path d="M10 11h3" />
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
  vpc: { label: "VPC Network", color: "#248814", accent: "#5cc044", role: "network", description: "Network topology — VPCs, subnets, security groups, gateways, and routing" },
  // Compute / Containers
  eks: { label: "EKS", color: "#FF9900", accent: "#ffd27a", role: "processor", description: "Managed Kubernetes — runs containerized apps on Kubernetes clusters" },
  ecr: { label: "ECR", color: "#FF9900", accent: "#ffd27a", role: "storage", description: "Container registry — stores, manages, and deploys Docker container images" },
  batch: { label: "Batch", color: "#FF9900", accent: "#ffd27a", role: "processor", description: "Batch computing — runs batch jobs at any scale on managed infrastructure" },
  elasticbeanstalk: { label: "Elastic Beanstalk", color: "#FF9900", accent: "#ffd27a", role: "processor", description: "App platform — deploys and scales web apps with auto-managed infrastructure" },
  apprunner: { label: "App Runner", color: "#FF9900", accent: "#ffd27a", role: "processor", description: "Container service — deploys containerized web apps from source or image" },
  // Storage
  efs: { label: "EFS", color: "#7B2D8B", accent: "#c885da", role: "storage", description: "Elastic file system — serverless shared file storage for EC2 and containers" },
  backup: { label: "AWS Backup", color: "#7B2D8B", accent: "#c885da", role: "storage", description: "Backup service — centralized backup management across AWS services" },
  // Database
  opensearch: { label: "OpenSearch", color: "#3F8624", accent: "#8ed66d", role: "storage", description: "Search and analytics — managed OpenSearch/Elasticsearch clusters" },
  "neptune-db": { label: "Neptune", color: "#3F8624", accent: "#8ed66d", role: "storage", description: "Graph database — managed graph DB for highly connected datasets" },
  dax: { label: "DAX", color: "#3F8624", accent: "#8ed66d", role: "storage", description: "DynamoDB Accelerator — in-memory cache for DynamoDB microsecond reads" },
  // Networking
  acm: { label: "ACM", color: "#00E7FF", accent: "#66f0ff", role: "network", description: "Certificate Manager — provisions and manages SSL/TLS certificates" },
  // Monitoring / Management
  cloudwatch: { label: "CloudWatch", color: "#00B4E0", accent: "#66d4f0", role: "unknown", description: "Monitoring — collects metrics, logs, and alarms for AWS resources" },
  logs: { label: "CloudWatch Logs", color: "#00B4E0", accent: "#66d4f0", role: "unknown", description: "Log management — ingests, stores, and queries application and system logs" },
  cloudformation: { label: "CloudFormation", color: "#00B4E0", accent: "#66d4f0", role: "unknown", description: "Infrastructure as code — provisions AWS resources from declarative templates" },
  cloudtrail: { label: "CloudTrail", color: "#DD344C", accent: "#ff8394", role: "unknown", description: "Audit logging — records API calls and account activity for governance" },
  ssm: { label: "Systems Manager", color: "#00B4E0", accent: "#66d4f0", role: "unknown", description: "Operations hub — manages EC2 instances, patching, parameters, and automation" },
  config: { label: "AWS Config", color: "#00B4E0", accent: "#66d4f0", role: "unknown", description: "Configuration tracking — records and evaluates AWS resource configurations" },
  // Security
  wafv2: { label: "WAF", color: "#DD344C", accent: "#ff8394", role: "unknown", description: "Web application firewall — protects apps from common web exploits" },
  guardduty: { label: "GuardDuty", color: "#DD344C", accent: "#ff8394", role: "unknown", description: "Threat detection — continuously monitors for malicious activity and anomalies" },
  inspector2: { label: "Inspector", color: "#DD344C", accent: "#ff8394", role: "unknown", description: "Vulnerability scanning — automated security assessments of workloads" },
  // Analytics
  athena: { label: "Athena", color: "#4053D6", accent: "#8f9fff", role: "storage", description: "SQL query engine — serverless interactive queries on S3 data" },
  kafka: { label: "MSK", color: "#C766D4", accent: "#dfa0e8", role: "queue", description: "Managed Kafka — fully managed Apache Kafka for streaming data" },
  firehose: { label: "Kinesis Firehose", color: "#C766D4", accent: "#dfa0e8", role: "queue", description: "Data delivery — loads streaming data into S3, Redshift, OpenSearch" },
  emr: { label: "EMR", color: "#4053D6", accent: "#8f9fff", role: "processor", description: "Big data processing — managed Hadoop/Spark clusters for data processing" },
  quicksight: { label: "QuickSight", color: "#4053D6", accent: "#8f9fff", role: "unknown", description: "BI dashboards — serverless business intelligence and visualizations" },
  // Developer Tools
  codepipeline: { label: "CodePipeline", color: "#00B4E0", accent: "#66d4f0", role: "processor", description: "CI/CD pipeline — automates build, test, and deploy workflows" },
  codebuild: { label: "CodeBuild", color: "#00B4E0", accent: "#66d4f0", role: "processor", description: "Build service — compiles source code, runs tests, produces artifacts" },
  codecommit: { label: "CodeCommit", color: "#00B4E0", accent: "#66d4f0", role: "storage", description: "Git repository — managed private Git repos in AWS" },
  codedeploy: { label: "CodeDeploy", color: "#00B4E0", accent: "#66d4f0", role: "processor", description: "Deployment service — automates code deployments to EC2, Lambda, ECS" },
  // Machine Learning
  sagemaker: { label: "SageMaker", color: "#6366F1", accent: "#a5a7fa", role: "processor", description: "ML platform — builds, trains, and deploys machine learning models" },
  bedrock: { label: "Bedrock", color: "#6366F1", accent: "#a5a7fa", role: "processor", description: "GenAI service — access to foundation models for generative AI apps" },
  // Application Integration
  scheduler: { label: "EventBridge Scheduler", color: "#E7157B", accent: "#ff63b3", role: "trigger", description: "Task scheduler — creates scheduled one-time or recurring invocations" },
  pipes: { label: "EventBridge Pipes", color: "#E7157B", accent: "#ff63b3", role: "trigger", description: "Event pipes — point-to-point integrations between event sources and targets" },
  mq: { label: "Amazon MQ", color: "#FF4F8B", accent: "#ff8ab1", role: "queue", description: "Message broker — managed ActiveMQ and RabbitMQ for legacy messaging" },
  // Fallback
  unknown: { label: "AWS Resource", color: "#6f8596", accent: "#a8bac7", role: "unknown", description: "AWS resource — part of your cloud architecture" },
};

export function getServiceVisual(service) {
  return SERVICE_VISUALS[normalizeServiceName(service)] || SERVICE_VISUALS.unknown;
}

export function getServiceRole(service) {
  return getServiceVisual(service).role || "unknown";
}
