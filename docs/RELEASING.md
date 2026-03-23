# Release Guide

How to build, test, and publish a new version of cloudwire to PyPI.

---

## Overview

Releases are fully automated. When you merge a PR with a `release` label, GitHub Actions will:

1. Bump the version in `pyproject.toml` and `cloudwire/__init__.py`
2. Commit the version bump and create a git tag
3. Build the frontend and Python wheel
4. Publish to PyPI via trusted publishing
5. Create a GitHub Release with build artifacts

```
merge PR (with release label) → auto version bump → git tag → build → PyPI + GitHub Release
```

No manual version bumping, tagging, or publishing required.

---

## Prerequisites (first-time setup)

### 1. Configure PyPI trusted publishing

cloudwire uses OIDC trusted publishing — no API tokens to store or rotate.

1. Create an account on [pypi.org](https://pypi.org) if you don't have one
2. Go to **Account settings → Publishing**
3. Click **Add a new publisher** and fill in:
   - **PyPI project name:** `cloudwire`
   - **GitHub owner:** your GitHub username or org
   - **Repository name:** `cloudwire`
   - **Workflow filename:** `release.yml`
   - **Environment name:** `pypi`
4. Add a second publisher with the same settings but **Workflow filename:** `publish.yml` (fallback for manual tag releases)
5. Save

### 2. Create the GitHub environment

1. Go to your repo on GitHub → **Settings → Environments**
2. Click **New environment**, name it `pypi`
3. Optionally add protection rules (e.g. require a review before publishing)

That's all — no secrets to add. The OIDC handshake between GitHub Actions and PyPI handles authentication.

### 3. Enable workflow write permissions

1. Go to **Settings → Actions → General**
2. Under **Workflow permissions**, select **Read and write permissions**
3. Check **Allow GitHub Actions to create and approve pull requests**
4. Save

### 4. Create release labels

Go to your repo → **Issues → Labels** and create:

| Label | Description | Suggested color |
|-------|-------------|-----------------|
| `release` | Trigger a patch release on merge | `#0E8A16` (green) |
| `release:minor` | Trigger a minor release on merge | `#1D76DB` (blue) |
| `release:major` | Trigger a major release on merge | `#D93F0B` (red) |

---

## Automated release (recommended)

This is the standard way to release. No manual version bumps, no tagging, no publishing commands.

### Step 1 — Create your PR

Work on a feature branch and open a PR against `main` as usual.

### Step 2 — Add a release label

Before merging, add one of these labels to the PR:

| Label | Effect | Example |
|-------|--------|---------|
| `release` or `release:patch` | Bump patch version | `0.2.6` → `0.2.7` |
| `release:minor` | Bump minor, reset patch | `0.2.6` → `0.3.0` |
| `release:major` | Bump major, reset minor + patch | `0.2.6` → `1.0.0` |

**No label = no release.** The PR merges normally without triggering any release pipeline.

### Step 3 — Merge the PR

Merge as usual (squash, merge commit, or rebase — all work). On merge, **`release.yml`** runs a single job that does everything:

1. Reads the current version from `pyproject.toml`
2. Bumps it based on the label
3. Updates `pyproject.toml` and `cloudwire/__init__.py`
4. Commits: `chore: bump version 0.2.6 → 0.2.7`
5. Creates git tag `v0.2.7` and pushes to `main`
6. Builds the React frontend (`npm ci && npm run build`)
7. Builds the Python wheel (`python -m build`)
8. Verifies static assets are bundled in the wheel
9. Publishes to PyPI via trusted publishing
10. Creates a GitHub Release with auto-generated notes and dist artifacts

If the build or publish fails after the tag was pushed, the tag is automatically cleaned up so the next attempt doesn't collide.

> **Note:** `publish.yml` exists as a fallback for manual tag pushes only. It automatically skips tags created by the automated release to prevent double-publishing.

### Step 4 — Verify

```bash
pip install --upgrade cloudwire
cloudwire --version
```

The GitHub Release will appear at: `https://github.com/Himanshu-370/cloudwire/releases`

### Example walkthrough

Here's a concrete example of releasing a new scanner feature:

```bash
# 1. Work on your feature branch
git checkout -b feat/elasticache-scanner
# ... make changes ...
git add -A && git commit -m "feat: add ElastiCache scanner"
git push -u origin feat/elasticache-scanner

# 2. Open a PR on GitHub
gh pr create --title "feat: add ElastiCache scanner" --body "Adds dedicated ElastiCache scanner"

# 3. Add the release label (this is a new feature, so use minor)
gh pr edit --add-label "release:minor"

# 4. Merge the PR (via GitHub UI or CLI)
gh pr merge --squash

# That's it! GitHub Actions will:
#   - Bump 0.2.6 → 0.3.0
#   - Tag v0.3.0
#   - Publish to PyPI
#   - Create GitHub Release
#
# If this was a bug fix, you'd use the "release" label instead,
# and the version would bump 0.2.6 → 0.2.7
```

---

## Manual release (quick command)

If you need to bypass the automated flow (e.g. CI is down), you can release manually:

```bash
make release V=0.2.7
```

This will:
1. Update the version in `cloudwire/__init__.py` and `pyproject.toml`
2. Clean previous build artifacts
3. Build the React frontend into `cloudwire/static/`
4. Build the Python wheel and sdist
5. Upload to PyPI via `twine`

After the upload, tag and push:

```bash
git add cloudwire/__init__.py pyproject.toml
git commit -m "chore: bump version to 0.2.7"
git tag v0.2.7
git push && git push origin v0.2.7
```

> **Note:** `make release` requires `twine` and `build` (`pip install twine build`) and PyPI credentials configured via `~/.pypirc` or `TWINE_USERNAME`/`TWINE_PASSWORD` env vars.

---

## Version numbering

Follow [Semantic Versioning](https://semver.org/):

```
MAJOR.MINOR.PATCH
```

| Increment | When |
|-----------|------|
| **PATCH** (`0.1.0 → 0.1.1`) | Bug fixes, scanner tweaks, dependency updates with no behaviour change |
| **MINOR** (`0.1.0 → 0.2.0`) | New services, new UI features, new CLI options, backwards-compatible changes |
| **MAJOR** (`0.1.0 → 1.0.0`) | Breaking CLI flags, major API changes, drop Python version support |

Pre-release versions:

```bash
git tag v1.0.0-rc1    # release candidate
git tag v1.0.0-beta1  # beta
```

PyPI accepts these as pre-releases — users only get them if they explicitly `pip install cloudwire==1.0.0rc1` or use `--pre`.

---

## Manual release (without GitHub Actions)

Use this if Actions is unavailable or you need to publish from your machine.

```bash
# 1. Build frontend
cd frontend && npm ci && npm run build && cd ..

# 2. Build the wheel
python -m build

# 3. Verify the wheel contains static assets
python - <<'EOF'
import zipfile
from pathlib import Path
whl = next(Path("dist").glob("*.whl"))
with zipfile.ZipFile(whl) as z:
    names = z.namelist()
assert any("static/index.html" in n for n in names), "index.html missing"
assert any("static/assets" in n and n.endswith(".js") for n in names), "JS bundle missing"
print(f"OK: {whl.name} looks good")
EOF

# 4. Upload
pip install twine
twine upload dist/*
```

Twine will prompt for your PyPI username and password (or API token if you prefer).

---

## Hotfix release

If a critical bug needs to be fixed on an already-released version:

```bash
# Create a hotfix branch from the release tag
git checkout -b hotfix/0.1.1 v0.1.0

# Fix the bug, then bump patch version in pyproject.toml and cloudwire/__init__.py
# ... edit files ...
git add -A
git commit -m "fix: <description>"

# Tag and push — publish.yml will build and publish automatically
git tag v0.1.1
git push origin hotfix/0.1.1 v0.1.1

# Merge fix back to main
git checkout main
git merge hotfix/0.1.1
git push
```

The tag push triggers `publish.yml` (the manual tag fallback), which handles the build, PyPI publish, and GitHub Release creation automatically via OIDC trusted publishing — no API tokens or `twine` needed.

---

## What's in the wheel

The published wheel (`cloudwire-X.Y.Z-py3-none-any.whl`) contains:

```
cloudwire/
├── __init__.py
├── cli.py
├── app/
│   ├── main.py
│   ├── errors.py
│   ├── aws_clients.py
│   ├── models.py
│   ├── services.py
│   ├── scanner.py
│   ├── scan_jobs.py
│   ├── graph_store.py
│   ├── terraform_parser.py
│   ├── hcl_parser.py
│   ├── routes/
│   │   ├── scan.py
│   │   ├── tags.py
│   │   └── terraform.py
│   └── scanners/
│       ├── _utils.py
│       └── ...
└── static/
    ├── index.html
    └── assets/
        ├── index-<hash>.js
        └── index-<hash>.css
```

The wheel is `py3-none-any` — pure Python, platform-independent. Users on macOS, Linux, and Windows all install the same file.

---

## Makefile reference

```bash
make release V=X.Y.Z  # bump version, build everything, upload to PyPI
make build            # full build: npm run build + python -m build
make frontend         # frontend only: npm run build → cloudwire/static/
make package          # Python wheel only (run make frontend first)
make clean            # remove cloudwire/static/, dist/, build/, *.egg-info/
make install-dev      # pip install -e . (editable install for local development)
make dev              # start backend (:8000) and frontend dev server (:5173)
```

---

## Checklist before every release

### Automated release (PR with label)

- [ ] `make build` completes without errors locally
- [ ] Did a quick scan against a real or test AWS account and the graph renders
- [ ] Updated `CHANGELOG.md` with user-facing changes
- [ ] PR has the correct release label (`release`, `release:minor`, or `release:major`)
- [ ] PR is merged to `main`
- [ ] GitHub Actions `Auto Release` workflow is green
- [ ] `pip install --upgrade cloudwire` on a clean machine shows the new version

### Manual release

- [ ] Version bumped in `cloudwire/__init__.py` and `pyproject.toml`
- [ ] Both version strings match the tag you're about to push
- [ ] `make build` completes without errors locally
- [ ] Installed the wheel locally and confirmed `cloudwire --version` is correct
- [ ] Did a quick scan against a real or test AWS account and the graph renders
- [ ] Committed and pushed all changes to `main`
- [ ] Tag pushed: `git push origin vX.Y.Z`
- [ ] GitHub Actions pipeline is green
- [ ] `pip install --upgrade cloudwire` on a clean machine shows the new version
