# CloudWire

AWS infrastructure visualization tool — scan your account and explore service dependencies as an interactive graph, directly in your browser.

[![PyPI version](https://img.shields.io/pypi/v/cloudwire.svg)](https://pypi.org/project/cloudwire/)
[![Python versions](https://img.shields.io/pypi/pyversions/cloudwire.svg)](https://pypi.org/project/cloudwire/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Build](https://github.com/Himanshu-370/cloudwire/actions/workflows/publish.yml/badge.svg)](https://github.com/Himanshu-370/cloudwire/actions/workflows/publish.yml)

If CloudWire saves you time, a [GitHub star](https://github.com/Himanshu-370/cloudwire) helps others find it.

No data leaves your system. AWS credentials never leave your terminal. The graph is built locally using your existing credential chain (`~/.aws/credentials`, `aws sso login`, `saml2aws`, `aws-vault` — all work out of the box).

<p align="center">
  <img src="docs/cloudgraph.svg" alt="CloudWire — AWS infrastructure graph visualization" width="100%">
</p>

---

## Quick start

```bash
# Install
pip install cloudwire

# Launch (opens http://localhost:8080 automatically)
cloudwire

# Target a specific profile and region
cloudwire --profile staging --region us-east-1

# Overlay cost data from AWS Cost Explorer
cloudwire --costs
```

> **Tip:** Prefer isolated installs? Use `pipx install cloudwire` instead.

> **Requirements:** Python 3.9+ and valid AWS credentials configured locally.

On first load, select the services you want to scan from the top bar and click **Scan**. The graph populates in real time as resources are discovered.

---

## Why CloudWire?

Tools like [Rover](https://github.com/im2nguyen/rover), [Terravision](https://github.com/patrickchugh/terravision), and [Inframap](https://github.com/cycloidio/inframap) visualize infrastructure from Terraform state files. CloudWire takes a different approach:

- **Live scanning, not plan-file parsing** — CloudWire queries your AWS account directly via boto3, discovering resources and relationships in real time. No Terraform required. You can also import `.tfstate`/`.tf` files if you prefer.
- **Relationship inference** — edges aren't just "resource A references resource B." CloudWire resolves IAM policies, environment variable references, event triggers, and VPC containment to surface connections that don't appear in any state file.
- **Runs entirely local** — single Python process, no database, no cloud backend, no signup. Your AWS credentials never leave your machine.

---

## Key features

- **Interactive graph** — dark-themed canvas with animated data flow, pan/zoom, and SVG export
- **24 AWS services** with dedicated scanners, icons, colors, and relationship inference
- **Real edges** — API integrations, event triggers, IAM policy inference, env var references, VPC containment
- **VPC topology** — subnets, security groups, IGWs, NAT GWs, route tables with AZ grouping and internet exposure detection
- **Tag-based scanning** — discover and scan resources by AWS tags
- **Terraform import** — upload `.tfstate` or `.tf` files to visualize without AWS credentials
- **Cost overlay** — visualize per-resource AWS spend from Cost Explorer directly on graph nodes (EC2, RDS, S3, DynamoDB, ElastiCache, Redshift), with service-level totals for Lambda, SQS, and more
- **Analysis tools** — blast radius, shortest path, architecture summary, pattern detection
- **Three layout modes** — Circular, Flow, Swimlane — switchable from the toolbar
- **Permission-aware** — missing IAM policies surfaced clearly, never blocks the scan


---

## Required IAM permissions

CloudWire is **read-only**. All operations use `List*`, `Describe*`, and `Get*` API actions only — no write access required.

A minimal IAM policy is documented in [docs/USAGE.md](docs/USAGE.md). The recommended starting point:

```
arn:aws:iam::aws:policy/ReadOnlyAccess
```

If you use a more restrictive policy, CloudWire will scan what it can and clearly report which services were denied — it never fails silently.

### Cost overlay (optional)

The `--costs` flag requires AWS Cost Explorer to be activated in the [Billing console](https://console.aws.amazon.com/billing/home#/costexplorer). For per-resource cost breakdowns, enable "hourly and resource-level data" in Cost Explorer settings (takes 24 hours to activate). The additional IAM permissions required are:

```
ce:GetCostAndUsage
ce:GetCostAndUsageWithResources
```

---

## Supported services

| Service | Scanner |
|---------|---------|
| API Gateway | Dedicated — REST + HTTP APIs, multi-service integrations, Cognito authorizers |
| Lambda | Dedicated — functions, event source mappings, env var references, IAM policy inference |
| SQS | Dedicated — queues, attributes, dead letter queue edges |
| SNS | Dedicated — topics and subscriptions |
| EventBridge | Dedicated — rules and targets |
| DynamoDB | Dedicated — tables, streams, global table replicas |
| EC2 | Dedicated — instances, VPC, subnet, security group, instance profile edges |
| ECS | Dedicated — clusters, services, task definitions, load balancer edges |
| S3 | Dedicated — buckets and Lambda notification edges |
| RDS | Dedicated — DB instances and clusters |
| Step Functions | Dedicated |
| Kinesis | Dedicated |
| IAM | Dedicated — roles with full policy resolution |
| Cognito | Dedicated — user pools |
| CloudFront | Dedicated — distributions, S3/API GW/ELB origins, Lambda@Edge |
| Route 53 | Dedicated — hosted zones, record sets, alias target edges |
| ElastiCache | Dedicated — cache clusters |
| Redshift | Dedicated — clusters |
| Glue | Dedicated — jobs, crawlers, triggers |
| AppSync | Dedicated — GraphQL APIs |
| Secrets Manager | Dedicated |
| KMS | Dedicated |
| VPC Network | Dedicated — VPCs, subnets, security groups, IGWs, NAT GWs, route tables |
| ELB | Discovered via CloudFront, Route 53, ECS edges |
| Everything else | Generic (tagged resources only) |

---

## How it works

CloudWire is a Python CLI (FastAPI backend) that serves a pre-compiled React frontend. The backend scans AWS via boto3 and builds a [networkx](https://networkx.org/) graph. The frontend visualizes it using a custom SVG canvas engine. No database, no cloud dependency — everything runs in a single process on your machine.

---

## Contributing

We welcome contributions! See [CONTRIBUTING.md](CONTRIBUTING.md) for setup instructions, project structure, code style, and PR guidelines.

```bash
git clone https://github.com/Himanshu-370/cloudwire
cd cloudwire
make dev   # starts backend + frontend in parallel
```

---

## Links

- [Architecture deep dive](docs/ARCHITECTURE.md)
- [Full feature list](docs/FEATURES.md)
- [Usage & setup guide](docs/USAGE.md)
- [Changelog](CHANGELOG.md)
- [Release guide for maintainers](docs/RELEASING.md)
- [Security policy](SECURITY.md)

## License

MIT — see [LICENSE](LICENSE).
