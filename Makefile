# ==============================================================================
# Enterprise Multimodal Intelligence System - Makefile
# ==============================================================================

.PHONY: help install setup-env start start-api start-ui stop restart status \
        health check-ready test test-cov lint format \
        docker-up docker-down docker-logs docker-ps locust clean

# Python virtual environment binary path
VENV_BIN ?= .venv/bin
PYTHON := $(VENV_BIN)/python
UVICORN := $(VENV_BIN)/uvicorn
STREAMLIT := $(VENV_BIN)/streamlit
PYTEST := $(VENV_BIN)/pytest
RUFF := $(VENV_BIN)/ruff

# Default target
.DEFAULT_GOAL := help

# Colors for terminal output
BLUE  := \033[36m
GREEN := \033[32m
RED   := \033[31m
RESET := \033[0m

## -----------------------------------------------------------------------------
## Help Commands
## -----------------------------------------------------------------------------

help: ## Display available commands with descriptions
	@echo ""
	@echo "$(BLUE)Enterprise Agentic RAG System - Management Commands:$(RESET)"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  $(GREEN)%-18s$(RESET) %s\n", $$1, $$2}'
	@echo ""

## -----------------------------------------------------------------------------
## Environment & Installation
## -----------------------------------------------------------------------------

install: ## Install production and dev dependencies using uv
	@echo "$(BLUE)Installing project dependencies...$(RESET)"
	@uv sync --all-extras

setup-env: ## Copy .env.example to .env if .env does not exist
	@if [ ! -f .env ]; then \
		echo "$(GREEN)Creating .env from .env.example...$(RESET)"; \
		cp .env.example .env; \
	else \
		echo "$(BLUE).env file already exists.$(RESET)"; \
	fi

## -----------------------------------------------------------------------------
## Local Application Lifecycle (Start / Stop / Status)
## -----------------------------------------------------------------------------

start: start-api start-ui ## Start both FastAPI backend and Streamlit UI in background
	@echo "$(GREEN)All local services started.$(RESET)"

start-api: ## Start FastAPI backend server on port 8000
	@echo "$(BLUE)Starting FastAPI backend server on http://localhost:8000...$(RESET)"
	@nohup $(UVICORN) app.main:app --reload --port 8000 > /dev/null 2>&1 &

start-ui: ## Start Streamlit UI on port 8501
	@echo "$(BLUE)Starting Streamlit UI on http://localhost:8501...$(RESET)"
	@nohup $(STREAMLIT) run ui/app.py --server.port 8501 > /dev/null 2>&1 &

stop: ## Stop local FastAPI (port 8000) and Streamlit (port 8501) processes
	@echo "$(RED)Stopping processes on ports 8000 and 8501...$(RESET)"
	@-lsof -ti:8000 | xargs kill -9 2>/dev/null || true
	@-lsof -ti:8501 | xargs kill -9 2>/dev/null || true
	@-pkill -f "uvicorn" 2>/dev/null || true
	@-pkill -f "streamlit" 2>/dev/null || true
	@echo "$(GREEN)Stopped.$(RESET)"

restart: stop start ## Restart local API and UI services

status: ## Check running local service processes
	@echo "$(BLUE)Checking running local processes:$(RESET)"
	@pgrep -fl "uvicorn|streamlit" || echo "No local API/UI processes found."

## -----------------------------------------------------------------------------
## Health Checks & Connectivity
## -----------------------------------------------------------------------------

health: ## Run external connection health checks (Postgres, Redis, Qdrant, LLM Gateway, etc.)
	@echo "$(BLUE)Running system health check...$(RESET)"
	@$(PYTHON) -m app.services.health.connection_checker

check-ready: ## Test API readiness endpoint
	@if curl -sf http://localhost:8000/ready > /dev/null 2>&1; then \
		curl -s http://localhost:8000/ready | $(PYTHON) -m json.tool; \
	else \
		echo "$(RED)API server is not running on http://localhost:8000. Start it with 'make start' or 'make start-api'.$(RESET)"; \
		exit 1; \
	fi

## -----------------------------------------------------------------------------
## Testing & Code Quality
## -----------------------------------------------------------------------------

test: ## Run unit test suite
	@echo "$(BLUE)Running unit tests...$(RESET)"
	@$(PYTEST) tests/

test-cov: ## Run unit tests with coverage report
	@echo "$(BLUE)Running test coverage...$(RESET)"
	@$(PYTEST) --cov=app tests/

lint: ## Run Ruff linter checks
	@echo "$(BLUE)Checking code style with Ruff...$(RESET)"
	@$(RUFF) check .

format: ## Format code with Ruff
	@echo "$(BLUE)Formatting code with Ruff...$(RESET)"
	@$(RUFF) format .
	@$(RUFF) check --fix .

## -----------------------------------------------------------------------------
## Docker Orchestration
## -----------------------------------------------------------------------------

docker-up: ## Build and start Docker services in background
	@echo "$(BLUE)Starting Docker container stack...$(RESET)"
	@docker-compose up -d --build

docker-down: ## Stop and remove Docker containers
	@echo "$(RED)Stopping Docker container stack...$(RESET)"
	@docker-compose down

docker-logs: ## Tail Docker container logs
	@docker-compose logs -f

docker-ps: ## View status of Docker containers
	@docker-compose ps

## -----------------------------------------------------------------------------
## Load Testing & Utilities
## -----------------------------------------------------------------------------

locust: ## Run Locust load testing interface
	@echo "$(BLUE)Starting Locust load testing UI...$(RESET)"
	@$(VENV_BIN)/locust -f scripts/locustfile.py

clean: ## Remove temporary python cache, build, test, and coverage artifacts
	@echo "$(BLUE)Cleaning temporary files...$(RESET)"
	@find . -type d -name "__pycache__" -exec rm -rf {} +
	@find . -type d -name ".pytest_cache" -exec rm -rf {} +
	@find . -type d -name ".ruff_cache" -exec rm -rf {} +
	@find . -type d -name ".deepeval" -exec rm -rf {} +
	@find . -type d -name "*.egg-info" -exec rm -rf {} +
	@rm -rf .coverage htmlcov dist build
	@echo "$(GREEN)Clean complete.$(RESET)"
