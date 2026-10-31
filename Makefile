# Common tasks, one command each. `make` or `make help` lists them.
#
# Linux / macOS / CI: make is preinstalled. Windows: `winget install ezwinports.make`, or run the
# command shown next to each target by hand (they are plain uv / npm / docker commands).

COMPOSE := docker compose -f deploy/docker-compose.yml

.DEFAULT_GOAL := help
.PHONY: help install dev-backend dev-frontend test lint check dashboard up down logs k8s-dev k8s-prod-preview hooks

help: ## list the targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-18s %s\n", $$1, $$2}'

install: ## install backend + frontend dependencies
	cd backend && uv sync --group test
	cd frontend && npm ci

dev-backend: ## run the API locally with reload (needs Postgres, Qdrant, MinIO, Ollama: see README)
	cd backend && uv run uvicorn app.main:app --reload --port 8000

dev-frontend: ## run the Vite dev server on http://localhost:5173
	cd frontend && npm run dev

test: ## all tests (backend pytest + frontend vitest); no services needed
	cd backend && uv run --no-dev --group test pytest -q
	cd frontend && npm test

lint: ## all linters (ruff, eslint) and the dashboard-is-generated check
	cd backend && uvx ruff check app tests evals migrations
	cd frontend && npm run lint
	python deploy/observability/grafana/build_dashboard.py --check

check: lint test ## what CI runs: lint, then test

dashboard: ## regenerate the Grafana dashboard JSON from build_dashboard.py
	python deploy/observability/grafana/build_dashboard.py

up: ## the whole stack in Docker: app :8080, Grafana :3000, Prometheus :9090
	$(COMPOSE) up -d --build

down: ## stop the Docker stack (data is kept; add -v by hand to delete it)
	$(COMPOSE) down

logs: ## follow backend + worker logs
	$(COMPOSE) logs -f backend worker

k8s-dev: ## apply the dev overlay to the current Kubernetes context
	kubectl apply -k deploy/k8s/overlays/dev

k8s-prod-preview: ## print what the prod overlay would apply (nothing is changed)
	kubectl kustomize deploy/k8s/overlays/prod

hooks: ## install the git pre-commit hooks (.pre-commit-config.yaml)
	uvx pre-commit install
