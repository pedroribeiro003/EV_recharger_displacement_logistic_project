.DEFAULT_GOAL := help

.PHONY: help install install-dev lint format typecheck test test-unit test-integration \
        migrate migrate-new ingest-all bootstrap clean deploy

# ── Help ───────────────────────────────────────────────────────────────────────
help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}' | sort

# ── Dependencies ───────────────────────────────────────────────────────────────
install: ## Install production dependencies
	pip install -e .

install-dev: ## Install all dependencies (prod + dev) and pre-commit hooks
	pip install -e ".[dev]"
	pre-commit install

# ── Code Quality ───────────────────────────────────────────────────────────────
lint: ## Run ruff linter
	ruff check .

format: ## Auto-fix style and imports with ruff
	ruff format .
	ruff check --fix .

typecheck: ## Run mypy static type checker
	mypy .

# ── Tests ──────────────────────────────────────────────────────────────────────
test: ## Run all tests
	pytest

test-unit: ## Run unit tests only (no DB required)
	pytest tests/unit -v

test-integration: ## Run integration tests (requires DATABASE_URL set)
	pytest tests/integration -v

coverage: ## Run tests with coverage report
	pytest --cov --cov-report=html
	@echo "Report: htmlcov/index.html"

# ── Database ───────────────────────────────────────────────────────────────────
migrate: ## Apply pending Alembic migrations
	alembic upgrade head

migrate-new: ## Create a new migration (usage: make migrate-new MSG="description")
	alembic revision --autogenerate -m "$(MSG)"

db-status: ## Show current migration state
	alembic current

db-report: ## Generate PDF report and stream to stdout (redirect to local file)
	python scripts/db_report.py

# ── Pipeline ───────────────────────────────────────────────────────────────────
ingest-all: ## Run full ingestion pipeline (all sources)
	python main.py ingest all

bootstrap: ## Check DB state, ingest missing data, start Tupi poll
	python scripts/bootstrap.py

# ── Server Deployment ──────────────────────────────────────────────────────────
setup: ## First-time server provisioning (run as root on Ubuntu server)
	sudo bash scripts/setup.sh

deploy: ## Deploy latest code to server (run as root on Ubuntu server)
	sudo bash scripts/deploy.sh

service-start: ## Start the ev-demand systemd service
	sudo systemctl start ev-demand

service-stop: ## Stop the ev-demand systemd service
	sudo systemctl stop ev-demand

service-restart: ## Restart the ev-demand systemd service
	sudo systemctl restart ev-demand

service-status: ## Show ev-demand service status
	sudo systemctl status ev-demand --no-pager

service-logs: ## Follow ev-demand service logs
	sudo journalctl -u ev-demand -f

# ── Misc ───────────────────────────────────────────────────────────────────────
clean: ## Remove Python cache, test artifacts, and build artifacts
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage dist build *.egg-info
