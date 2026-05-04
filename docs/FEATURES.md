# cloudwire — Features

A complete overview of what cloudwire does and what you can do with it.

---

## Core concept

cloudwire connects to your AWS account, discovers resources across the services you choose, and draws them as an interactive graph in your browser. Each node is an AWS resource. Each edge is a real relationship — an event trigger, a queue subscription, an API integration, a database connection.

The result is a live map of how your infrastructure is actually wired together.

---

## Scanning

### Multi-service scanning
Select any combination of AWS services to scan in a single pass. cloudwire scans all selected services in parallel and builds the graph as results come in — you don't wait for one service to finish before the next starts.

### Real-time progress
A progress bar tracks how many services have been scanned. The graph populates live as each service completes — you can start exploring before the full scan finishes.

### Quick and Deep scan modes
- **Quick** — lists resources only. Fast, low API call volume, results cached for 5 minutes.
- **Deep** — describes each resource individually for richer attribute data. Slower but more detailed, cached for 30 minutes.

### Scan caching
Scanning the same region and services twice within the cache window returns the previous result instantly. No redundant API calls, no waiting.

### Stop mid-scan
Cancel a running scan at any time. Resources already discovered are kept on the graph — you don't lose what was found before you stopped.

### Multi-region support
Scan any of the 29 supported AWS regions. CloudFront distributions are global and always included regardless of region.

---

## Graph visualization

### Interactive canvas
Pan, zoom, and drag the canvas freely. The graph supports thousands of nodes without performance degradation through viewport virtualization — only nodes visible in the current view are rendered.

### Three layout modes
- **Circular** (default) — nodes grouped by service in circular clusters. Good for getting an overview of what exists.
- **Flow** — left-to-right sequential layout following data flow direction. Entry points appear on the left with START badges, terminal nodes on the right with END badges. Good for tracing how requests move through your system.
- **Swimlane** — nodes arranged in horizontal lanes by role (triggers, processors, storage, queues). Good for comparing resources across services side by side.

Switch between layouts at any time from the layout dropdown in the graph toolbar, without re-scanning.

### Node detail levels
The graph adapts to zoom level:
- **Zoomed out far** — nodes render as small colored dots for a high-level overview
- **Zoomed out** — nodes show icons only, no labels
- **Normal** — nodes show service icon, resource name, service type label, and role badge
- **Zoomed in** — full detail including educational tooltip on hover

### Resource state indicators
Nodes for services that expose resource state (EC2, Lambda, RDS, DynamoDB, ECS, ElastiCache, CloudFront, Step Functions) show a colored status dot:
- **Green** — active / running / available
- **Red** — failed / error / disabled
- **Amber** — transitional state (starting, stopping, updating)

### Role badges
Each node is automatically classified with a functional role based on what it does in a cloud architecture:
- **TRIGGER** — entry points and event sources (API Gateway, EventBridge, CloudFront, Route 53, AppSync, Cognito, ELB)
- **PROC** — compute and processing (Lambda, EC2, ECS, Step Functions, Glue)
- **STORE** — data storage (DynamoDB, RDS, S3, ElastiCache, Redshift)
- **QUEUE** — message queuing (SQS, SNS, Kinesis)

### Minimap
A thumbnail overview in the corner shows the full graph and your current viewport position. Click anywhere on the minimap to jump to that area instantly.

---

## Exploring relationships

### Blast radius highlighting
Select any node and toggle blast radius mode to highlight everything connected to it:
- **Orange** — upstream dependencies (what this resource depends on)
- **Cyan** — downstream dependents (what depends on this resource)

Instantly answers "if this Lambda goes down, what else breaks?" or "what feeds into this queue?"

### Path finder
Select a source and destination node to find the shortest connection path between them. cloudwire traces the route through the graph and highlights every hop along the way.

### Focus mode
Narrow the graph to show only a selected node and its immediate neighbors. Adjust the hop depth (1, 2, or 3 hops) to control how much context you see. Everything outside the focus is faded out.

### Flow animation
Animated data flow along graph edges is enabled by default, showing the direction traffic moves through your architecture. Animated particles travel along edges from source to target. Toggle off with the ANIMATE button in the toolbar.

### START / END badges
In flow layout, entry points (nodes with outgoing edges but no incoming edges) are labelled **START** and terminal nodes (incoming but no outgoing) are labelled **END**, making it easy to see where data enters and exits your architecture.

---

## VPC network topology

When you include **VPC Network** in your scan, cloudwire renders a CloudMapper-style network diagram showing how your resources sit inside VPCs, subnets, and availability zones.

### Scoped VPC scanning (two-phase)
VPC scanning runs after all other services complete (Phase 2). cloudwire only fetches infrastructure for VPCs that your scanned resources actually reference — if your Lambdas and RDS instances use 2 out of 15 VPCs, only those 2 are scanned. This dramatically reduces API calls and graph clutter.

### Internet anchor nodes
Each VPC with an Internet Gateway gets a synthetic **Internet** node showing the path from the public internet into your infrastructure.

### Security group rule edges
Inbound SG rules are parsed into labeled edges showing port ranges (e.g. `443/tcp`, `22/tcp`, `all`). Rules open to `0.0.0.0/0` or `::/0` are flagged as internet-exposed regardless of protocol — SSH, HTTPS, or any port-specific rule open to the world will trigger the exposure badge.

### Network exposure detection
Resources reachable from the internet via IGW → public subnet → open security group are automatically flagged with an exposure path. Hover over an exposed resource to highlight the full path from Internet → IGW → Subnet → Security Group → Resource.

### Container annotations
VPCs, availability zones, and subnets render as nested background rectangles with distinct border styles:
- **VPC** — solid border
- **Availability Zone** — dashed border
- **Subnet** — dotted border

Click any container annotation to collapse or expand its children.

### Tag filtering compatibility
VPC topology nodes are preserved when using tag-based filtering. The filter walks VPC infrastructure ancestors to keep the full topology chain visible even when filtering to a subset of tagged resources.

---

## Filtering and search

### Search
Type in the search bar to filter nodes by resource ID or name. Results update as you type. Shows the first 120 matches with a count indicator if more exist.

### Service visibility toggles
Show or hide entire services from the graph with one click. Useful for decluttering when you've scanned many services but only care about a subset.

### Isolated node toggle
Nodes with no connections to other scanned services are hidden by default to keep the graph clean. Toggle "Show isolated" to reveal them. When all nodes in a scan are isolated (e.g. scanning only SQS), they're shown automatically.

### Clustering
When a service has many resources, they're automatically collapsed into a single cluster node showing a count. Expand or collapse individual service clusters from the sidebar.

### Tag-based scanning
Switch to **Tags** mode to scan resources by AWS tags instead of by service. The workflow:
1. Select one or more tag keys from the searchable multi-select dropdown
2. Tag values from all selected keys are merged and shown in a second searchable multi-select dropdown
3. Select values and click **ADD FILTER** to create filter chips
4. Click **Scan by tags** to discover matching resources and scan only the relevant services

Multiple tag filters combine as AND conditions. The discovered services are scanned automatically — your manual service selections are preserved for when you switch back to service-based scanning.

---

## Resource inspector

Click any node to open the inspector panel on the right. It shows:

- **Resource ID** and ARN
- **Service type** and region
- **All attributes** returned by the AWS API — instance type, runtime, table class, status, tags, and more
- **Incoming edges** — what calls or triggers this resource
- **Outgoing edges** — what this resource calls or writes to

---

## Architecture summary

Generate an automatic architecture summary for the scanned graph. The summary describes the overall pattern (event-driven, request-response, data pipeline, etc.), lists the services involved, and highlights key relationships — useful for documentation or onboarding new team members.

---

## Supported AWS services

| Service | What's discovered | Relationships |
|---------|------------------|---------------|
| API Gateway | REST APIs and HTTP APIs | → Lambda, Step Functions, SQS, SNS, Kinesis, EventBridge integrations; ← Cognito authorizers |
| Lambda | All functions with runtime, memory, timeout | → DynamoDB, SQS, S3, Kinesis, ECS, ElastiCache via env var references; → services via IAM policy inference; ← event source mappings |
| SQS | All queues with attributes | ← Lambda triggers, ← SNS subscriptions, → dead letter queues |
| SNS | All topics | → SQS subscriptions, → Lambda subscriptions |
| EventBridge | All rules and their targets | → Lambda, SQS, Step Functions, and any ARN target |
| DynamoDB | All tables with status and billing | ← Lambda streams, → DynamoDB Streams, → global table replicas |
| EC2 | All instances with state | → VPC, Subnet, Security Group, IAM Instance Profile |
| ECS | Clusters and services | → task definitions, → load balancers/target groups, → service roles |
| S3 | All buckets | → Lambda notifications, ← CloudFront origins, ← Glue crawler targets |
| RDS | DB instances and clusters with status | → VPC, Subnet, Security Group |
| Step Functions | All state machines | ← EventBridge targets, ← API Gateway integrations |
| Kinesis | All streams | ← Lambda event sources, ← API Gateway integrations |
| IAM | Roles (capped at 200) | → Lambda (role-to-function edges), policy-based service inference |
| Cognito | User pools | → API Gateway authorizer edges |
| CloudFront | All distributions with status | → S3 origins, → API Gateway origins, → ALB/ELB origins, → Lambda@Edge associations |
| Route 53 | Hosted zones and record sets | → API Gateway, → S3 website, → ELB alias targets |
| ElastiCache | All cache clusters with status | ← Lambda env var references |
| Redshift | All clusters | → VPC, Subnet, Security Group |
| Glue | Jobs, crawlers, and triggers | → S3/DynamoDB crawler targets, → output databases, → job/crawler trigger actions |
| AppSync | All GraphQL APIs | — |
| Secrets Manager | All secrets | — |
| KMS | All keys | — |
| ELB | Load balancers | ← CloudFront origins, ← Route 53 aliases, ← ECS services |
| VPC Network | VPCs, subnets, security groups, internet gateways, NAT gateways, route tables | → Internet anchor nodes, → SG rule edges with port labels, → AZ grouping; scoped to VPCs referenced by other services |
| Everything else | Tagged resources via Resource Groups Tagging API | — |

---

## Scan warnings and permission errors

When cloudwire lacks IAM permissions for a service, it reports the error clearly instead of failing silently. Permission errors and scan warnings appear in an expandable panel at the bottom of the page:

- **Permission errors** are shown in red with a count badge — tells you exactly which services need IAM access
- **Other warnings** (e.g. quick-mode skipped features, truncated results) shown in amber
- Click the panel to expand the full list of warnings

This makes it easy to incrementally grant permissions — scan, see what's missing, add the IAM policy, rescan.

---

## Privacy and security

- **All data stays local.** Nothing is sent to any external server. The graph is built in memory on your machine and served only to your local browser.
- **Read-only AWS access.** cloudwire never creates, modifies, or deletes any AWS resources. It only calls List and Describe APIs.
- **Credentials never leave your terminal.** AWS credentials are read from your local credential chain and used only to make API calls to AWS directly.
- **Runs on localhost only.** The server binds to `127.0.0.1` by default and is never exposed to your network.

---

## Works with every AWS auth method

cloudwire reads credentials from the standard AWS credential chain. Any tool that writes to `~/.aws/credentials` works automatically:

| Tool | How to use |
|------|-----------|
| AWS CLI profiles | `cloudwire --profile my-profile` |
| AWS SSO | `aws sso login` then `cloudwire --profile my-profile` |
| saml2aws | `saml2aws login` then `cloudwire` |
| aws-vault | `aws-vault exec my-profile -- cloudwire` |
| Environment variables | Set `AWS_ACCESS_KEY_ID` etc., then `cloudwire` |
| EC2/ECS instance role | Just run `cloudwire` — credentials are picked up automatically |
