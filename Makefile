.PHONY: frontend package build clean install-dev dev help

PYTHON ?= python3.11
NPM    ?= npm

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

frontend: ## Build the React frontend into cloudwire/static/
	cd frontend && $(NPM) run build

package: ## Build the Python wheel and sdist (run `make frontend` first)
	$(PYTHON) -m build

build: frontend package ## Full build: frontend → Python wheel

clean: ## Remove build artifacts
	rm -rf cloudwire/static/assets cloudwire/static/index.html cloudwire/static/vite.svg
	rm -rf dist/ build/ *.egg-info cloudwire.egg-info

install-dev: ## Install the package in editable mode (requires `make frontend` first)
	pip install -e ".[dev]" 2>/dev/null || pip install -e .

dev: ## Run frontend dev server and backend API concurrently (requires tmux or parallel)
	@echo "Starting backend on :8000 and frontend on :5173 ..."
	@$(PYTHON) -m uvicorn cloudwire.app.main:app --reload --port 8000 & \
	cd frontend && $(NPM) run dev; \
	kill %1 2>/dev/null; true
