# Contributing to CloudWire

Thanks for considering a contribution! This guide covers everything you need to get started.

## Prerequisites

- Python 3.9+ (3.11 recommended)
- Node.js 18+
- AWS credentials configured (any method: `~/.aws/credentials`, SSO, `saml2aws`, `aws-vault`)

## Set up the dev environment

```bash
git clone https://github.com/Himanshu-370/cloudwire
cd cloudwire

# Python
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# Frontend
cd frontend && npm install
```

## Run in development mode

```bash
make dev
```

This starts the FastAPI backend on `:8000` (with `--reload`) and the Vite dev server on `:5173` concurrently. The Vite dev server proxies all `/api/*` requests to the backend.

## Project structure

```
cloudwire/                        # Python package (the distributable unit)
├── __init__.py                 # Package version
├── cli.py                      # `cloudwire` CLI entry point (click)
├── static/                     # Built React app (populated by `make build`)
└── app/                        # FastAPI backend
    ├── main.py                 # App assembly, middleware, exception handlers
    ├── errors.py               # APIError, error payload helpers
    ├── aws_clients.py          # Shared boto3 client factories, region validation
    ├── models.py               # Pydantic request/response models
    ├── services.py             # Canonical service registry
    ├── routes/                 # API route modules
    │   ├── scan.py             # Scan create/poll/stop, background runner
    │   ├── tags.py             # Tag key/value discovery endpoints
    │   └── terraform.py        # Terraform file upload and parsing
    ├── scanner.py              # Scan orchestrator, mixin composition
    ├── scanners/               # Per-service scanner modules (mixin classes)
    │   ├── _utils.py           # Shared helpers (ARN parsing, env var conventions)
    │   └── ...                 # 20 service scanners
    ├── scan_jobs.py            # Async job store with progress tracking
    ├── graph_store.py          # networkx graph with thread-safe mutations + caching
    ├── terraform_parser.py     # .tfstate parser with edge inference
    └── hcl_parser.py           # .tf (HCL) file parser

frontend/                       # React + Vite source (compiled into cloudwire/static/)
├── src/
│   ├── pages/CloudWirePage.jsx # Main page
│   ├── components/
│   │   ├── graph/              # GraphCanvas, GraphNode, GraphEdge, Minimap, Legend
│   │   └── layout/             # TopBar, ServiceSidebar, InspectorPanel, TerraformDropZone
│   ├── hooks/                  # useScanPolling, useTagDiscovery, useGraphPipeline, ...
│   ├── lib/
│   │   ├── api.js              # Shared fetch utilities and API prefix
│   │   ├── graph/              # Layout, clustering, analysis, annotations
│   │   ├── serviceVisuals.jsx  # Service icon + color map
│   │   └── awsRegions.js       # AWS region list
│   └── styles/graph.css
├── vite.config.js
└── package.json
```

## Where to make changes

| Area | Where to edit |
|------|--------------|
| Add a new AWS service scanner | `cloudwire/app/scanners/` -- create a mixin class, import in `scanner.py`, add to `service_scanners` dict |
| Change graph layout | `frontend/src/lib/graph/layout.js` |
| Add a new UI component | `frontend/src/components/` |
| Change API routes | `cloudwire/app/routes/` -- scan, tags, or terraform route module |
| Change CLI options | `cloudwire/cli.py` |
| Error handling / AWS client helpers | `cloudwire/app/errors.py`, `cloudwire/app/aws_clients.py` |

## Before opening a PR

- [ ] Run `make lint` and fix any ruff errors
- [ ] Run a scan against a real (or mocked) AWS account and confirm the graph renders
- [ ] Make sure `make build` completes without errors
- [ ] Keep PRs focused -- one feature or fix per PR
- [ ] Update `CHANGELOG.md` if your change is user-facing

## Code style

- **Python:** standard library imports first, then third-party, then local. Run `make lint` before opening a PR — ruff is configured in `pyproject.toml` (line-length 120, E/F/I/UP rules).
- **JavaScript:** match the style of the surrounding file. No linter enforced yet.

## Good first issues

Look for issues labeled [`good first issue`](https://github.com/Himanshu-370/cloudwire/labels/good%20first%20issue) -- these are scoped, well-documented, and ideal for first-time contributors.

## Questions?

Open a [discussion](https://github.com/Himanshu-370/cloudwire/discussions) or comment on the issue you're working on.
