# Changelog

All notable changes to CloudWire are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/).

## [0.2.5] - 2026-03-17

### Added
- Dependabot configuration for automated Python and npm dependency updates
- Request body size limit middleware (2 MB cap on JSON endpoints)
- API 404 catch-all for unmatched `/api/*` routes (returns JSON instead of SPA HTML)
- Ruff linting configuration in `pyproject.toml`
- `CODE_OF_CONDUCT.md` (Contributor Covenant v2.1)
- `make lint` Makefile target

### Changed
- Version now derived from package metadata via `importlib.metadata` (single source of truth in `pyproject.toml`)
- Dev extras expanded with `ruff` and `mypy`

### Fixed
- Ghost `cloudwire/app/cost/` directory with orphaned `.pyc` files removed
- SPA fallback no longer masks `/api/*` 404 errors with HTML responses

### Removed
- AI-generated `CODEBASE_DEEP_DIVE.md` from repository tracking

## [0.2.4] - 2026-03-14

### Added
- Terraform file upload and parsing (.tfstate, .json, .tf files)
- HCL parser for Terraform configuration files
- Terraform drop zone and file panel UI components
- Rate limiting on terraform parse endpoint
- Concurrent scan job cap (max 8 in-flight)
- Graph serialization caching for improved poll performance
- Shared API utilities (`lib/api.js`) for frontend fetch consolidation

### Changed
- Broke up `main.py` god-object into route modules (`routes/scan.py`, `routes/tags.py`, `routes/terraform.py`), `errors.py`, and `aws_clients.py`
- Split `graphTransforms.js` (965 lines) into `graph/normalize.js`, `layout.js`, `clustering.js`, `analysis.js`, `annotations.js`
- Consolidated triplicated `_service_from_arn` and `_safe_list` into `scanners/_utils.py`
- Makefile now uses portable Python for version bumping (was macOS-only `sed -i ''`)
- Default Python changed from `python3.11` to `python3`

### Fixed
- Thread safety: `_node_attr_index` now protected by dedicated lock
- Thread safety: job field mutations now go through `ScanJobStore.update_services_total()`
- `resourceId` now URL-encoded in frontend API calls (prevents path traversal with ARN-style IDs)
- FastAPI version field now reads from package `__version__` (was hardcoded `0.1.0`)
- Rate limiter uses `deque.popleft()` instead of `list.pop(0)` (O(1) vs O(n))

## [0.2.3] - 2025-12-15

### Added
- Tag-based scanning -- discover and scan resources by AWS tags
- Multi-key tag filter with searchable dropdowns
- Global service tag discovery (CloudFront, Route53, IAM from us-east-1)
- Tag filter seeding for resources not covered by dedicated scanners

### Changed
- Version bump to 0.2.3

## [0.2.2] - 2025-11-20

### Added
- VPC network topology support (VPCs, subnets, SGs, IGWs, NAT GWs, route tables)
- Internet exposure detection (IGW -> route table -> subnet -> open SG -> resource)
- Availability Zone grouping and collapsible VPC containers
- Enhanced dependency checks in CLI startup

### Changed
- Service visuals updated with new icons and color scheme
