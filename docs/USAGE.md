# Usage Guide

Everything you need to install, configure, and use cloudwire.

---

## Requirements

- Python 3.9 or later
- AWS credentials configured on your machine (see [AWS credentials](#aws-credentials) below)
- A supported OS: macOS, Linux, or Windows (WSL recommended on Windows)

---

## Installation

```bash
pip install cloudwire
```

If you use `pipx` (recommended — keeps the tool isolated from your project environments):

```bash
pipx install cloudwire
```

---

## Quick start

```bash
cloudwire
```

The server starts on `http://localhost:8080` and your browser opens automatically.

1. Select the AWS services you want to scan from the dropdown in the top bar
2. Choose a region
3. Click **Scan**
4. Watch the graph build in real time as resources are discovered

---

## CLI reference

```
cloudwire [OPTIONS]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--port` | `8080` | Local port to listen on |
| `--host` | `127.0.0.1` | Bind address |
| `--profile` | (AWS default) | AWS credentials profile from `~/.aws/credentials` |
| `--region` | `us-east-1` | Default AWS region shown in the UI |
| `--no-browser` | off | Start server without opening the browser |
| `--print-url` | off | Print the URL to stdout and exit (useful for scripting) |
| `--version` | — | Print the installed version |
| `--help` | — | Show help and exit |

### Examples

```bash
# Use a specific AWS profile
cloudwire --profile production

# Use a specific region
cloudwire --region eu-west-1

# Custom port (if 8080 is taken)
cloudwire --port 9000

# Start without auto-opening the browser
cloudwire --no-browser

# SSH tunnel workflow — print the URL and open it locally
cloudwire --print-url
# → http://localhost:8080
```

---

## AWS credentials

cloudwire reads credentials from the standard AWS credential chain in this order:

1. **Environment variables** — `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`
2. **AWS CLI profiles** — `~/.aws/credentials` and `~/.aws/config`
3. **IAM instance/container role** — when running on EC2, ECS, or Lambda

This means any tool that writes to the standard credential files works automatically:

```bash
# AWS SSO
aws sso login --profile my-profile
cloudwire --profile my-profile

# saml2aws
saml2aws login
cloudwire

# aws-vault
aws-vault exec my-profile -- cloudwire

# Standard AWS CLI profile
aws configure --profile staging
cloudwire --profile staging
```

### Minimum IAM permissions

cloudwire only needs **read** access. The exact permissions depend on which services you scan, but a good starting point is attaching the `ReadOnlyAccess` managed policy to your IAM role or user.

For a tighter policy, here are the core permissions used per service:

| Service | Permissions needed |
|---------|-------------------|
| Lambda | `lambda:ListFunctions`, `lambda:ListEventSourceMappings` |
| API Gateway | `apigateway:GET` |
| SQS | `sqs:ListQueues`, `sqs:GetQueueAttributes` |
| SNS | `sns:ListTopics`, `sns:ListSubscriptions` |
| EventBridge | `events:ListRules`, `events:ListTargetsByRule` |
| DynamoDB | `dynamodb:ListTables`, `dynamodb:DescribeTable` |
| EC2 | `ec2:DescribeInstances` |
| VPC Network | `ec2:DescribeVpcs`, `ec2:DescribeSubnets`, `ec2:DescribeSecurityGroups`, `ec2:DescribeInternetGateways`, `ec2:DescribeNatGateways`, `ec2:DescribeRouteTables` |
| ECS | `ecs:ListClusters`, `ecs:ListServices`, `ecs:DescribeServices`, `ecs:DescribeTaskDefinition` |
| S3 | `s3:ListAllMyBuckets`, `s3:GetBucketNotification` |
| RDS | `rds:DescribeDBInstances`, `rds:DescribeDBClusters` |
| Step Functions | `states:ListStateMachines` |
| Kinesis | `kinesis:ListStreams` |
| IAM | `iam:ListRoles`, `iam:ListRolePolicies`, `iam:GetRolePolicy`, `iam:ListAttachedRolePolicies`, `iam:GetPolicy`, `iam:GetPolicyVersion` |
| Cognito | `cognito-idp:ListUserPools` |
| CloudFront | `cloudfront:ListDistributions`, `cloudfront:GetDistribution` |
| Route 53 | `route53:ListHostedZones`, `route53:ListResourceRecordSets` |
| Redshift | `redshift:DescribeClusters` |
| Secrets Manager | `secretsmanager:ListSecrets` |
| KMS | `kms:ListKeys`, `kms:DescribeKey` |
| ElastiCache | `elasticache:DescribeCacheClusters` |
| Glue | `glue:ListJobs`, `glue:GetCrawlers`, `glue:GetTriggers` |
| AppSync | `appsync:ListGraphqlApis` |
| Tag-based scanning | `tag:GetTagKeys`, `tag:GetTagValues`, `tag:GetResources` |
| Generic fallback* | `tag:GetResources` |
| All scans | `sts:GetCallerIdentity` (to resolve account ID) |

> *\*Generic fallback:* Services not listed above (and any additional services discovered via tags) use the Resource Groups Tagging API (`tag:GetResources`) rather than dedicated service APIs. These will only discover resources that have AWS tags attached.

---

## Using the UI

### Scanning

- **Services dropdown** — select one or more AWS services to scan. Only selected services are scanned.
- **Region selector** — pick the AWS region. CloudFront is global and always uses `us-east-1` regardless.
- **Mode** — Quick scans list resources only. Deep scans describe each resource individually (slower but richer data).
- **Scan button** — starts the scan. Disabled until at least one service is selected.
- **Stop button** — cancels a running scan. Resources already discovered are kept on the graph.

### Graph canvas

- **Pan** — click and drag on empty canvas space
- **Zoom** — scroll wheel or pinch on trackpad
- **Select node** — click a node to open the inspector panel on the right
- **Fit to screen** — double-click on empty canvas space
- **Blast radius** — when a node is selected, connected upstream/downstream nodes are highlighted
- **Flow animation** — enabled by default, shows animated particles along edges indicating data flow direction. Toggle with the ANIMATE button in the toolbar
- **START/END badges** — entry points (no incoming edges) show a START badge, terminal nodes (no outgoing edges) show an END badge

### Inspector panel

Opens when you click a node. Shows:
- Resource ID and service type
- All attributes returned by the AWS API (region, ARN, state, tags, etc.)
- Incoming and outgoing edges (what calls this, what this calls)

### Search

The search bar in the left sidebar filters nodes by ID or label. Results are capped at 120 — refine your search if truncated.

### Layout

Switch between layout modes from the layout dropdown in the graph toolbar:
- **Circular** (default) — nodes arranged in service clusters
- **Flow** — sequential left-to-right layout with START/END badges showing data flow direction
- **Swimlane** — grouped by role (triggers, processors, storage, queues) in horizontal lanes

### Isolated nodes

By default, nodes with no edges are hidden (they have no relationships to other scanned services). Toggle **Show isolated** in the top bar to reveal them. If all nodes are isolated (e.g. you scanned only SQS), they are shown automatically.

### Tag-based scanning

Switch the mode toggle from **Services** to **Tags** to scan by AWS resource tags:

1. Select tag keys from the searchable, multi-select key dropdown
2. Select values from the merged, searchable value dropdown
3. Click **ADD FILTER** — a filter chip appears
4. Add more filters if needed (they combine as AND)
5. Click **Scan by tags** to discover and scan matching resources

Your manual service selections are preserved — they are not overwritten by tag scan results.

### Terraform import

You can visualize infrastructure from Terraform files without AWS credentials:

1. Drag and drop `.tfstate`, `.json` (state files), or `.tf` (HCL) files onto the **Terraform drop zone** in the UI
2. cloudwire parses the files, extracts `aws_*` resources, and builds the graph using the same visualization pipeline as live scans
3. Relationships between resources are inferred from resource attributes, ARN references, and environment variables

**Limits:**
- Maximum **10 files** per upload
- Maximum **20 MB** total upload size
- Accepted extensions: `.tfstate`, `.json`, `.tf`

Multiple files can be combined in a single upload to build a unified graph. This is useful for reviewing Terraform plans or visualizing infrastructure when you don't have direct AWS access.

### VPC network topology

Include **VPC Network** in your service selection to see CloudMapper-style network diagrams. VPC scanning runs after all other services (Phase 2) and is automatically scoped to only the VPCs your resources reference.

- Click container annotations (VPC/AZ/Subnet backgrounds) to collapse or expand them
- Hover over internet-exposed resources to see the full exposure path highlighted
- Security group edges show port range labels (e.g. `443/tcp`)

---

## SSH tunnel workflow

If cloudwire is running on a remote server and you want to view the UI in your local browser:

```bash
# On the remote server (no browser)
cloudwire --no-browser --port 8080

# In a separate terminal on your local machine
ssh -L 8080:localhost:8080 user@remote-host

# Open locally
open http://localhost:8080
```

Or use `--print-url` to get the URL in a script:

```bash
URL=$(ssh user@remote-host "cloudwire --print-url --port 8080")
# start tunnel, then open $URL
```

---

## Local development

If you want to run cloudwire from source (for development, testing, or contributing):

### Prerequisites

- Python 3.9+
- Node.js 18+ and npm
- AWS credentials configured (see [AWS credentials](#aws-credentials))

### Option 1: Makefile (recommended)

```bash
# Install Python package in editable mode
make install-dev

# Run backend + frontend dev servers concurrently
make dev
```

This starts the FastAPI backend on `http://localhost:8000` and the Vite frontend dev server on `http://localhost:5173` (with hot reload).

### Option 2: Run manually

```bash
# Backend
pip install -e .
python3 -m uvicorn cloudwire.app.main:app --reload --port 8000

# Frontend (separate terminal)
cd frontend
npm install          # first time only
npm run dev          # http://localhost:5173
```

### Production-style build

```bash
make frontend        # builds React app into cloudwire/static/
cloudwire            # serves both API and UI on http://localhost:8080
```

### Testing a scan

1. Open the frontend (`http://localhost:5173` in dev mode, or `http://localhost:8080` after `make frontend`)
2. Select one or more AWS services from the dropdown
3. Pick a region where you have resources
4. Choose **Quick** mode for a fast first test, or **Deep** for full resource enrichment
5. Click **Scan** and watch the graph build in real time

If you don't have AWS resources, you can verify the backend starts correctly by hitting the health endpoint:

```bash
curl http://localhost:8000/api/health
```

---

## Caching

Completed scans are cached for 5 minutes (quick mode) or 30 minutes (deep mode). If you scan the same region and services again within the cache window, the existing results are returned immediately without re-scanning. To bypass the cache, either pass `force_refresh: true` in the API payload or wait for the cache window to expire. Re-scanning with the same parameters within the window will return the cached result.

---

## Troubleshooting

**Port already in use**
```
Error: Port 8080 is already in use. Try a different port with --port <number>.
```
Another process is using the port. Run `cloudwire --port 9000` or find and stop the conflicting process.

---

**AWS credentials not found**
```
AWS credentials were not found. Set AWS credentials or run saml2aws login before scanning.
```
Run `aws configure`, `aws sso login`, or `saml2aws login` first, then retry.

---

**Session expired**
```
Your AWS session has expired. Refresh credentials and try again.
```
Re-authenticate with your SSO/SAML provider, then rescan.

---

**Graph is empty after scanning**
- Check that resources actually exist in the selected region
- Some services (like SQS, SNS) don't have edges to other services by default — toggle **Show isolated** to reveal them
- The generic fallback scanner only finds resources that have AWS tags. If your resources are untagged, use a dedicated scanner service from the list above

---

**Scan completes but graph takes a while to appear**
The graph is fetched after the scan job transitions to `completed`. If your account has many resources, the final graph payload can be large. Wait a few seconds after the status bar shows "completed".

---

**Permission errors during scan**
If your IAM role or user is missing permissions for some services, cloudwire shows them in an expandable panel at the bottom of the page. Permission errors are highlighted in red. Click the panel to see the full list. Grant the missing permissions (see [minimum IAM permissions](#minimum-iam-permissions)) and rescan.

---

**Stale data after page reload**
Reloading the page starts with a clean slate — no graph, no warnings, no sidebar data. You need to run a new scan to populate the graph. This is by design to avoid showing stale infrastructure data.
