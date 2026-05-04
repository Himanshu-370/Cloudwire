# Architecture

How cloudwire works under the hood — from `pip install` to interactive graph.

---

## Tech stack

| Layer | Technology | Role |
|-------|-----------|------|
| CLI | Python + [Click](https://click.palletsprojects.com/) | `cloudwire` entry point, dependency checks, launches Uvicorn |
| API server | [FastAPI](https://fastapi.tiangolo.com/) + [Uvicorn](https://www.uvicorn.org/) | REST API, file uploads, static file serving |
| AWS SDK | [boto3](https://boto3.amazonaws.com/v1/documentation/api/latest/) / botocore | All AWS API calls (read-only: `Describe*`, `List*`, `Get*`) |
| Graph engine | [networkx](https://networkx.org/) `DiGraph` | In-memory directed graph with thread-safe mutations |
| Validation | [Pydantic](https://docs.pydantic.dev/) v2 | Request/response models |
| HCL parsing | [python-hcl2](https://pypi.org/project/python-hcl2/) | Terraform `.tf` file parsing |
| Frontend | [React](https://react.dev/) 18 + [Vite](https://vitejs.dev/) 6 | UI framework and build tool |
| Graph rendering | Custom SVG (no external graph library) | `<svg>` + `<g>` transforms for pan/zoom/drag |

No database. No external services. Everything runs in a single process on your machine.

---

## High-level data flow

```
User clicks "Scan"
       │
       ▼
┌─────────────────┐    POST /api/scan    ┌──────────────────┐
│  React Frontend  │ ──────────────────▶ │  FastAPI Backend   │
│  (browser)       │                      │  (localhost:8080)  │
└────────┬────────┘                      └────────┬───────────┘
         │                                         │
         │  polls GET /api/scan/{id}/graph         │  submits to ThreadPoolExecutor
         │  every 1-3 seconds                      │
         │                                         ▼
         │                               ┌──────────────────┐
         │                               │  AWSGraphScanner  │
         │                               │  (background      │
         │                               │   thread)          │
         │                               └────────┬───────────┘
         │                                         │
         │                                         │  boto3 calls (parallel)
         │                                         ▼
         │                               ┌──────────────────┐
         │                               │   AWS APIs        │
         │                               │   (read-only)     │
         │                               └────────┬───────────┘
         │                                         │
         │                                         │  nodes + edges
         │                                         ▼
         │                               ┌──────────────────┐
         │  JSON graph payload           │   GraphStore      │
         │◀──────────────────────────────│   (networkx       │
         │                               │    DiGraph)        │
         │                               └──────────────────┘
         ▼
┌─────────────────┐
│  Graph Pipeline  │  filter → cluster → layout → render
│  (useMemo chain) │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  SVG Canvas      │  nodes, edges, annotations, pan/zoom
│  (GraphCanvas)   │
└─────────────────┘
```

---

## Backend architecture

### Request routing

```
main.py (assembly)
  ├── routes/scan.py       → /api/scan, /api/scan/{id}, /api/scan/{id}/graph, /api/scan/{id}/stop
  ├── routes/tags.py       → /api/tags/keys, /api/tags/values, /api/tags/resources
  ├── routes/terraform.py  → /api/terraform/parse
  ├── errors.py            → APIError, error_payload(), friendly_exception_message()
  └── aws_clients.py       → boto3 client factories, region validation
```

`main.py` is the assembly point. It creates the FastAPI app, registers middleware, exception handlers, and includes the three route modules. `aws_clients.py` is the centralized boto3 client factory with adaptive retry configuration (`mode: adaptive`, `max_attempts: 10`).

### Middleware stack

Middleware is applied in the following order (outermost first):

1. **`RequestBodyLimitMiddleware`** — Caps JSON request bodies at 2 MB. Returns `413` for oversized payloads. Protects scan and tag endpoints from accidental or malicious large payloads.
2. **`SecurityHeadersMiddleware`** — Adds security headers to all responses: `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Content-Security-Policy`, and others.

### API 404 catch-all

A catch-all route (`/api/{path}`) returns a JSON 404 response instead of falling through to the SPA HTML. This prevents API misroutes from serving the React app, which would mask errors for API clients.

### Scan lifecycle

1. **`POST /api/scan`** validates the request, resolves the AWS account ID via STS, checks for a cached/in-flight job with the same parameters, and submits the scan to a thread pool.

2. **`ScanJobStore`** manages the job lifecycle: `queued → running → completed/failed/cancelled`. It holds up to 4 concurrent workers and caps in-flight jobs at 8. Completed jobs are cached by a deterministic key (account + region + services + mode + tag hash) with a TTL of 5 minutes (quick) or 30 minutes (deep).

3. **`AWSGraphScanner.scan()`** runs in a background thread with two phases:

```
Phase 1: Non-VPC services (parallel)
  ┌─────────────────────────────────────────────┐
  │  ThreadPoolExecutor(max_workers=5)           │
  │                                              │
  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  │
  │  │ Lambda   │  │ SQS      │  │ DynamoDB │  │
  │  │ scanner  │  │ scanner  │  │ scanner  │  │
  │  └──────────┘  └──────────┘  └──────────┘  │
  │        ... up to 5 services at once ...      │
  └─────────────────────────────────────────────┘
                       │
                       ▼
Phase 2: VPC scan (sequential, scoped)
  Only fetches VPCs referenced by Phase 1 results
                       │
                       ▼
Post-scan: Internet exposure computation
  Traces IGW → route table → subnet → open SG → resource
```

Each service scanner is a mixin class (e.g., `LambdaScannerMixin`) that calls AWS APIs and populates the graph. Some scanners use additional internal parallelism — API Gateway uses up to 16 workers for integration fetching, DynamoDB uses up to 16 for describe calls.

### How nodes get into the graph

Every scanner calls `self._node(node_id, **attrs)` which delegates to `GraphStore.add_node()`. The graph store merges attributes if the node already exists (e.g., a Lambda stub created by EventBridge gets enriched when the Lambda scanner runs).

**Node ID format:** `"{service}:{arn_or_path}"` — for example:
- `lambda:arn:aws:lambda:us-east-1:123456789012:function:my-func`
- `vpc:subnet/subnet-0abc123`
- `terraform:module.api.aws_lambda_function.handler`

### How edges are inferred

Edges come from four sources:

| Source | Example |
|--------|---------|
| **Direct API data** | Lambda event source mapping → SQS queue ARN |
| **IAM policy analysis** | Lambda role has `sqs:SendMessage` → edge to SQS queue |
| **Environment variable conventions** | Lambda env `ORDER_TABLE_NAME=orders` → edge to DynamoDB table named "orders" |
| **Cross-scanner references** | API Gateway integration URI contains a Lambda ARN |

After all services are scanned, `_fetch_and_apply_tags()` batch-fetches tags via the Resource Groups Tagging API and attaches them to matching nodes.

### Thread safety

`GraphStore` uses a single `threading.Lock` for all graph and metadata mutations. The scanner maintains separate locks for the node attribute index, IAM cache, and metrics counters. The graph payload is cached after serialization and invalidated on any mutation — so polling requests during an active scan serve a pre-built payload instead of re-serializing the entire graph every 1-3 seconds.

---

## Frontend architecture

### Graph pipeline

The frontend transforms raw API data into positioned, rendered nodes through a pure `useMemo` pipeline in `useGraphPipeline.js`:

```
Raw graph from API
       │
       ▼
1. Region filter         → drop nodes not in the selected region
       │
       ▼
2. Service filter        → drop hidden services
       │
       ▼
3. Connectivity split    → separate connected vs isolated nodes
       │
       ▼
4. Service clustering    → collapse "8 Lambda" into one cluster node
       │
       ▼
5. Container collapse    → collapse VPC/subnet children into summary nodes
       │
       ▼
6. Focus mode            → BFS from selected node, keep only N-hop neighborhood
       │
       ▼
7. Layout algorithm      → assign (x, y) positions to every node
       │
       ▼
8. Network annotations   → compute VPC/AZ/subnet bounding boxes
       │
       ▼
Positioned nodes + edges + annotations → GraphCanvas
```

Each step is a separate `useMemo` with precise dependency tracking — only the steps affected by a state change re-run.

### Layout algorithms

All layout runs client-side in JavaScript. No server-side layout computation.

**Flow layout** — Topological sort (Kahn's algorithm) assigns each node a level. Nodes are placed left-to-right by level, top-to-bottom within each level. Overflow within a level wraps to a secondary column.

**Circular layout** — Same topological levels, but nodes are placed in concentric rings. Ring radius grows with level depth. Each ring's capacity is computed from its circumference to maintain readable spacing.

**Swimlane layout** — Nodes are classified by role (trigger, queue, processor, storage, network) and placed in horizontal lanes. Within each lane, nodes are sorted by connection count.

**Hybrid orchestration** — The graph is split into connected components. Each component is laid out independently (flow or circular), then components are arranged in a grid. Isolated nodes are placed in a separate grid to the right.

### Rendering

`GraphCanvas` renders a single `<svg>` element. Pan, zoom, and drag are implemented via mouse event handlers that update a transform on a root `<g>` element. There is no Canvas API, no D3, no WebGL — just React components rendering SVG directly.

**Viewport culling** keeps large graphs fast: only nodes within the visible viewport (plus a 300px buffer) are rendered. Edges connecting off-screen nodes are excluded.

Each node is a `<GraphNode>` component (SVG `<g>` with `<rect>` + `<text>` + service icon). Each edge is a `<GraphEdge>` component (SVG `<path>` with `<marker>` arrowheads). Container annotations are SVG `<rect>` + `<text>` in the background layer.

### Polling

`useScanPolling` uses `setTimeout` chains (not `setInterval`) with adaptive delays:

| Scan elapsed time | Poll interval |
|-------------------|---------------|
| 0 – 30 seconds | 1 second |
| 30 – 60 seconds | 2 seconds |
| > 60 seconds | 3 seconds |

A stale-poll token (integer, incremented on every new scan) ensures async callbacks from abandoned polls never update state. Scans are auto-abandoned after 10 minutes (`MAX_SCAN_MS = 10 * 60 * 1000`).

The hook also exposes a `bootstrapLoading` state, distinct from `scanLoading`. `bootstrapLoading` fires when the page loads and detects an active job already in progress (e.g., after a page refresh mid-scan), allowing the UI to resume polling without requiring a new scan.

---

## Terraform parsing

The Terraform parser handles `.tfstate` (JSON) and `.tf` (HCL) files uploaded via `POST /api/terraform/parse`.

**Two-pass algorithm:**

*Pass 1 — Node registration:* Each `aws_*` resource in the state file is mapped to a cloudwire service/type pair via an 80+ entry lookup table (`TF_RESOURCE_TYPE_MAP`). Sensitive attributes (passwords, tokens, private keys) are stripped before storage. Secondary indices are built for O(1) edge lookups.

*Pass 2 — Edge inference:* Type-specific extractors handle Lambda environment variables, API Gateway integration URIs, EventBridge rule-target links, ECS cluster-service-task chains, CloudFront origins, IAM role attachments, and more. A generic ARN sweep catches any remaining cross-resource references.

The result is registered as a completed `ScanJob` — the same frontend polling/display logic works for both live scans and Terraform imports.

---

## VPC topology and internet exposure

The VPC scanner runs in Phase 2 (after all other services) and only fetches VPCs that were referenced by Phase 1 resources — avoiding the noise of scanning all VPCs in the account.

**Internet exposure detection** traces the path from the public internet to a resource:

```
Internet → IGW → Route Table → Subnet → Security Group (open ingress) → Resource
```

Each resource in an internet-reachable subnet with an open security group gets marked `exposed_internet=True` with the full path stored for frontend highlighting.

---

## Graph data model

### Node (example)

```json
{
  "id": "lambda:arn:aws:lambda:us-east-1:123456789012:function:order-processor",
  "label": "order-processor",
  "service": "lambda",
  "type": "lambda",
  "region": "us-east-1",
  "arn": "arn:aws:lambda:us-east-1:123456789012:function:order-processor",
  "runtime": "python3.12",
  "handler": "app.handler",
  "memory_size": 256,
  "timeout": 30,
  "role": "arn:aws:iam::123456789012:role/order-processor-role",
  "tags": { "env": "production", "team": "payments" }
}
```

### Edge (example)

```json
{
  "id": "sqs:arn:...order-queue→lambda:arn:...order-processor",
  "source": "sqs:arn:aws:sqs:us-east-1:123456789012:order-queue",
  "target": "lambda:arn:aws:lambda:us-east-1:123456789012:function:order-processor",
  "relationship": "triggers",
  "via": "event_source_mapping"
}
```

### Relationship types

| Relationship | Meaning |
|-------------|---------|
| `triggers` | Source invokes/triggers target (event source mapping, EventBridge target) |
| `integrates` | API Gateway integration to a backend |
| `references` | Resource references another via env var, ARN attribute, etc. |
| `assumes` | Resource assumes an IAM role |
| `contains` | VPC contains subnet, subnet contains resource, cluster contains service |
| `protects` | Security group protects a resource |
| `routes` | Route table routes to a subnet |
| `routes_via` | IGW/NAT routes via a route table |
| `gateway` | Internet anchor connects to IGW |
| `delivers` | SNS topic delivers to a subscriber |
| `notifies` | S3 bucket notification to Lambda/SQS/SNS |
| `dead_letter` | Dead letter queue configuration |
| `forwards_to` | Load balancer forwards to target group |
| `origin` | CloudFront distribution origin |
| `allows` | Security group allows traffic (with port_range) |

---

## Caching strategy

**Graph payload cache** (`GraphStore._cached_payload`) — The serialized JSON payload is cached after the first `get_graph_payload()` call. Any mutation (add node, add edge, update metadata) invalidates the cache. During active scans, the cache is frequently invalidated; once a scan completes, repeated poll requests (and the final status fetch) are served from cache without re-serializing the entire graph.

**Job result cache** (`ScanJobStore._cache`) — Completed scans are cached by a deterministic key derived from: account ID, region, sorted services, scan mode, IAM/describe flags, and a hash of tag ARNs. Cache TTL is 5 minutes for quick scans, 30 minutes for deep scans. On cache hit, the existing completed job is returned without re-scanning.

**Job retention** — Up to 50 completed/failed/cancelled jobs are retained in memory. Older jobs are pruned unless they are the latest graph or have an active cache entry.
